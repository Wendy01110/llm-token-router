import pytest

from token_router.app.config import (
    ApiKeyConfig,
    AppConfig,
    ModelInstanceConfig,
    ProviderConfig,
    RefreshConfig,
    RoutingConfig,
)
from token_router.app.router.selector import NoAvailableModelError, RouteSelector
from token_router.app.usage import UsageRecord


class FakeUsageManager:
    def __init__(self, records):
        self.records = records

    def get_usage(self, provider, key_id, model_name, quota_date):
        return self.records.get((provider, key_id, model_name), UsageRecord())


def make_config():
    return AppConfig(
        refresh=RefreshConfig(),
        routing=RoutingConfig(),
        providers={
            "test": ProviderConfig(
                type="openai_compatible",
                base_url="https://example.test/v1",
                keys=[
                    ApiKeyConfig(id="k1", value="sk-1"),
                    ApiKeyConfig(id="k2", value="sk-2"),
                ],
            )
        },
        model_instances=[
            ModelInstanceConfig(
                name="model-a",
                provider="test",
                key_id="k1",
                level=1,
                daily_quota=100,
                priority=10,
                groups=["general"],
            ),
            ModelInstanceConfig(
                name="model-a",
                provider="test",
                key_id="k2",
                level=1,
                daily_quota=100,
                priority=10,
                groups=["general"],
            ),
            ModelInstanceConfig(
                name="model-b",
                provider="test",
                key_id="k1",
                level=2,
                daily_quota=100,
                priority=20,
                groups=["general"],
            ),
        ],
    )


def test_selector_prefers_lower_stage_same_level():
    selector = RouteSelector(
        make_config(),
        FakeUsageManager({("test", "k1", "model-a"): UsageRecord(total_tokens=25)}),
    )

    selected = selector.select(
        model="auto", router={"level": 1}, quota_date="2026-05-27"
    )

    assert selected.key_id == "k2"


def test_selector_falls_back_to_lower_level_when_level_exhausted():
    selector = RouteSelector(
        make_config(),
        FakeUsageManager(
            {
                ("test", "k1", "model-a"): UsageRecord(total_tokens=100),
                ("test", "k2", "model-a"): UsageRecord(total_tokens=100),
            }
        ),
    )

    selected = selector.select(
        model="auto", router={"level": 1}, quota_date="2026-05-27"
    )

    assert selected.model_name == "model-b"


def test_selector_raises_when_strict_model_is_exhausted():
    selector = RouteSelector(
        make_config(),
        FakeUsageManager(
            {
                ("test", "k1", "model-a"): UsageRecord(total_tokens=100),
                ("test", "k2", "model-a"): UsageRecord(total_tokens=100),
            }
        ),
    )

    with pytest.raises(NoAvailableModelError):
        selector.select(
            model="model-a",
            router={"level": 1, "strict_model": True},
            quota_date="2026-05-27",
        )
