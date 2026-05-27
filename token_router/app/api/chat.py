from __future__ import annotations

from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from token_router.app.config import ApiKeyConfig, AppConfig
from token_router.app.router.quota import quota_date_for
from token_router.app.router.selector import NoAvailableModelError, RouteSelector
from token_router.app.schemas.chat import ChatCompletionRequest
from token_router.app.usage import UsageManager


router = APIRouter(prefix="/v1")


def _get_config(request: Request) -> AppConfig:
    config = request.app.state.config
    if config is None:
        raise HTTPException(status_code=500, detail="router config is not loaded")
    return config


def _find_api_key(config: AppConfig, provider: str, key_id: str) -> ApiKeyConfig:
    for api_key in config.providers[provider].keys:
        if api_key.id == key_id:
            return api_key
    raise HTTPException(status_code=500, detail="selected API key is not configured")


@router.post("/chat/completions")
async def chat_completions(
    request_payload: ChatCompletionRequest,
    request: Request,
) -> JSONResponse:
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
        )
    except NoAvailableModelError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    provider_config = config.providers[selected.provider]
    api_key = _find_api_key(config, selected.provider, selected.key_id)
    outgoing_payload = request_payload.model_dump(exclude_none=True)
    outgoing_payload["model"] = selected.model_name
    outgoing_payload.pop("router", None)

    request_id = str(uuid4())
    started_at = perf_counter()
    try:
        upstream_response: dict[str, Any] = await request.app.state.provider.chat_completion(
            provider_config,
            api_key,
            outgoing_payload,
        )
    except httpx.HTTPStatusError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        usage_manager.log_request(
            request_id=request_id,
            provider=selected.provider,
            key_id=selected.key_id,
            model_name=selected.model_name,
            level=selected.level,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            status="error",
            error_message=str(exc),
            latency_ms=latency_ms,
        )
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        ) from exc

    usage = upstream_response.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
    if prompt_tokens or completion_tokens:
        usage_manager.record_usage(
            provider=selected.provider,
            key_id=selected.key_id,
            model_name=selected.model_name,
            quota_date=quota_date,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    latency_ms = int((perf_counter() - started_at) * 1000)
    usage_manager.log_request(
        request_id=upstream_response.get("id", request_id),
        provider=selected.provider,
        key_id=selected.key_id,
        model_name=selected.model_name,
        level=selected.level,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        status="ok",
        error_message=None,
        latency_ms=latency_ms,
    )

    headers = {}
    if request_payload.router.get("debug"):
        headers = {
            "X-Router-Provider": selected.provider,
            "X-Router-Key-Id": selected.key_id,
            "X-Router-Model": selected.model_name,
            "X-Router-Level": str(selected.level),
            "X-Router-Usage-Ratio": str(selected.usage_ratio),
            "X-Router-Stage": str(selected.stage),
        }
    return JSONResponse(content=upstream_response, headers=headers)
