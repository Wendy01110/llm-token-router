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

    def get_key_request_count(self, provider, key_id, quota_date):
        return sum(
            record.request_count
            for (record_provider, record_key_id, _), record in self.records.items()
            if record_provider == provider and record_key_id == key_id
        )


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
                level=1,
                priority=10,
                keys=[{"key_id": "k1", "daily_quota": 100}],
                groups=["general"],
            ),
            ModelInstanceConfig(
                name="model-a",
                provider="test",
                level=1,
                priority=10,
                keys=[{"key_id": "k2", "daily_quota": 100}],
                groups=["general"],
            ),
            ModelInstanceConfig(
                name="model-b",
                provider="test",
                level=2,
                priority=20,
                keys=[{"key_id": "k1", "daily_quota": 100}],
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


def test_selector_falls_back_when_request_quota_is_exhausted():
    config = make_config()
    config.model_instances[0] = ModelInstanceConfig(
        name="model-a",
        provider="test",
        level=1,
        priority=10,
        keys=[{"key_id": "k1", "daily_quota": 1000, "daily_request_quota": 2}],
        groups=["general"],
    )
    config.model_instances = [config.model_instances[0], config.model_instances[2]]
    selector = RouteSelector(
        config,
        FakeUsageManager(
            {("test", "k1", "model-a"): UsageRecord(total_tokens=10, request_count=2)}
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


def test_selector_expands_multiple_keys_from_one_model_instance():
    config = make_config()
    config.model_instances[0] = ModelInstanceConfig(
        name="model-a",
        provider="test",
        level=1,
        priority=10,
        keys=[
            {"key_id": "k1", "daily_quota": 100, "priority": 30},
            {"key_id": "k2", "daily_quota": 200, "priority": 20},
        ],
        groups=["general"],
    )
    config.model_instances = [config.model_instances[0], config.model_instances[2]]
    selector = RouteSelector(
        config,
        FakeUsageManager({("test", "k1", "model-a"): UsageRecord(total_tokens=25)}),
    )

    selected = selector.select(
        model="auto", router={"level": 1}, quota_date="2026-05-27"
    )

    assert selected.key_id == "k2"
    assert selected.daily_quota == 200
    assert selected.daily_request_quota is None
    assert selected.priority == 20


def test_selector_skips_excluded_runtime_route():
    selector = RouteSelector(make_config(), FakeUsageManager({}))

    selected = selector.select(
        model="auto",
        router={"level": 1},
        quota_date="2026-05-27",
        excluded_routes={("test", "default", "k1", "model-a")},
    )

    assert selected.key_id == "k2"
