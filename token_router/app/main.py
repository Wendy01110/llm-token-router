from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI

from token_router.app.api import admin, chat, health
from token_router.app.config import AppConfig, load_config
from token_router.app.database import init_db
from token_router.app.providers.openai_compatible import OpenAICompatibleProvider
from token_router.app.usage import UsageManager


def create_app(
    config: AppConfig | None = None,
    usage_manager: UsageManager | None = None,
    provider: object | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> FastAPI:
    app = FastAPI(title="Local LLM Token Router")

    if config is None:
        config_path = Path(os.environ.get("TOKEN_ROUTER_CONFIG", "config.yaml"))
        if config_path.exists():
            config = load_config(config_path)

    if usage_manager is None:
        db_path = Path(os.environ.get("TOKEN_ROUTER_DB", "token_router.sqlite3"))
        init_db(db_path)
        usage_manager = UsageManager(db_path)

    app.state.config = config
    app.state.usage_manager = usage_manager
    app.state.provider = provider or OpenAICompatibleProvider()
    app.state.now_fn = now_fn or datetime.now

    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(chat.router)
    return app


app = create_app()
