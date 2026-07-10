from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from token_router.app.config import ApiKeyConfig, AppConfig, EndpointConfig
from token_router.app.providers.streaming import (
    SSEUsageTracker,
    apply_stream_usage_policy,
)
from token_router.app.router.runtime import (
    RuntimeRouteState,
    is_retryable_runtime_error,
    route_key,
)
from token_router.app.router.selector import NoAvailableModelError, RouteSelector
from token_router.app.schemas.chat import ChatCompletionRequest
from token_router.app.schemas.router import SelectedRoute
from token_router.app.usage import UsageManager


router = APIRouter(prefix="/v1")


def _get_config(request: Request) -> AppConfig:
    config = request.app.state.config
    if config is None:
        raise HTTPException(status_code=500, detail="router config is not loaded")
    return config


def _find_api_key(endpoint_config: EndpointConfig, key_id: str) -> ApiKeyConfig:
    for api_key in endpoint_config.keys:
        if api_key.id == key_id:
            return api_key
    raise HTTPException(status_code=500, detail="selected API key is not configured")


@router.post("/chat/completions")
async def chat_completions(
    request_payload: ChatCompletionRequest,
    request: Request,
) -> Response:
    config = _get_config(request)
    usage_manager: UsageManager = request.app.state.usage_manager
    runtime_state: RuntimeRouteState = request.app.state.runtime_state
    selector = RouteSelector(config, usage_manager)
    quota_now = request.app.state.now_fn()

    request_id = str(uuid4())
    request_started_at = perf_counter()
    excluded_routes = runtime_state.active_route_keys()
    if request_payload.model_dump(exclude_none=True).get("stream") is True:
        selected, stream, first_chunk = await _open_chat_stream_with_fallback(
            request_payload=request_payload,
            request=request,
            config=config,
            usage_manager=usage_manager,
            runtime_state=runtime_state,
            selector=selector,
            quota_date=quota_now,
            request_id=request_id,
            excluded_routes=excluded_routes,
        )
        return StreamingResponse(
            _stream_and_record_usage(
                stream=stream,
                first_chunk=first_chunk,
                usage_manager=usage_manager,
                runtime_state=runtime_state,
                request_id=request_id,
                selected=selected,
                quota_date=selected.quota_record_date,
                started_at=request_started_at,
            ),
            media_type="text/event-stream",
            headers=_router_headers(request_payload, selected),
        )

    last_error: httpx.HTTPError | None = None
    use_fallback_models = False
    while True:
        try:
            selected, endpoint_config, api_key, outgoing_payload = _prepare_chat_attempt(
                config=config,
                selector=selector,
                request_payload=request_payload,
                quota_date=quota_now,
                excluded_routes=excluded_routes,
                use_fallback_models=use_fallback_models,
            )
        except NoAvailableModelError as exc:
            if last_error is not None:
                _raise_runtime_http_error(last_error)
            raise HTTPException(status_code=429, detail=str(exc)) from exc

        if not runtime_state.try_acquire_concurrency(selected):
            excluded_routes.add(route_key(selected))
            use_fallback_models = True
            continue

        attempt_started_at = perf_counter()
        try:
            upstream_response: dict[str, Any] = (
                await request.app.state.provider.chat_completion(
                    endpoint_config,
                    api_key,
                    outgoing_payload,
                )
            )
        except httpx.HTTPError as exc:
            _log_chat_request(
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error_message=_http_error_message(exc),
                started_at=attempt_started_at,
            )
            if is_retryable_runtime_error(exc):
                runtime_state.mark_cooldown(
                    selected,
                    config.routing.runtime_cooldown_seconds,
                )
                excluded_routes.add(route_key(selected))
                last_error = exc
                use_fallback_models = True
                continue
            _raise_runtime_http_error(exc)
        finally:
            runtime_state.release_concurrency(selected)

        usage = upstream_response.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(
            usage.get("total_tokens") or prompt_tokens + completion_tokens
        )
        usage_manager.record_usage(
            provider=selected.provider,
            key_id=selected.key_id,
            model_name=selected.model_name,
            quota_date=selected.quota_record_date,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            quota_refresh_mode=selected.quota_refresh_mode,
        )
        _log_chat_request(
            usage_manager=usage_manager,
            request_id=request_id,
            selected=selected,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            status="ok",
            error_message=None,
            started_at=request_started_at,
        )

        return JSONResponse(
            content=upstream_response,
            headers=_router_headers(request_payload, selected),
        )


