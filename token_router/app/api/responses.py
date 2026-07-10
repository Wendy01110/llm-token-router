from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from token_router.app.api.chat import (
    _empty_stream_error,
    _find_api_key,
    _get_config,
    _router_headers,
)
from token_router.app.config import ApiKeyConfig, AppConfig, EndpointConfig
from token_router.app.router.runtime import (
    RuntimeRouteState,
    is_retryable_runtime_error,
    route_key,
)
from token_router.app.router.selector import NoAvailableModelError, RouteSelector
from token_router.app.schemas.chat import ChatCompletionRequest
from token_router.app.schemas.responses import ResponsesRequest
from token_router.app.schemas.router import SelectedRoute
from token_router.app.usage import UsageManager


router = APIRouter(prefix="/v1")


@router.post("/responses")
async def responses(
    request_payload: ResponsesRequest,
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
        selected, stream, first_chunk = await _open_responses_stream_with_fallback(
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
            _stream_native_responses_and_record_usage(
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
            headers=_router_headers(_debug_chat_payload(request_payload), selected),
        )

    last_error: httpx.HTTPError | None = None
    use_fallback_models = False
    while True:
        try:
            selected, endpoint_config, api_key, outgoing_payload = (
                _prepare_responses_attempt(
                    config=config,
                    selector=selector,
                    request_payload=request_payload,
                    quota_date=quota_now,
                    excluded_routes=excluded_routes,
                    use_fallback_models=use_fallback_models,
                )
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
                await request.app.state.provider.responses(
                    endpoint_config,
                    api_key,
                    outgoing_payload,
                )
            )
        except httpx.HTTPError as exc:
            _log_response_request(
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                usage={},
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

        usage = _normalise_response_usage(upstream_response.get("usage") or {})
        usage_manager.record_usage(
            provider=selected.provider,
            key_id=selected.key_id,
            model_name=selected.model_name,
            quota_date=selected.quota_record_date,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            quota_refresh_mode=selected.quota_refresh_mode,
        )
        _log_response_request(
            usage_manager=usage_manager,
            request_id=request_id,
            selected=selected,
            usage=usage,
            status="ok",
            error_message=None,
            started_at=request_started_at,
        )

        return JSONResponse(
            content=upstream_response,
            headers=_router_headers(_debug_chat_payload(request_payload), selected),
        )


def _prepare_responses_attempt(
    config: AppConfig,
    selector: RouteSelector,
    request_payload: ResponsesRequest,
    quota_date: str | datetime,
    excluded_routes: set[tuple[str, str, str, str]],
    use_fallback_models: bool = False,
) -> tuple[SelectedRoute, EndpointConfig, ApiKeyConfig, dict[str, Any]]:
    selected = selector.select(
        model=request_payload.model,
        router=request_payload.router,
        quota_date=quota_date,
        responses_api="native",
        responses_tool_types=_responses_tool_types(request_payload),
        excluded_routes=excluded_routes,
        use_fallback_models=use_fallback_models,
    )
    provider_config = config.providers[selected.provider]
    endpoint_config = provider_config.get_endpoint(selected.endpoint)
    api_key = _find_api_key(endpoint_config, selected.key_id)
    outgoing_payload = request_payload.model_dump(exclude_none=True)
    outgoing_payload["model"] = selected.upstream_model_name
    outgoing_payload.pop("router", None)
    return selected, endpoint_config, api_key, outgoing_payload


async def _open_responses_stream_with_fallback(
    request_payload: ResponsesRequest,
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
            selected, endpoint_config, api_key, outgoing_payload = (
                _prepare_responses_attempt(
                    config=config,
                    selector=selector,
                    request_payload=request_payload,
                    quota_date=quota_date,
                    excluded_routes=excluded_routes,
                    use_fallback_models=use_fallback_models,
                )
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
            stream = request.app.state.provider.responses_stream(
                endpoint_config,
                api_key,
                outgoing_payload,
            )
            first_chunk = await anext(stream)
        except StopAsyncIteration:
            exc = _empty_stream_error()
            _log_response_request(
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                usage={},
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
            _log_response_request(
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                usage={},
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


def _debug_chat_payload(request_payload: ResponsesRequest) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=request_payload.model,
        messages=[],
        router=request_payload.router,
    )


def _responses_tool_types(request_payload: ResponsesRequest) -> set[str]:
    tools = request_payload.model_dump(exclude_none=True).get("tools")
    if not isinstance(tools, list):
        return set()
    return {
        tool["type"]
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("type"), str)
    }


async def _stream_native_responses_and_record_usage(
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
    usage_tracker = ResponsesSSEUsageTracker()
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
    except httpx.HTTPError as exc:
        status = "error"
        error_message = _http_error_message(exc)
        yield _responses_stream_error_event(error_message)
    finally:
        try:
            usage = _normalise_response_usage(latest_usage or {})
            usage_manager.record_usage(
                provider=selected.provider,
                key_id=selected.key_id,
                model_name=selected.model_name,
                quota_date=quota_date,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                quota_refresh_mode=selected.quota_refresh_mode,
            )
            _log_response_request(
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                usage=usage,
                status=status,
                error_message=error_message,
                started_at=started_at,
            )
        finally:
            runtime_state.release_concurrency(selected)


class ResponsesSSEUsageTracker:
    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> dict[str, Any] | None:
        self._buffer += chunk
        latest_usage = None
        while b"\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split(b"\n", 1)
            usage = _extract_usage_from_responses_sse_line(raw_line)
            if usage is not None:
                latest_usage = usage
        return latest_usage


def _extract_usage_from_responses_sse_line(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line.startswith(b"data:"):
        return None
    data = line[5:].strip()
    if not data:
        return None
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None

    response = event.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        return response["usage"]
    if isinstance(event.get("usage"), dict):
        return event["usage"]
    return None


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


def _responses_stream_error_event(message: str) -> bytes:
    payload = {
        "type": "error",
        "error": {
            "type": "upstream_error",
            "code": "upstream_http_error",
            "message": message,
        },
    }
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: error\ndata: {data}\n\n".encode()


def _normalise_response_usage(usage: dict[str, Any]) -> dict[str, int]:
    prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion_tokens = int(
        usage.get("output_tokens") or usage.get("completion_tokens") or 0
    )
    total_tokens = int(
        usage.get("total_tokens") or prompt_tokens + completion_tokens
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _log_response_request(
    usage_manager: UsageManager,
    request_id: str,
    selected: SelectedRoute,
    usage: dict[str, int],
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
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        status=status,
        error_message=error_message,
        latency_ms=latency_ms,
    )
