from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from token_router.app.config import (
    ApiKeyConfig,
    AppConfig,
    EndpointConfig,
    ModelInstanceConfig,
    ProviderConfig,
    RefreshConfig,
    RoutingConfig,
)
from token_router.app.database import init_db
from token_router.app.usage import UsageManager


@pytest.fixture
def fixed_now():
    return datetime(2026, 5, 27, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def app_config():
    return AppConfig(
        refresh=RefreshConfig(timezone="Asia/Shanghai", daily_reset_hour=11),
        routing=RoutingConfig(
            default_level=1,
            fallback_enabled=True,
            max_fallback_level=5,
        ),
        providers={
            "test": ProviderConfig(
                type="openai_compatible",
                endpoints={
                    "api": EndpointConfig(
                        base_url="https://example.test/v1",
                        keys=[ApiKeyConfig(id="k1", value="sk-1")],
                    )
                },
            )
        },
        model_instances=[
            ModelInstanceConfig(
                name="model-a",
                provider="test",
                endpoint="api",
                level=1,
                priority=10,
                keys=[{"key_id": "k1", "daily_quota": 100}],
                groups=["general"],
            )
        ],
    )


@pytest.fixture
def usage_manager(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    return UsageManager(db_path)
