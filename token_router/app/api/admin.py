from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from token_router.app.config import AppConfig
from token_router.app.router.quota import quota_date_for
from token_router.app.router.selector import NoAvailableModelError, RouteSelector
from token_router.app.usage import UsageManager


router = APIRouter(prefix="/admin")


def _get_config(request: Request) -> AppConfig:
    config = request.app.state.config
    if config is None:
        raise HTTPException(status_code=500, detail="router config is not loaded")
    return config


def _quota_date(request: Request, config: AppConfig) -> str:
    return quota_date_for(
        request.app.state.now_fn(),
        config.refresh.timezone,
        config.refresh.daily_reset_hour,
    )


@router.get("/models")
def list_models(request: Request) -> list[dict[str, Any]]:
    config = _get_config(request)
    usage_manager: UsageManager = request.app.state.usage_manager
    selector = RouteSelector(config, usage_manager)
    quota_date = _quota_date(request, config)
    return [route.to_dict() for route in selector.list_status(quota_date)]


@router.post("/route/preview")
def route_preview(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    config = _get_config(request)
    usage_manager: UsageManager = request.app.state.usage_manager
    selector = RouteSelector(config, usage_manager)
    quota_date = _quota_date(request, config)

    try:
        selected = selector.select(
            model=payload.get("model", "auto"),
            router=payload.get("router", {}),
            quota_date=quota_date,
        )
    except NoAvailableModelError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return {
        "selected": selected.to_dict(),
        "reason": "selected by level, stage, priority, and usage ratio",
    }