def _prepare_chat_attempt(
    config: AppConfig,
    selector: RouteSelector,
    request_payload: ChatCompletionRequest,
    quota_date: str | datetime,
    excluded_routes: set[tuple[str, str, str, str]],
    use_fallback_models: bool = False,
) -> tuple[SelectedRoute, EndpointConfig, ApiKeyConfig, dict[str, Any]]:
    selected = selector.select(
        model=request_payload.model,
        router=request_payload.router,
        quota_date=quota_date,
        response_format_type=_response_format_type(request_payload),
        excluded_routes=excluded_routes,
        use_fallback_models=use_fallback_models,
    )
    provider_config = config.providers[selected.provider]
    endpoint_config = provider_config.get_endpoint(selected.endpoint)
    api_key = _find_api_key(endpoint_config, selected.key_id)
    outgoing_payload = request_payload.model_dump(exclude_none=True)
    outgoing_payload["model"] = selected.upstream_model_name
    outgoing_payload.pop("router", None)
    _adapt_openai_standard_params(
        outgoing_payload,
        request_payload.router,
        selected,
        endpoint_config,
    )
    _apply_router_thinking_options(outgoing_payload, request_payload.router, selected)
    return selected, endpoint_config, api_key, outgoing_payload


async def _open_chat_stream_with_fallback(
    request_payload: ChatCompletionRequest,
    request: Request,
    config: AppConfig,
    usage_manager: UsageManager,
    runtime_state: RuntimeRouteState,
    selector: RouteSelector,
    quota_date: str | datetime,
    request_id: str,
    excluded_routes: set[tuple[str, str, str, str]],
) -> tuple[SelectedRoute, AsyncIterator[bytes], bytes | None]:
    last_error: httpx.HTTPError | None = None
    use_fallback_models = False
    while True:
        try:
            selected, endpoint_config, api_key, outgoing_payload = _prepare_chat_attempt(
                config=config,
                selector=selector,
                request_payload=request_payload,
                quota_date=quota_date,
                excluded_routes=excluded_routes,
                use_fallback_models=use_fallback_models,
            )
        except NoAvailableModelError as exc:
            if last_error is not None:
                _raise_runtime_http_error(last_error)
            raise HTTPException(status_code=429, detail=str(exc)) from exc

        outgoing_payload = apply_stream_usage_policy(
            outgoing_payload,
            endpoint_config.stream_usage_mode,
        )
        if not runtime_state.try_acquire_concurrency(selected):
            excluded_routes.add(route_key(selected))
            use_fallback_models = True
            continue

        attempt_started_at = perf_counter()
        try:
            stream = request.app.state.provider.chat_completion_stream(
                endpoint_config,
                api_key,
                outgoing_payload,
            )
            first_chunk = await anext(stream)
        except StopAsyncIteration:
            exc = _empty_stream_error()
            _log_chat_request(
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error_message=_http_error_message(exc),
                started_at=attempt_started_at,
            )
            runtime_state.mark_cooldown(
                selected,
                config.routing.runtime_cooldown_seconds,
            )
            excluded_routes.add(route_key(selected))
            last_error = exc
            use_fallback_models = True
            runtime_state.release_concurrency(selected)
            continue
        except httpx.HTTPError as exc:
            _log_chat_request(
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error_message=_http_error_message(exc),
                started_at=attempt_started_at,
            )
            if is_retryable_runtime_error(exc):
                runtime_state.mark_cooldown(
                    selected,
                    config.routing.runtime_cooldown_seconds,
                )
                excluded_routes.add(route_key(selected))
                last_error = exc
                use_fallback_models = True
                runtime_state.release_concurrency(selected)
                continue
            runtime_state.release_concurrency(selected)
            _raise_runtime_http_error(exc)
        except Exception:
            runtime_state.release_concurrency(selected)
            raise

        return selected, stream, first_chunk


def _raise_runtime_http_error(exc: httpx.HTTPError) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=_response_error_text(exc.response) or str(exc),
        ) from exc
    raise HTTPException(status_code=503, detail=str(exc)) from exc


def _empty_stream_error() -> httpx.RequestError:
    return httpx.RemoteProtocolError("upstream stream ended before first chunk")


