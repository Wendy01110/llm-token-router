# Local Router MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI-based LLM token router with YAML configuration, SQLite usage tracking, OpenAI-compatible forwarding, and basic admin endpoints.

**Architecture:** Keep configuration as startup-loaded YAML and runtime state in SQLite. Route selection is a pure, testable service that attaches usage data to configured model instances, filters candidates, and sorts by level, stage, priority, and usage ratio. FastAPI endpoints remain thin wrappers around config, usage, selector, and provider services.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, PyYAML, httpx, SQLite via stdlib `sqlite3`, pytest, uvicorn.

---

## File Structure

- `pyproject.toml`: package metadata, dependencies, pytest config.
- `README.md`: local setup and API examples.
- `config.example.yaml`: example local provider/model config.
- `token_router/__init__.py`: package marker.
- `token_router/app/__init__.py`: app package marker.
- `token_router/app/config.py`: Pydantic config models and YAML/env loading.
- `token_router/app/database.py`: SQLite schema initialization and connection helper.
- `token_router/app/schemas/chat.py`: chat request/response-adjacent schemas.
- `token_router/app/schemas/router.py`: router options and selected route schemas.
- `token_router/app/router/quota.py`: quota date, usage ratio, and stage helpers.
- `token_router/app/router/selector.py`: candidate filtering and selection.
- `token_router/app/usage.py`: usage and request log persistence.
- `token_router/app/providers/openai_compatible.py`: httpx forwarding to OpenAI-compatible APIs.
- `token_router/app/api/health.py`: health endpoint.
- `token_router/app/api/admin.py`: model status and route preview endpoints.
- `token_router/app/api/chat.py`: chat completions endpoint.
- `token_router/app/main.py`: app factory and default app.
- `tests/`: focused unit and API tests.

## Task 1: Project Package And Config Loader

**Files:**
- Create: `pyproject.toml`
- Create: `config.example.yaml`
- Create: `token_router/__init__.py`
- Create: `token_router/app/__init__.py`
- Create: `token_router/app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config loader tests**

```python
# tests/test_config.py
from pathlib import Path

from token_router.app.config import load_config


def test_load_config_resolves_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ROUTER_KEY", "sk-test")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
refresh:
  timezone: Asia/Shanghai
  daily_reset_hour: 11
routing:
  default_level: 1
  fallback_enabled: true
  max_fallback_level: 5
providers:
  test:
    type: openai_compatible
    base_url: https://example.test/v1
    keys:
      - id: test_1
        value: ${TEST_ROUTER_KEY}
model_instances:
  - name: test-model
    provider: test
    key_id: test_1
    level: 1
    daily_quota: 1000
    priority: 10
    groups: [general]
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.providers["test"].keys[0].value == "sk-test"
    assert config.model_instances[0].provider == "test"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_config.py -v`

Expected: FAIL because `token_router.app.config` does not exist.

- [ ] **Step 3: Add packaging and dependencies**

Create `pyproject.toml` with:

```toml
[project]
name = "llm-token-router"
version = "0.1.0"
description = "Local LLM token router"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "httpx>=0.27",
    "pydantic>=2.7",
    "PyYAML>=6.0",
    "uvicorn[standard]>=0.30",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 4: Implement config loading**

Implement these public objects in `token_router/app/config.py`:

```python
class RefreshConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    daily_reset_hour: int = Field(default=11, ge=0, le=23)


class RoutingConfig(BaseModel):
    default_level: int = Field(default=1, ge=1)
    fallback_enabled: bool = True
    max_fallback_level: int = Field(default=5, ge=1)


class ApiKeyConfig(BaseModel):
    id: str
    value: str


class ProviderConfig(BaseModel):
    type: str = "openai_compatible"
    base_url: str
    keys: list[ApiKeyConfig]


class ModelInstanceConfig(BaseModel):
    name: str
    provider: str
    key_id: str
    level: int = Field(ge=1)
    daily_quota: int = Field(ge=1)
    priority: int = Field(default=100, ge=1)
    groups: list[str] = Field(default_factory=list)
    enabled: bool = True


class AppConfig(BaseModel):
    refresh: RefreshConfig = Field(default_factory=RefreshConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    providers: dict[str, ProviderConfig]
    model_instances: list[ModelInstanceConfig]


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    raw_text = Path(path).read_text(encoding="utf-8")
    resolved_text = resolve_env_refs(raw_text)
    data = yaml.safe_load(resolved_text)
    config = AppConfig.model_validate(data)
    validate_model_references(config)
    return config
```

