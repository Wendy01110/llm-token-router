from __future__ import annotations

import json
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from token_router.app.api.chat import _find_api_key, _get_config, _router_headers
from token_router.app.router.quota import quota_date_for
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
    selector = RouteSelector(config, usage_manager)
    quota_date = quota_date_for(
        request.app.state.now_fn(),
        config.refresh.timezone,
        config.refresh.daily_reset_hour,
    )

    try:
        selected = selector.select(
            model=request_payload.model,
            router=request_payload.router,
            quota_date=quota_date,
            responses_api="native",
        )
    except NoAvailableModelError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    provider_config = config.providers[selected.provider]
    endpoint_config = provider_config.get_endpoint(selected.endpoint)
    api_key = _find_api_key(endpoint_config, selected.key_id)
    outgoing_payload = request_payload.model_dump(exclude_none=True)
    outgoing_payload["model"] = selected.model_name
    outgoing_payload.pop("router", None)

    request_id = str(uuid4())
    started_at = perf_counter()
    debug_payload = ChatCompletionRequest(
        model=request_payload.model,
        messages=[],
        router=request_payload.router,
    )

    if outgoing_payload.get("stream") is True:
        stream = request.app.state.provider.responses_stream(
            endpoint_config,
            api_key,
            outgoing_payload,
        )
        return StreamingResponse(
            _stream_native_responses_and_record_usage(
                stream=stream,
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                quota_date=quota_date,
                started_at=started_at,
            ),
            media_type="text/event-stream",
            headers=_router_headers(debug_payload, selected),
        )

    try:
        upstream_response: dict[str, Any] = await request.app.state.provider.responses(
            endpoint_config,
            api_key,
            outgoing_payload,
        )
    except httpx.HTTPStatusError as exc:
        _log_response_request(
            usage_manager=usage_manager,
            request_id=request_id,
            selected=selected,
            usage={},
            status="error",
            error_message=str(exc),
            started_at=started_at,
        )
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        ) from exc

    usage = _normalise_response_usage(upstream_response.get("usage") or {})
    usage_manager.record_usage(
        provider=selected.provider,
        key_id=selected.key_id,
        model_name=selected.model_name,
        quota_date=quota_date,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
    )
    _log_response_request(
        usage_manager=usage_manager,
        request_id=upstream_response.get("id", request_id),
        selected=selected,
        usage=usage,
        status="ok",
        error_message=None,
        started_at=started_at,
    )

    return JSONResponse(
        content=upstream_response,
        headers=_router_headers(debug_payload, selected),
    )


async def _stream_native_responses_and_record_usage(
    stream: AsyncIterator[bytes],
    usage_manager: UsageManager,
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
        usage = _normalise_response_usage(latest_usage or {})
        usage_manager.record_usage(
            provider=selected.provider,
            key_id=selected.key_id,
            model_name=selected.model_name,
            quota_date=quota_date,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
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
