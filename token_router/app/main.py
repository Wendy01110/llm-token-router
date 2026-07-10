from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from token_router.app.api import admin, chat, health, reports, responses
from token_router.app.config import AppConfig, load_config
from token_router.app.daily_eval import start_daily_eval_scheduler
from token_router.app.database import init_db
from token_router.app.providers.openai_compatible import OpenAICompatibleProvider
from token_router.app.router.runtime import RuntimeRouteState
from token_router.app.usage import UsageManager


def create_app(
    config: AppConfig | None = None,
    usage_manager: UsageManager | None = None,
    provider: object | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.config is None:
            config_path = Path(os.environ.get("TOKEN_ROUTER_CONFIG", "config.yaml"))
            if config_path.exists():
                app.state.config = load_config(config_path)
        if app.state.config is not None:
            _bootstrap_calendar_usage(app.state.config, app.state.usage_manager)
        daily_eval_task = None
        if app.state.config is not None:
            daily_eval_task = start_daily_eval_scheduler(
                config=app.state.config,
                usage_manager=app.state.usage_manager,
            )
        app.state.daily_eval_task = daily_eval_task
        try:
            yield
        finally:
            if daily_eval_task is not None:
                daily_eval_task.cancel()
                with suppress(asyncio.CancelledError):
                    await daily_eval_task

    app = FastAPI(title="Local LLM Token Router", lifespan=lifespan)

    if usage_manager is None:
        db_path = Path(os.environ.get("TOKEN_ROUTER_DB", "token_router.sqlite3"))
        init_db(db_path)
        usage_manager = UsageManager(db_path)
    else:
        init_db(usage_manager.db_path)

    app.state.config = config
    app.state.usage_manager = usage_manager
    app.state.provider = provider or OpenAICompatibleProvider()
    app.state.runtime_state = RuntimeRouteState()
    app.state.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    if config is not None:
        _bootstrap_calendar_usage(config, usage_manager)

    app.include_router(health.router)
    app.include_router(reports.router)
    app.include_router(admin.router)
    app.include_router(chat.router)
    app.include_router(responses.router)
    return app


def _bootstrap_calendar_usage(
    config: AppConfig,
    usage_manager: UsageManager,
) -> None:
    routes = {
        (instance.provider, key_config.key_id, instance.name)
        for instance in config.model_instances
        for key_config in instance.iter_key_configs()
        if key_config.quota_refresh_mode == "delayed_calendar_day"
    }
    usage_manager.bootstrap_calendar_usage(config.refresh.timezone, routes)


app = create_app()