Validation rules:

- `daily_reset_hour` is between 0 and 23.
- `level`, `daily_quota`, and `priority` are positive integers.
- every model instance references an existing provider and key id.
- `${ENV_NAME}` values are resolved with `os.environ["ENV_NAME"]`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_config.py -v`

Expected: PASS.

- [ ] **Step 6: Add example config**

Create `config.example.yaml` with one `dashscope`, one `deepseek`, and one `local_ollama` example provider. Use environment variable references for real API keys.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml config.example.yaml token_router tests/test_config.py
git commit -m "Add config loader"
```

## Task 2: Quota Helpers

**Files:**
- Create: `token_router/app/router/__init__.py`
- Create: `token_router/app/router/quota.py`
- Test: `tests/test_quota.py`

- [ ] **Step 1: Write failing tests for quota date and stage**

```python
# tests/test_quota.py
from datetime import datetime
from zoneinfo import ZoneInfo

from token_router.app.router.quota import quota_date_for, stage_for_usage


def test_quota_date_uses_reset_hour_boundary():
    tz = ZoneInfo("Asia/Shanghai")

    before_reset = datetime(2026, 5, 27, 10, 59, tzinfo=tz)
    after_reset = datetime(2026, 5, 27, 11, 0, tzinfo=tz)

    assert quota_date_for(before_reset, "Asia/Shanghai", 11) == "2026-05-26"
    assert quota_date_for(after_reset, "Asia/Shanghai", 11) == "2026-05-27"


def test_stage_for_usage_uses_25_percent_buckets():
    assert stage_for_usage(0, 100) == 1
    assert stage_for_usage(24, 100) == 1
    assert stage_for_usage(25, 100) == 2
    assert stage_for_usage(50, 100) == 3
    assert stage_for_usage(75, 100) == 4
    assert stage_for_usage(100, 100) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_quota.py -v`

Expected: FAIL because `token_router.app.router.quota` does not exist.

- [ ] **Step 3: Implement quota helpers**

Implement:

```python
def quota_date_for(now: datetime, timezone_name: str, reset_hour: int) -> str:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    quota_day = local_now.date()
    if local_now.hour < reset_hour:
        quota_day = quota_day - timedelta(days=1)
    return quota_day.isoformat()


def usage_ratio(used_tokens: int, daily_quota: int) -> float:
    return used_tokens / daily_quota


def stage_for_usage(used_tokens: int, daily_quota: int) -> int | None:
    ratio = usage_ratio(used_tokens, daily_quota)
    if ratio >= 1.0:
        return None
    if ratio >= 0.75:
        return 4
    if ratio >= 0.50:
        return 3
    if ratio >= 0.25:
        return 2
    return 1


def is_exhausted(used_tokens: int, daily_quota: int) -> bool:
    return used_tokens >= daily_quota
```

Return `None` from `stage_for_usage` when exhausted.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_quota.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_router/app/router tests/test_quota.py
git commit -m "Add quota helpers"
```

## Task 3: SQLite Usage Manager

**Files:**
- Create: `token_router/app/database.py`
- Create: `token_router/app/usage.py`
- Test: `tests/test_usage.py`

- [ ] **Step 1: Write failing usage manager tests**

```python
# tests/test_usage.py
from token_router.app.database import init_db
from token_router.app.usage import UsageManager