def _http_error_message(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response_text = _response_error_text(exc.response)
        if response_text:
            return f"{exc}; response: {response_text}"
    return str(exc)


def _response_error_text(response: httpx.Response) -> str:
    try:
        return response.text.strip()
    except httpx.ResponseNotRead:
        return ""


def _log_chat_request(
    usage_manager: UsageManager,
    request_id: str,
    selected: SelectedRoute,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    status: str,
    error_message: str | None,
    started_at: float,
) -> None:
    latency_ms = int((perf_counter() - started_at) * 1000)
    usage_manager.log_request(
        request_id=request_id,
        provider=selected.provider,
        key_id=selected.key_id,
        model_name=selected.model_name,
        level=selected.level,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        status=status,
        error_message=error_message,
        latency_ms=latency_ms,
    )


def _router_headers(
    request_payload: ChatCompletionRequest, selected: SelectedRoute
) -> dict[str, str]:
    if not request_payload.router.get("debug"):
        return {}
    return {
        "X-Router-Provider": selected.provider,
        "X-Router-Endpoint": selected.endpoint,
        "X-Router-Key-Id": selected.key_id,
        "X-Router-Model": selected.model_name,
        "X-Router-Level": str(selected.level),
        "X-Router-Usage-Ratio": str(selected.usage_ratio),
        "X-Router-Stage": str(selected.stage),
    }


def _response_format_type(request_payload: ChatCompletionRequest) -> str | None:
    response_format = request_payload.model_dump(exclude_none=True).get(
        "response_format"
    )
    if not isinstance(response_format, dict):
        return None
    response_format_type = response_format.get("type")
    if isinstance(response_format_type, str):
        return response_format_type
    return None


def _adapt_openai_standard_params(
    outgoing_payload: dict[str, Any],
    router_options: dict[str, Any],
    selected: SelectedRoute,
    endpoint_config: EndpointConfig,
) -> None:
    provider = router_options.get("provider")
    if provider not in (None, "auto"):
        return

    if "max_tokens" in outgoing_payload:
        outgoing_payload.setdefault(
            "max_completion_tokens",
            outgoing_payload["max_tokens"],
        )
        outgoing_payload.pop("max_tokens", None)

    if outgoing_payload.get("stream") is not True:
        outgoing_payload.pop("stream_options", None)
    elif endpoint_config.stream_usage_mode not in {
        "openai_include_usage",
        "ark_include_usage",
    }:
        outgoing_payload.pop("stream_options", None)

    if "reasoning_effort" not in outgoing_payload or "thinking" in router_options:
        return

    reasoning_effort = outgoing_payload["reasoning_effort"]
    if selected.provider == "openrouter":
        reasoning = dict(outgoing_payload.get("reasoning") or {})
        reasoning["effort"] = reasoning_effort
        outgoing_payload["reasoning"] = reasoning
        outgoing_payload.pop("reasoning_effort", None)
        return

    if selected.provider == "xiaomi_mimo":
        outgoing_payload["thinking"] = {
            "type": "disabled" if reasoning_effort == "none" else "enabled"
        }
        outgoing_payload.pop("reasoning_effort", None)
        return

    if selected.provider == "volcengine_ark":
        outgoing_payload["thinking"] = {
            "type": "disabled" if reasoning_effort == "none" else "enabled"
        }
        if not selected.upstream_model_name.startswith("doubao-seed-2-0"):
            outgoing_payload.pop("reasoning_effort", None)


def _apply_router_thinking_options(
    outgoing_payload: dict[str, Any],
    router_options: dict[str, Any],
    selected: SelectedRoute,
) -> None:
    if "thinking" not in router_options:
        return

    thinking_enabled = bool(router_options["thinking"])
    thinking_effort = router_options.get("thinking_effort")

    if selected.provider == "openrouter":
        outgoing_payload.pop("reasoning_effort", None)
        if thinking_enabled:
            if thinking_effort:
                outgoing_payload["reasoning"] = {"effort": thinking_effort}
            else:
                outgoing_payload["reasoning"] = {"enabled": True}
        else:
            outgoing_payload["reasoning"] = {"effort": "none"}
        return

    if selected.provider in {"volcengine_ark", "xiaomi_mimo", "deepseek"}:
        outgoing_payload["thinking"] = {
            "type": "enabled" if thinking_enabled else "disabled"
        }
        outgoing_payload.pop("reasoning_effort", None)
        if (
            thinking_enabled
            and thinking_effort
            and (
                selected.provider == "deepseek"
                or (
                    selected.provider == "volcengine_ark"
                    and selected.upstream_model_name.startswith("doubao-seed-2-0")
                )
            )
        ):
            outgoing_payload["reasoning_effort"] = thinking_effort


async def _stream_and_record_usage(
    stream: AsyncIterator[bytes],
    first_chunk: bytes | None,
    usage_manager: UsageManager,
    runtime_state: RuntimeRouteState,
    request_id: str,
    selected: SelectedRoute,
    quota_date: str,
    started_at: float,
) -> AsyncIterator[bytes]:
    latest_usage: dict[str, Any] | None = None
    usage_tracker = SSEUsageTracker()
    status = "ok"
    error_message = None
    try:
        if first_chunk is not None:
            usage = usage_tracker.feed(first_chunk)
            if usage is not None:
                latest_usage = usage
            yield first_chunk
        async for chunk in stream:
            usage = usage_tracker.feed(chunk)
            if usage is not None:
                latest_usage = usage
            yield chunk
    except httpx.HTTPStatusError as exc:
        status = "error"
        error_message = str(exc)
        raise
    finally:
        try:
            usage = latest_usage or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(
                usage.get("total_tokens") or prompt_tokens + completion_tokens
            )
            usage_manager.record_usage(
                provider=selected.provider,
                key_id=selected.key_id,
                model_name=selected.model_name,
                quota_date=quota_date,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                quota_refresh_mode=selected.quota_refresh_mode,
            )
            latency_ms = int((perf_counter() - started_at) * 1000)
            usage_manager.log_request(
                request_id=request_id,
                provider=selected.provider,
                key_id=selected.key_id,
                model_name=selected.model_name,
                level=selected.level,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                status=status,
                error_message=error_message,
                latency_ms=latency_ms,
            )
        finally:
            runtime_state.release_concurrency(selected)
