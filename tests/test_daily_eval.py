from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from token_router.app.config import (
    ApiKeyConfig,
    AppConfig,
    EndpointConfig,
    ModelInstanceConfig,
    ProviderConfig,
    RefreshConfig,
    RoutingConfig,
)
from token_router.app.daily_eval import (
    DailyEvalResult,
    EvalTarget,
    HOTSPOT_QUERIES,
    ModelEvalResult,
    TAVILY_SEARCH_URL,
    build_tavily_headers,
    build_tavily_search_payload,
    expand_eval_targets,
    format_hotspot_context,
    next_daily_eval_run,
    render_daily_eval_home,
    run_model_eval,
    run_model_evals,
    run_daily_eval_scheduler,
    start_daily_eval_scheduler,
    write_daily_eval_report,
)
from token_router.app.database import init_db
from token_router.app.main import create_app
from token_router.app.usage import UsageManager


class FakeProvider:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response or {
            "id": "chatcmpl-test",
            "choices": [{"message": {"content": "国际、经济和 AI 热点摘要。"}}],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        }
        self.error = error
        self.calls = []

    async def chat_completion(self, provider_config, api_key, payload):
        self.calls.append((provider_config, api_key, payload))
        if self.error is not None:
            raise self.error
        return self.response


class ConcurrentProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.active_calls = 0
        self.max_active_calls = 0

    async def chat_completion(self, provider_config, api_key, payload):
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(0.01)
        self.active_calls -= 1
        return await super().chat_completion(provider_config, api_key, payload)


def multi_key_config() -> AppConfig:
    return AppConfig(
        refresh=RefreshConfig(timezone="Asia/Shanghai", daily_reset_hour=11),
        routing=RoutingConfig(
            default_level=1,
            fallback_enabled=True,
            max_fallback_level=5,
        ),
        providers={
            "ark": ProviderConfig(
                type="openai_compatible",
                endpoints={
                    "api": EndpointConfig(
                        base_url="https://ark.example.test/api/v3",
                        keys=[
                            ApiKeyConfig(id="ark-1", value="sk-1"),
                            ApiKeyConfig(id="ark-2", value="sk-2"),
                        ],
                    )
                },
            )
        },
        model_instances=[
            ModelInstanceConfig(
                name="model-a",
                provider="ark",
                endpoint="api",
                level=1,
                keys=[
                    {"key_id": "ark-1", "daily_quota": 1000, "priority": 10},
                    {"key_id": "ark-2", "daily_quota": 2000, "priority": 20},
                    {
                        "key_id": "ark-1",
                        "daily_quota": 1000,
                        "priority": 30,
                        "enabled": False,
                    },
                ],
                groups=["general"],
            ),
            ModelInstanceConfig(
                name="model-disabled",
                provider="ark",
                endpoint="api",
                level=1,
                keys=[{"key_id": "ark-1", "daily_quota": 1000}],
                enabled=False,
            ),
        ],
    )


def test_expand_eval_targets_expands_each_enabled_model_key():
    targets = expand_eval_targets(multi_key_config())

    assert targets == [
        EvalTarget(
            provider="ark",
            endpoint="api",
            key_id="ark-1",
            model_name="model-a",
            level=1,
            daily_quota=1000,
            daily_request_quota=None,
        ),
        EvalTarget(
            provider="ark",
            endpoint="api",
            key_id="ark-2",
            model_name="model-a",
            level=1,
            daily_quota=2000,
            daily_request_quota=None,
        ),
    ]


def test_hotspot_queries_use_daily_news_topics():
    assert [query.topic for query in HOTSPOT_QUERIES] == [
        "general",
        "news",
        "finance",
    ]
    assert [query.query for query in HOTSPOT_QUERIES] == [
        (
            "latest daily general news headlines including major global events, "
            "technology, AI, public policy, health, and society"
        ),
        (
            "latest daily international news covering geopolitics, diplomacy, "
            "conflicts, security, and global affairs"
        ),
        (
            "latest daily finance news covering global economy, markets, central "
            "banks, inflation, commodities, stocks, and AI business"
        ),
    ]


