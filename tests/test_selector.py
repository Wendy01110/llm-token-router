from datetime import datetime
from zoneinfo import ZoneInfo

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
        return self.records.get(
            (provider, key_id, model_name, quota_date),
            self.records.get((provider, key_id, model_name), UsageRecord()),
        )

    def get_usage_for_dates(
        self,
        provider,
        key_id,
        model_name,
        quota_dates,
        quota_refresh_mode="shifted_day",
    ):
        records = [
            self.get_usage(provider, key_id, model_name, quota_date)
            for quota_date in quota_dates
        ]
        return UsageRecord(
            prompt_tokens=sum(record.prompt_tokens for record in records),
            completion_tokens=sum(record.completion_tokens for record in records),
            total_tokens=sum(record.total_tokens for record in records),
            request_count=sum(record.request_count for record in records),
        )

    def get_key_request_count(self, provider, key_id, quota_date):
        return sum(
            record.request_count
            for record_key, record in self.records.items()
            if record_key[0] == provider
            and record_key[1] == key_id
            and (len(record_key) == 3 or record_key[3] == quota_date)
        )

    def get_key_request_count_for_dates(
        self,
        provider,
        key_id,
        quota_dates,
        quota_refresh_mode="shifted_day",
    ):
        return sum(
            self.get_key_request_count(provider, key_id, quota_date)
            for quota_date in quota_dates
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


def test_selector_releases_only_yesterday_usage_at_delayed_reset():
    config = make_config()
    config.model_instances[0] = ModelInstanceConfig(
        name="model-a",
        provider="test",
        level=1,
        priority=10,
        keys=[
            {
                "key_id": "k1",
                "daily_quota": 200,
                "quota_refresh_mode": "delayed_calendar_day",
            }
        ],
        groups=["general"],
    )
    config.model_instances = [config.model_instances[0]]
    usage_manager = FakeUsageManager(
        {
            ("test", "k1", "model-a", "2026-05-26"): UsageRecord(
                total_tokens=150
            ),
            ("test", "k1", "model-a", "2026-05-27"): UsageRecord(
                total_tokens=20
            ),
        }
    )
    selector = RouteSelector(config, usage_manager)
    timezone = ZoneInfo("Asia/Shanghai")

    before_release = selector.list_status(
        datetime(2026, 5, 27, 10, 59, tzinfo=timezone)
    )[0]
    after_release = selector.list_status(
        datetime(2026, 5, 27, 11, 0, tzinfo=timezone)
    )[0]

    assert before_release.used_tokens == 170
    assert before_release.usage_ratio == 0.85
    assert after_release.used_tokens == 20
    assert after_release.usage_ratio == 0.1
    assert after_release.quota_record_date == "2026-05-27"


def test_selector_restores_availability_after_delayed_release():
    config = make_config()
    config.model_instances[0] = ModelInstanceConfig(
        name="model-a",
        provider="test",
        level=1,
        keys=[
            {
                "key_id": "k1",
                "daily_quota": 200,
                "quota_refresh_mode": "delayed_calendar_day",
            }
        ],
    )
    config.model_instances = [config.model_instances[0]]
    selector = RouteSelector(
        config,
        FakeUsageManager(
            {
                ("test", "k1", "model-a", "2026-05-26"): UsageRecord(
                    total_tokens=190
                ),
                ("test", "k1", "model-a", "2026-05-27"): UsageRecord(
                    total_tokens=20
                ),
            }
        ),
    )
    timezone = ZoneInfo("Asia/Shanghai")

    before_release = selector.list_status(
        datetime(2026, 5, 27, 10, 59, tzinfo=timezone)
    )[0]
    after_release = selector.list_status(
        datetime(2026, 5, 27, 11, 0, tzinfo=timezone)
    )[0]

    assert before_release.available is False
    assert after_release.available is True


def test_selector_ignores_fallback_models_for_initial_auto_selection():
    config = make_config()
    config.model_instances.append(
        ModelInstanceConfig(
            name="model-c",
            provider="test",
            level=2,
            priority=5,
            keys=[{"key_id": "k1", "daily_quota": 100}],
            groups=["general"],
        )
    )
    selector = RouteSelector(config, FakeUsageManager({}))

    selected = selector.select(
        model="auto",
        router={"level": 1, "fallback_models": ["model-c"]},
        quota_date="2026-05-27",
    )

    assert selected.model_name == "model-a"


def test_selector_uses_fallback_models_order_for_model_fallback():
    config = make_config()
    config.model_instances.append(
        ModelInstanceConfig(
            name="model-c",
            provider="test",
            level=2,
            priority=5,
            keys=[{"key_id": "k1", "daily_quota": 100}],
            groups=["general"],
        )
    )
    selector = RouteSelector(
        config,
        FakeUsageManager(
            {
                ("test", "k1", "model-a"): UsageRecord(total_tokens=100),
                ("test", "k2", "model-a"): UsageRecord(total_tokens=100),
            }
        ),
    )

    selected = selector.select(
        model="model-a",
        router={
            "level": 1,
            "fallback_models": ["model-c", "model-b"],
        },
        quota_date="2026-05-27",
    )

    assert selected.model_name == "model-c"


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


def test_selector_requires_explicit_model_when_configured():
    config = make_config()
    config.model_instances.insert(
        0,
        ModelInstanceConfig(
            name="paid-model",
            provider="test",
            level=1,
            priority=1,
            keys=[{"key_id": "k1", "daily_quota": 100}],
            groups=["general"],
            requires_explicit_model=True,
        ),
    )
    selector = RouteSelector(config, FakeUsageManager({}))

    auto_selected = selector.select(
        model="auto", router={"level": 1}, quota_date="2026-05-27"
    )
    explicit_selected = selector.select(
        model="paid-model", router={"level": 1}, quota_date="2026-05-27"
    )

    assert auto_selected.model_name == "model-a"
    assert explicit_selected.model_name == "paid-model"


def test_selector_keeps_client_model_name_separate_from_upstream_model():
    config = make_config()
    config.model_instances[0] = ModelInstanceConfig(
        name="payg/model-a",
        upstream_model="model-a",
        provider="test",
        level=1,
        priority=10,
        keys=[{"key_id": "k1", "daily_quota": 100}],
        requires_explicit_model=True,
    )
    selector = RouteSelector(config, FakeUsageManager({}))

    selected = selector.select(
        model="payg/model-a",
        router={"level": 1, "strict_model": True, "fallback": False},
        quota_date="2026-05-27",
    )

    assert selected.model_name == "payg/model-a"
    assert selected.upstream_model_name == "model-a"


def test_selector_skips_unsupported_response_format_type():
    config = make_config()
    config.model_instances = [
        ModelInstanceConfig(
            name="json-unsupported-model",
            provider="test",
            level=1,
            priority=10,
            keys=[{"key_id": "k1", "daily_quota": 100}],
            unsupported_response_format_types=["json_object"],
        ),
        ModelInstanceConfig(
            name="json-supported-model",
            provider="test",
            level=2,
            priority=20,
            keys=[{"key_id": "k1", "daily_quota": 100}],
        ),
    ]
    selector = RouteSelector(config, FakeUsageManager({}))

    selected = selector.select(
        model="auto",
        router={"level": 1},
        quota_date="2026-05-27",
        response_format_type="json_object",
    )

    assert selected.model_name == "json-supported-model"


def test_selector_raises_when_strict_model_has_unsupported_response_format_type():
    config = make_config()
    config.model_instances[0] = ModelInstanceConfig(
        name="model-a",
        provider="test",
        level=1,
        priority=10,
        keys=[{"key_id": "k1", "daily_quota": 100}],
        unsupported_response_format_types=["json_object"],
    )
    config.model_instances = [config.model_instances[0], config.model_instances[2]]
    selector = RouteSelector(config, FakeUsageManager({}))

    with pytest.raises(NoAvailableModelError):
        selector.select(
            model="model-a",
            router={"level": 1, "strict_model": True},
            quota_date="2026-05-27",
            response_format_type="json_object",
        )