def test_usage_manager_records_and_reads_daily_usage(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    init_db(db_path)
    manager = UsageManager(db_path)

    manager.record_usage(
        provider="test",
        key_id="key1",
        model_name="model-a",
        quota_date="2026-05-27",
        prompt_tokens=12,
        completion_tokens=8,
    )

    usage = manager.get_usage("test", "key1", "model-a", "2026-05-27")

    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 8
    assert usage.total_tokens == 20
    assert usage.request_count == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_usage.py -v`

Expected: FAIL because database and usage modules do not exist.

- [ ] **Step 3: Implement SQLite schema and usage manager**

Create tables `model_usage_daily` and `request_logs` in `init_db`. Implement this public API:

```python
@dataclass(frozen=True)
class UsageRecord:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0


class UsageManager:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def get_usage(self, provider: str, key_id: str, model_name: str, quota_date: str) -> UsageRecord:
        """SELECT one row from model_usage_daily; return UsageRecord() when absent."""

    def record_usage(self, provider: str, key_id: str, model_name: str, quota_date: str, prompt_tokens: int, completion_tokens: int) -> None:
        """INSERT row with totals or UPDATE existing row by adding token counts and incrementing request_count."""

    def log_request(self, request_id: str, provider: str | None, key_id: str | None, model_name: str | None, level: int | None, prompt_tokens: int, completion_tokens: int, total_tokens: int, status: str, error_message: str | None, latency_ms: int | None) -> None:
        """INSERT one request_logs row."""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_usage.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_router/app/database.py token_router/app/usage.py tests/test_usage.py
git commit -m "Add SQLite usage tracking"
```

## Task 4: Route Selector

**Files:**
- Create: `token_router/app/schemas/__init__.py`
- Create: `token_router/app/schemas/router.py`
- Create: `token_router/app/router/selector.py`
- Test: `tests/test_selector.py`

- [ ] **Step 1: Write failing selector tests**

```python
# tests/test_selector.py
import pytest

from token_router.app.config import AppConfig, ModelInstanceConfig, ProviderConfig, ApiKeyConfig, RefreshConfig, RoutingConfig
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
                keys=[ApiKeyConfig(id="k1", value="sk-1"), ApiKeyConfig(id="k2", value="sk-2")],
            )
        },
        model_instances=[
            ModelInstanceConfig(name="model-a", provider="test", key_id="k1", level=1, daily_quota=100, priority=10, groups=["general"]),
            ModelInstanceConfig(name="model-a", provider="test", key_id="k2", level=1, daily_quota=100, priority=10, groups=["general"]),
            ModelInstanceConfig(name="model-b", provider="test", key_id="k1", level=2, daily_quota=100, priority=20, groups=["general"]),
        ],
    )


def test_selector_prefers_lower_stage_same_level():
    selector = RouteSelector(
        make_config(),
        FakeUsageManager({("test", "k1", "model-a"): UsageRecord(total_tokens=25)}),
        now_fn=lambda: None,
    )

    selected = selector.select(model="auto", router={"level": 1}, quota_date="2026-05-27")

    assert selected.key_id == "k2"


def test_selector_falls_back_to_lower_level_when_level_exhausted():
    selector = RouteSelector(
        make_config(),
        FakeUsageManager({
            ("test", "k1", "model-a"): UsageRecord(total_tokens=100),
            ("test", "k2", "model-a"): UsageRecord(total_tokens=100),
        }),
        now_fn=lambda: None,
    )

    selected = selector.select(model="auto", router={"level": 1}, quota_date="2026-05-27")

    assert selected.model_name == "model-b"


def test_selector_raises_when_strict_model_is_exhausted():
    selector = RouteSelector(
        make_config(),
        FakeUsageManager({
            ("test", "k1", "model-a"): UsageRecord(total_tokens=100),
            ("test", "k2", "model-a"): UsageRecord(total_tokens=100),
        }),
        now_fn=lambda: None,
    )

    with pytest.raises(NoAvailableModelError):
        selector.select(model="model-a", router={"level": 1, "strict_model": True}, quota_date="2026-05-27")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_selector.py -v`

Expected: FAIL because selector module does not exist.

- [ ] **Step 3: Implement router schemas and selector**

Implement:

```python
@dataclass(frozen=True)
class SelectedRoute:
    provider: str
    key_id: str
    model_name: str
    level: int
    daily_quota: int
    used_tokens: int
    usage_ratio: float
    stage: int
    priority: int


class NoAvailableModelError(Exception):
    pass


class RouteSelector:
    def __init__(self, config: AppConfig, usage_manager: UsageManager, now_fn: Callable[[], datetime] | None = None):
        self.config = config
        self.usage_manager = usage_manager
        self.now_fn = now_fn or datetime.now

    def select(self, model: str, router: Mapping[str, Any] | None, quota_date: str) -> SelectedRoute:
        """Build candidate levels, apply filters, attach usage, drop exhausted instances, sort, and return the first route."""

    def list_status(self, quota_date: str) -> list[SelectedRoute]:
        """Return all configured model instances with attached usage, including exhausted instances for admin display."""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_selector.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_router/app/schemas token_router/app/router/selector.py tests/test_selector.py
git commit -m "Add route selector"
```

## Task 5: Admin And Health APIs

**Files:**
- Create: `token_router/app/api/__init__.py`
- Create: `token_router/app/api/health.py`
- Create: `token_router/app/api/admin.py`
- Create: `token_router/app/main.py`
- Test: `tests/test_admin_api.py`

- [ ] **Step 1: Write failing API tests**

```python
# tests/test_admin_api.py
from fastapi.testclient import TestClient