def test_tavily_request_matches_curl_shape():
    assert TAVILY_SEARCH_URL == "https://api.tavily.com/search"
    assert build_tavily_headers("tvly-dev") == {
        "Content-Type": "application/json",
        "Authorization": "Bearer tvly-dev",
    }

    payload = build_tavily_search_payload(HOTSPOT_QUERIES[0])

    assert payload["query"] == HOTSPOT_QUERIES[0].query
    assert payload["topic"] == "general"
    assert payload["include_answer"] == "advanced"
    assert payload["search_depth"] == "advanced"
    assert payload["max_results"] == 10
    assert payload["time_range"] == "day"


def test_format_hotspot_context_includes_every_tavily_result():
    tavily_results = {
        "general": {
            "query": "latest daily general news",
            "answer": "General answer",
            "results": [
                {
                    "title": f"Title {index}",
                    "url": f"https://example.test/{index}",
                    "content": f"Content {index}",
                }
                for index in range(12)
            ],
        }
    }

    context = format_hotspot_context(tavily_results)

    for index in range(12):
        assert f"Title {index}" in context
        assert f"https://example.test/{index}" in context
        assert f"Content {index}" in context


def test_run_model_eval_counts_successful_usage_in_model_instances(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    usage_manager = UsageManager(db_path)
    config = multi_key_config()
    target = expand_eval_targets(config)[0]
    provider = FakeProvider()

    result = asyncio.run(
        run_model_eval(
            config=config,
            usage_manager=usage_manager,
            provider=provider,
            target=target,
            quota_date="2026-06-04",
            hotspot_context="热点资料",
            max_tokens=300,
        )
    )

    usage = usage_manager.get_usage("ark", "ark-1", "model-a", "2026-06-04")
    assert result.status == "ok"
    assert result.total_tokens == 18
    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 7
    assert usage.total_tokens == 18
    assert usage.request_count == 1
    assert provider.calls[0][2]["model"] == "model-a"
    assert provider.calls[0][1].id == "ark-1"


def test_run_model_eval_records_request_when_usage_missing(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    usage_manager = UsageManager(db_path)
    config = multi_key_config()
    target = expand_eval_targets(config)[0]
    provider = FakeProvider(
        response={
            "id": "chatcmpl-no-usage",
            "choices": [{"message": {"content": "摘要"}}],
        }
    )

    result = asyncio.run(
        run_model_eval(
            config=config,
            usage_manager=usage_manager,
            provider=provider,
            target=target,
            quota_date="2026-06-04",
            hotspot_context="热点资料",
            max_tokens=300,
        )
    )

    usage = usage_manager.get_usage("ark", "ark-1", "model-a", "2026-06-04")
    assert result.status == "ok"
    assert result.usage_missing is True
    assert usage.total_tokens == 0
    assert usage.request_count == 1


def test_run_model_evals_executes_targets_concurrently(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    usage_manager = UsageManager(db_path)
    config = multi_key_config()
    targets = expand_eval_targets(config)
    provider = ConcurrentProvider()

    results = asyncio.run(
        run_model_evals(
            config=config,
            usage_manager=usage_manager,
            provider=provider,
            targets=targets,
            quota_date="2026-06-04",
            hotspot_context="热点资料",
            max_tokens=300,
            concurrency=2,
        )
    )

    assert [result.key_id for result in results] == ["ark-1", "ark-2"]
    assert provider.max_active_calls == 2


def test_next_daily_eval_run_uses_next_midnight_in_config_timezone():
    now = datetime(2026, 6, 4, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai"))

    next_run = next_daily_eval_run(now, "Asia/Shanghai")

    assert next_run == datetime(2026, 6, 5, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_daily_eval_scheduler_labels_run_with_scheduled_time_when_sleep_wakes_early(
    tmp_path,
):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    timezone = ZoneInfo("Asia/Shanghai")
    now_values = iter(
        [
            datetime(2026, 6, 17, 23, 59, 0, tzinfo=timezone),
            datetime(2026, 6, 17, 23, 59, 59, 916769, tzinfo=timezone),
        ]
    )
    captured_run_times = []

    async def fake_sleep(delay_seconds):
        assert delay_seconds == 60

    async def fake_run_eval(**kwargs):
        captured_run_times.append(kwargs["now"])
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_daily_eval_scheduler(
                config=multi_key_config(),
                usage_manager=UsageManager(db_path),
                tavily_api_key="tvly-test",
                sleep_fn=fake_sleep,
                now_fn=lambda: next(now_values),
                run_eval=fake_run_eval,
            )
        )

    assert captured_run_times == [
        datetime(2026, 6, 18, 0, 0, tzinfo=timezone)
    ]


def test_start_daily_eval_scheduler_skips_without_tavily_key(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)

    task = start_daily_eval_scheduler(
        config=multi_key_config(),
        usage_manager=UsageManager(db_path),
    )

    assert task is None


def test_create_app_starts_daily_eval_scheduler_on_lifespan(
    tmp_path,
    monkeypatch,
):
    started = []

    def fake_start_daily_eval_scheduler(*, config, usage_manager):
        async def idle():
            await asyncio.Event().wait()

        task = asyncio.create_task(idle())
        started.append((config, usage_manager, task))
        return task

    monkeypatch.setattr(
        "token_router.app.main.start_daily_eval_scheduler",
        fake_start_daily_eval_scheduler,
    )
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    usage_manager = UsageManager(db_path)
    app = create_app(
        multi_key_config(),
        usage_manager,
        now_fn=lambda: datetime(2026, 6, 4, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert len(started) == 1
    assert started[0][0] is app.state.config
    assert started[0][1] is usage_manager
    assert started[0][2].cancelled()


def test_write_daily_eval_report_creates_latest_json_and_markdown(tmp_path):
    result = DailyEvalResult(
        run_date="2026-06-04",
        quota_date="2026-06-04",
        generated_at="2026-06-04T00:00:00+08:00",
        tavily_results={
            "ai": {
                "query": "artificial intelligence latest 24 hours",
                "answer": "AI answer",
                "results": [
                    {"title": "AI News", "url": "https://example.test/ai"}
                ],
            }
        },
        model_results=[
            ModelEvalResult(
                provider="ark",
                endpoint="api",
                key_id="ark-1",
                model_name="model-a",
                level=1,
                status="ok",
                latency_ms=123,
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
                usage_missing=False,
                summary="摘要内容",
                error_message=None,
            ),
            ModelEvalResult(
                provider="agnes",
                endpoint="api",
                key_id="agnes-1",
                model_name="agnes-2.0-flash",
                level=4,
                status="error",
                latency_ms=1099,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                usage_missing=False,
                summary="",
                error_message="upstream 500",
            ),
        ],
    )

    write_daily_eval_report(tmp_path, result)

    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    report = tmp_path / "2026-06-04" / "report.md"
    results_jsonl = tmp_path / "2026-06-04" / "results.jsonl"
    assert latest["run_date"] == "2026-06-04"
    assert latest["ok_count"] == 1
    assert latest["error_count"] == 1
    report_text = report.read_text(encoding="utf-8")
    assert "model-a" in report_text
    assert "agnes-2.0-flash" in report_text
    assert "upstream 500" in report_text
    result_lines = [
        json.loads(line)
        for line in results_jsonl.read_text(encoding="utf-8").splitlines()
    ]
    assert [line["key_id"] for line in result_lines] == ["ark-1", "agnes-1"]


def test_homepage_renders_latest_daily_eval_report():
    result = DailyEvalResult(
        run_date="2026-06-04",
        quota_date="2026-06-04",
        generated_at="2026-06-04T00:00:00+08:00",
        tavily_results={
            "economy": {
                "query": "global economy latest 24 hours",
                "answer": "Economy answer",
                "results": [],
            }
        },
        model_results=[
            ModelEvalResult(
                provider="ark",
                endpoint="api",
                key_id="ark-1",
                model_name="model-a",
                level=1,
                status="ok",
                latency_ms=123,
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
                usage_missing=False,
                summary="模型摘要",
                error_message=None,
            )
        ],
    )

    html = render_daily_eval_home(result=result, history=["2026-06-04"])

    assert "每日模型质量评测" in html
    assert "model-a" in html
    assert "Economy answer" in html
    assert "/admin/usage" in html


def test_root_page_reads_report_dir_from_environment(tmp_path, monkeypatch):
    result = DailyEvalResult(
        run_date="2026-06-04",
        quota_date="2026-06-04",
        generated_at="2026-06-04T00:00:00+08:00",
        tavily_results={},
        model_results=[],
    )
    write_daily_eval_report(tmp_path, result)
    monkeypatch.setenv("TOKEN_ROUTER_REPORTS_DIR", str(tmp_path))
    app = create_app(
        multi_key_config(),
        UsageManager(tmp_path / "usage.sqlite3"),
        now_fn=lambda: datetime(2026, 6, 4, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "2026-06-04" in response.text
