# Local LLM Token Router MVP Design

Date: 2026-05-27

## Goal

Build a local-only LLM token router that exposes an OpenAI-compatible chat endpoint and routes requests across configured model instances by level, quota usage, and 25% usage stages.

The first version is for one local user, not multi-tenant production use.

## Assumptions

- The service runs on a trusted local machine.
- Provider APIs are OpenAI-compatible in the MVP.
- Model/provider/key configuration is loaded from `config.yaml` at startup.
- SQLite is enough for daily usage and request logs.
- API keys may be referenced through environment variables in YAML.
- Daily quota resets use the configured timezone and reset hour, defaulting to `Asia/Shanghai` and `11:00`.

## Non-Goals

- Web admin UI
- Multi-user auth and permissions
- Redis or distributed locking
- PostgreSQL
- Prometheus/Grafana
- Dynamic provider/key management through APIs
- API key encryption at rest
- Streaming responses
- Request-time quota reservation
- Complex cooldown and retry policy

## Success Criteria

- The app starts locally with `uvicorn`.
- `POST /v1/chat/completions` accepts OpenAI-style chat requests.
- `model: "auto"` plus `router.level` selects an enabled model instance.
- The selector prefers lower level numbers, lower 25% usage stage, lower priority, and lower usage ratio.
- Provider/model filters in the request narrow the candidate set.
- Usage returned by the upstream provider is recorded in SQLite.
- `GET /admin/models` shows configured model instances with usage ratio and stage.
- `POST /admin/route/preview` returns the selected candidate without calling the provider.
- Tests cover quota date calculation, stage calculation, candidate filtering, and selector ordering.

## Architecture

The MVP has four main modules:

- `ConfigLoader`: loads YAML configuration, resolves environment variable references, and validates provider/key/model references.
- `UsageManager`: stores and reads daily token usage in SQLite, keyed by provider, key id, model name, and quota date.
- `RouteSelector`: turns request constraints into candidates, attaches usage information, filters exhausted/disabled instances, and chooses the best candidate.
- `OpenAICompatibleProvider`: forwards chat completion requests to the selected provider using the selected API key.

FastAPI wires these modules into HTTP endpoints.

## Data Model

Configuration lives in YAML:

- `refresh`: timezone and daily reset hour.
- `routing`: default level, fallback setting, stage thresholds.
- `providers`: provider name, type, base URL, and API keys.
- `model_instances`: provider, key id, model name, level, daily quota, priority, and groups.

SQLite stores runtime state:

- `model_usage_daily`: prompt tokens, completion tokens, total tokens, and request count per model instance per quota date.
- `request_logs`: selected route, usage, status, error message, and latency.

The core routing unit is:

```text
ModelInstance = provider + key_id + model_name
```

## Routing Behavior

The request can constrain routing with:

- `model`: `"auto"` or a concrete model name.
- `router.level`: desired starting level, default from config.
- `router.provider`: `"auto"` or a concrete provider.
- `router.model_group`: optional group filter.
- `router.strict_model`: when true, a concrete model cannot fall back to other models.
- `router.fallback`: whether lower model levels may be tried.
- `router.max_fallback_level`: lowest allowed fallback level.

For the MVP, routing is stateless. There is no cursor.

Provider filtering is always strict: if `router.provider` is a concrete provider, the selector never crosses to another provider. Model filtering is strict only when `router.strict_model` is true. When a concrete model is requested and `strict_model` is false, the selector first tries that model; if no candidate is available and fallback is enabled, it may relax the model constraint while continuing through allowed fallback levels.

Candidate selection:

1. Build candidate levels from requested level to `max_fallback_level` when fallback is enabled.
2. Load configured model instances matching level, provider, model, and group constraints.
3. Attach current quota usage.
4. Drop disabled or exhausted instances.
5. Sort candidates by:
   - `level`
   - `stage`
   - `priority`
   - `usage_ratio`
   - `provider`
   - `key_id`
   - `model_name`
6. Select the first candidate.
7. If no candidate is found for a concrete model and `strict_model` is false, repeat the same process without the model constraint.

Stage calculation:

```text
0 <= usage_ratio < 0.25  -> stage 1
0.25 <= usage_ratio < 0.5 -> stage 2
0.5 <= usage_ratio < 0.75 -> stage 3
0.75 <= usage_ratio < 1.0 -> stage 4
usage_ratio >= 1.0        -> exhausted
```

This naturally spreads traffic across same-level instances as each instance crosses a 25% threshold.

## API Surface

### `GET /health`

Returns service status.

### `POST /v1/chat/completions`

Accepts an OpenAI-compatible chat completion request with an optional `router` object.

The router replaces `model: "auto"` with the selected upstream model before forwarding. The response includes router debug headers when `router.debug` is true.

### `GET /admin/models`

Returns configured model instances with:

- provider
- key id
- model name
- level
- daily quota
- used tokens
- usage ratio
- stage
- enabled
- available

### `POST /admin/route/preview`

Runs the selector with the supplied request constraints and returns the selected model instance plus a concise reason.

## Error Handling

- No available candidate returns HTTP 429.
- Invalid config returns startup failure.
- Upstream authentication or request errors are passed through with a request log entry.
- Upstream 429/5xx are logged but not retried in the MVP.

## Testing Strategy

Use focused unit tests first:

- Quota date switches at the configured 11:00 boundary.
- Usage ratio maps to the correct stage.
- Exhausted candidates are filtered out.
- Provider/model/group constraints are honored.
- Selector chooses the lowest stage before lower usage ratio.
- Fallback moves from level 1 to level 2 when level 1 is exhausted.

Then add a small API test using FastAPI's test client for `/admin/route/preview`.

Provider calls should be tested with a fake transport, not real network calls.

## Initial File Layout

```text
token_router/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── api/
│   │   ├── admin.py
│   │   ├── chat.py
│   │   └── health.py
│   ├── providers/
│   │   └── openai_compatible.py
│   ├── router/
│   │   ├── quota.py
│   │   └── selector.py
│   └── schemas/
│       ├── chat.py
│       └── router.py
├── tests/
├── config.example.yaml
├── pyproject.toml
└── README.md
```

## Implementation Order

1. Project packaging and dependencies.
2. Config models and YAML loading.
3. Quota date and stage helpers.
4. SQLite schema and usage manager.
5. Route selector.
6. Admin preview/status APIs.
7. OpenAI-compatible provider forwarding.
8. Chat endpoint.
9. README and example config.