from token_router.app.main import create_app


def test_health_endpoint_returns_ok(app_config, usage_manager):
    app = create_app(app_config, usage_manager)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_route_preview_returns_selected_route(app_config, usage_manager):
    app = create_app(app_config, usage_manager)
    client = TestClient(app)

    response = client.post("/admin/route/preview", json={"model": "auto", "router": {"level": 1}})

    assert response.status_code == 200
    assert response.json()["selected"]["level"] == 1
```

Add reusable `app_config` and `usage_manager` fixtures in `tests/conftest.py`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_admin_api.py -v`

Expected: FAIL because API modules and fixtures do not exist.

- [ ] **Step 3: Implement app factory and admin endpoints**

Implement:

```python
def create_app(
    config: AppConfig | None = None,
    usage_manager: UsageManager | None = None,
    provider: OpenAICompatibleProvider | None = None,
) -> FastAPI:
    app = FastAPI(title="Local LLM Token Router")
    app.state.config = config or load_config()
    app.state.usage_manager = usage_manager or UsageManager("token_router.sqlite3")
    app.state.provider = provider or OpenAICompatibleProvider()
    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(chat_router)
    return app
```

Register:

- `GET /health`
- `GET /admin/models`
- `POST /admin/route/preview`

Compute the current quota date inside endpoints using `quota_date_for`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_admin_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_router/app/api token_router/app/main.py tests/conftest.py tests/test_admin_api.py
git commit -m "Add admin and health APIs"
```

## Task 6: OpenAI-Compatible Provider And Chat Endpoint

**Files:**
- Create: `token_router/app/providers/__init__.py`
- Create: `token_router/app/providers/openai_compatible.py`
- Create: `token_router/app/schemas/chat.py`
- Create: `token_router/app/api/chat.py`
- Modify: `token_router/app/main.py`
- Test: `tests/test_chat_api.py`

- [ ] **Step 1: Write failing chat API test with fake provider transport**

```python
# tests/test_chat_api.py
from fastapi.testclient import TestClient

from token_router.app.main import create_app


class FakeProvider:
    async def chat_completion(self, provider_config, api_key, payload):
        assert payload["model"] == "model-a"
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "model-a",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }


def test_chat_endpoint_routes_and_records_usage(app_config, usage_manager):
    app = create_app(app_config, usage_manager, provider=FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "router": {"level": 1}},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "model-a"
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.total_tokens == 5
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_chat_api.py -v`

Expected: FAIL because chat API/provider support is not implemented.

- [ ] **Step 3: Implement provider forwarding**

Implement `OpenAICompatibleProvider.chat_completion(provider_config, api_key, payload)` using `httpx.AsyncClient`:

- POST to `{base_url.rstrip("/")}/chat/completions`.
- Send `Authorization: Bearer <api_key>`.
- Send JSON payload with selected `model`.
- Return parsed JSON for 2xx.
- Raise `httpx.HTTPStatusError` for non-2xx.

- [ ] **Step 4: Implement chat endpoint**

Endpoint flow:

1. Parse request JSON as a permissive dict.
2. Select route using request `model` and `router`.
3. Replace outgoing `model` with selected model name.
4. Remove `router` from forwarded payload.
5. Call provider.
6. Record upstream `usage` when present.
7. Log request status.
8. Return upstream JSON.

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_chat_api.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add token_router/app/providers token_router/app/schemas/chat.py token_router/app/api/chat.py token_router/app/main.py tests/test_chat_api.py
git commit -m "Add chat completions endpoint"
```

## Task 7: README And End-To-End Verification

**Files:**
- Create: `README.md`
- Modify: files only if verification exposes defects.

- [ ] **Step 1: Write README**

Include:

- local install command: `python -m pip install -e ".[dev]"`
- copy config command: `cp config.example.yaml config.yaml`
- start command: `uvicorn token_router.app.main:app --reload`
- `/admin/route/preview` example
- `/v1/chat/completions` example
- note that stream mode is not supported in MVP

- [ ] **Step 2: Run all tests**

Run: `pytest -v`

Expected: PASS.

- [ ] **Step 3: Run import smoke test**

Run: `python -c "from token_router.app.main import app; print(app.title)"`

Expected: prints `Local LLM Token Router`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document local router usage"
```

## Final Verification

- [ ] Run `pytest -v` and confirm all tests pass.
- [ ] Run `git status --short --branch` and confirm no unintended files are left unstaged.
- [ ] Review `git log --oneline -5` to report the implementation commits.
