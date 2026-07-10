# Local LLM Token Router

Local-only LLM gateway that routes OpenAI-compatible chat requests across configured model instances by level, daily token quota, and 25% usage stage.

The MVP is designed for personal local use.

## Setup

Use a project-local virtual environment. The current `.venv` was tested with
Python 3.13.13; the package requires Python 3.11 or newer.

```bash
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp config.example.yaml config.yaml
cp .env.example .env
```

Edit `.env` and set the provider keys referenced by your config:

```bash
MIMO_TOKEN_PLAN_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
MIMO_TOKEN_PLAN_KEY=tp-...
MIMO_TOKEN_PLAN_MODEL=mimo-v2.5-pro

ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_API_KEY=...
ARK_API_KEY_2=...
ARK_PAYG_API_KEY=...
ARK_MODEL=doubao-seed-2-1-pro-260628

DS_PAYG_API_KEY=...

AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
AGNES_API_KEY=...
AGNES_MODEL=agnes-2.0-flash

OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=...
OPENROUTER_API_KEY_2=...
OPENROUTER_FREE_MODEL=openrouter/free

TAVILY_API_KEY=tvly-...
```

The repository's current `config.yaml` declares Ark plan model IDs directly and uses both `ARK_API_KEY` and `ARK_API_KEY_2`. The smaller `config.example.yaml` uses one plan key and the `ARK_MODEL` placeholder. `ARK_PAYG_API_KEY` and `DS_PAYG_API_KEY` are used only by explicit PayG routes.

`load_config()` reads `.env` from the same directory as `config.yaml` before resolving `${VAR_NAME}` references. Existing shell environment variables take precedence over values in `.env`.

`config.example.yaml` enables these providers by default:

- `xiaomi_mimo`
- `volcengine_ark`
- `deepseek`
- `agnes`
- `openrouter`

Provider and endpoint are separate:

- `provider` is the supplier, for example `xiaomi_mimo`.
- `endpoint` is one concrete URL/key pool under that supplier, for example `token_plan` or a future `api`.
- `model_instances` point to `provider + endpoint + key_id + model`.

This matters for Xiaomi MiMo because Token/Coding Plan URL/key and normal supplier API URL/key are different. Keep them as different endpoints under the same provider:

```yaml
providers:
  xiaomi_mimo:
    type: openai_compatible
    endpoints:
      token_plan:
        base_url: ${MIMO_TOKEN_PLAN_BASE_URL}
        auth_header: api_key
        keys:
          - id: mimo_token_plan
            value: ${MIMO_TOKEN_PLAN_KEY}
      api:
        base_url: ${MIMO_API_BASE_URL}
        auth_header: api_key
        keys:
          - id: mimo_api
            value: ${MIMO_API_KEY}
```

To add another provider later, add a new entry under `providers`, then add one or more `model_instances` pointing at that provider endpoint/key pair. Use `auth_header: authorization_bearer` for normal OpenAI-compatible Bearer auth, or `auth_header: api_key` for providers that expect an `api-key` header.

### OpenRouter Free Fallback

The example config uses OpenRouter's official Free Models Router, `openrouter/free`, instead of enumerating specific `:free` model ids from the model catalog. This keeps the local config stable while OpenRouter's free model list changes.

AGNES `agnes-2.0-flash` is configured as the fallback tier before OpenRouter. The OpenRouter model instance remains the lowest fallback tier:

```yaml
model_instances:
  - name: ${OPENROUTER_FREE_MODEL}
    provider: openrouter
    endpoint: api
    level: 5
    keys:
      - key_id: openrouter_1
        daily_quota: 4000000
        daily_request_quota: 40
        priority: 100
      - key_id: openrouter_2
        daily_quota: 4500000
        daily_request_quota: 45
        priority: 110
    groups: [general, coding, fallback, free]
```

OpenRouter requires `Authorization: Bearer <key>`, which maps to `auth_header: authorization_bearer`. The default config defines `openrouter_1` from `OPENROUTER_API_KEY` and `openrouter_2` from `OPENROUTER_API_KEY_2`. Optional OpenRouter attribution headers are not required for routing and are not sent by the current provider adapter.

The current local limits are `openrouter_1: 4M / 40 requests` and `openrouter_2: 4.5M / 45 requests`. `priority` is set on each key entry, so the router uses `openrouter_1` first and switches to `openrouter_2` after the first key reaches its request quota.

To force a local request through OpenRouter:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/free",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "router": {
      "provider": "openrouter",
      "level": 5,
      "fallback": false,
      "debug": true
    }
  }'
```

OpenRouter docs:

- [Free Models Router](https://openrouter.ai/docs/cookbook/get-started/free-models-router-playground)
- [API Quickstart](https://openrouter.ai/docs/quickstart)

## Configuration Maintenance

Local configuration lives in:

- `config.yaml`: providers, endpoints, model instances, quota and routing settings.
- `.env`: real URLs, API keys, and model names referenced by `${VAR_NAME}`.

`config.yaml` can be versioned when it contains only non-secret values and `${VAR_NAME}` references. Keep `.env` local and ignored by Git because it contains real keys.

After changing either file, restart `uvicorn` so the app reloads the config.

### Add An API Key

Add the secret to `.env`:

```bash
MIMO_TOKEN_PLAN_KEY_2=tp-another-key
```

Add a key entry under the existing endpoint in `config.yaml`:

```yaml
providers:
  xiaomi_mimo:
    endpoints:
      token_plan:
        keys:
          - id: mimo_token_plan
            value: ${MIMO_TOKEN_PLAN_KEY}
          - id: mimo_token_plan_2
            value: ${MIMO_TOKEN_PLAN_KEY_2}
```

Then add a model instance for that key:

```yaml
model_instances:
  - name: ${MIMO_TOKEN_PLAN_MODEL}
    provider: xiaomi_mimo
    endpoint: token_plan
    level: 1
    keys:
      - key_id: mimo_token_plan_2
        daily_quota: 1800000
        priority: 10
    groups: [coding, general]
```

Use a unique `key_id` per endpoint.

### Remove An API Key

Remove both references:

- Delete the key entry from `providers.<provider>.endpoints.<endpoint>.keys`.
- Delete or update every `model_instances` entry that uses that `key_id`.

The app validates this on startup. If a model instance points at a missing key, startup config loading fails.

### Add A Model

Add one `model_instances` entry:

```yaml
model_instances:
  - name: new-model-name
    provider: volcengine_ark
    endpoint: api
    level: 2
    max_concurrency: 4
    keys:
      - key_id: volcengine_ark_1
        daily_quota: 1800000
        quota_refresh_mode: delayed_calendar_day
        priority: 30
    groups: [general]
    unsupported_response_format_types: []
```

Fields:

- `name`: client-facing model name accepted by the local router.
- `upstream_model`: optional upstream model ID; defaults to `name`.
- `provider`: supplier name under `providers`.
- `endpoint`: URL/key pool under that provider.
- `level`: smaller has higher routing priority; `1` is first.
- `max_concurrency`: maximum in-flight requests per model/key route. Multiple keys for the same model each get this limit independently; when one route is saturated, the router skips it and selects another eligible route.
- `keys[].key_id`: key under that endpoint.
- `keys[].daily_quota`: daily token budget for this model/key instance.
- `keys[].daily_request_quota`: optional daily request budget for that key.
- `keys[].quota_refresh_mode`: optional quota refresh behavior. `shifted_day` uses the global reset-hour bucket. `delayed_calendar_day` records natural calendar days, counts yesterday plus today before the reset hour, and counts only today after the reset hour.
- `keys[].priority`: lower wins within the same level/stage.
- `groups`: optional tags such as `coding`, `general`, `reasoning`, `fallback`.
- `requires_explicit_model`: when true, the route is excluded from `model: "auto"` and must be requested by name.
- `unsupported_response_format_types`: optional response format types to skip for this model instance, for example `[json_object]`.

Ark plan quotas are independent per `model + key`. In the current `config.yaml`, most plan routes use 1.8M tokens; `doubao-seed-2-0-code-preview-260215` uses 1M on `volcengine_ark_1` and 1.8M on `volcengine_ark_2`. All plan routes use `delayed_calendar_day`. The explicit DeepSeek and Ark PayG routes use a 10M local limit and the default `shifted_day` mode.

The first startup after enabling `delayed_calendar_day` builds separate calendar-day usage buckets from local `request_logs`, using request latency to approximate each request's start time. New requests are then recorded directly into those buckets, so existing reset-hour aggregates are never reinterpreted as calendar-day data.

### Disable Or Remove A Model

To temporarily disable a model instance:

```yaml
model_instances:
  - name: new-model-name
    provider: volcengine_ark
    endpoint: api
    key_id: volcengine_ark_1
    enabled: false
```

To permanently remove it, delete that `model_instances` block.

### Add A New Supplier

Add the provider endpoint and key:

```yaml
providers:
  new_supplier:
    type: openai_compatible
    stream_usage_mode: parse_only
    endpoints:
      api:
        base_url: ${NEW_SUPPLIER_BASE_URL}
        auth_header: authorization_bearer
        keys:
          - id: new_supplier_1
            value: ${NEW_SUPPLIER_API_KEY}
```

Add the variables to `.env`:

```bash
NEW_SUPPLIER_BASE_URL=https://example.com/v1
NEW_SUPPLIER_API_KEY=sk-...
```

Then add model instances that point at `new_supplier/api/new_supplier_1`.

`stream_usage_mode` controls only streaming usage accounting for requests where the client sends `stream: true`. It does not force streaming. Set it at provider level for a default, and override it under an endpoint when one endpoint behaves differently.

### Add A New URL Under An Existing Supplier

Use a new endpoint name when the URL or key type differs. For example, Xiaomi MiMo Token Plan and Xiaomi MiMo normal API should be separate endpoints:

```yaml
providers:
  xiaomi_mimo:
    endpoints:
      token_plan:
        base_url: ${MIMO_TOKEN_PLAN_BASE_URL}
        auth_header: api_key
        keys:
          - id: mimo_token_plan
            value: ${MIMO_TOKEN_PLAN_KEY}
      api:
        base_url: ${MIMO_API_BASE_URL}
        auth_header: api_key
        keys:
          - id: mimo_api
            value: ${MIMO_API_KEY}
```

Add separate model instances for each endpoint/key pair.

### Change A URL

Prefer changing the `.env` value rather than editing `config.yaml`:

```bash
MIMO_TOKEN_PLAN_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
```

If the new URL uses a different key or auth mode, create a new endpoint instead of mutating the existing one. That keeps old usage records and routing behavior easier to understand.

### Check Your Changes

Preview the selected route:

```bash
curl -s http://127.0.0.1:8000/admin/route/preview \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","router":{"level":1,"debug":true}}'
```

List model status and quota usage:

```bash
curl -s http://127.0.0.1:8000/admin/models
```

Open the usage dashboard:

```text
http://127.0.0.1:8000/admin/usage
```

## Run

Run in the foreground during development so logs stay visible:

```bash
. .venv/bin/activate
python -m uvicorn token_router.app.main:app --reload
```

For local background use, run this from the project root:

```bash
mkdir -p logs
nohup .venv/bin/python -m uvicorn token_router.app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  > logs/router.log 2>&1 &
echo $! > .router.pid
```

By default the service reads:

- config: `config.yaml`
- SQLite database: `token_router.sqlite3`

Override them with:

```bash
export TOKEN_ROUTER_CONFIG=/path/to/config.yaml
export TOKEN_ROUTER_DB=/path/to/token_router.sqlite3
```

Check that the service is running:

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/health
```

Successful output:

```json
{"status":"ok"}
```

View background logs:

```bash
tail -f logs/router.log
```

Stop the background service:

```bash
kill "$(cat .router.pid)"
rm -f .router.pid
```

## Preview Routing

```bash
curl -s http://127.0.0.1:8000/admin/route/preview \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "router": {
      "level": 1
    }
  }'
```

## Chat Completions

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [
      {
        "role": "user",
        "content": "Explain GraphRAG in one paragraph."
      }
    ],
    "router": {
      "level": 1,
      "provider": "auto",
      "fallback": true,
      "debug": true
    }
  }'
```

### OpenAI Standard Parameter Adaptation

The default client path should use `model: "auto"` with standard OpenAI Chat Completions fields:

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Reply with OK."}],
  "store": false,
  "max_completion_tokens": 512,
  "reasoning_effort": "medium",
  "router": {
    "level": 1,
    "provider": "auto",
    "fallback": true
  }
}
```

After model selection, the router adapts these standard fields to the selected provider. For Chat Completions auto routes, OpenRouter uses `reasoning.effort`, MiMo uses `thinking.type`, and Ark uses `thinking.type`; Ark Chat models that support effort keep the top-level `reasoning_effort` field. When `router.provider` is explicit, standard fields pass through unchanged except for the router-native `router.thinking` option. Send `stream_options` only with `stream: true`; automatic non-streaming routes remove it before calling upstream providers.

Ark uses different effort fields for Chat and Responses: Chat API uses top-level `reasoning_effort`, while Responses API uses `reasoning.effort`. The local `/v1/responses` endpoint is a native proxy and currently does not translate `router.thinking` or `router.thinking_effort`; send provider-native `thinking` and `reasoning` fields when a Responses request needs thinking control.

### Runtime Fallback

The router falls back to another eligible route when an upstream call fails with `400`, `401`, `403`, `429`, `5xx`, a network error, a timeout, or when the selected model/key route is at `max_concurrency`. Other `4xx` responses are returned to the caller. Use `router.fallback_models` to order runtime fallback models without changing the initial normal route selection. Listed models still have to pass provider, level, model group, capability, quota, response format, and concurrency filters:

OpenAI-compatible upstream calls use an 1800-second router-side HTTP timeout per non-streaming attempt and a 150-second timeout per streaming attempt; timeout failures follow the runtime fallback rules above.

```json
{
  "router": {
    "level": 1,
    "fallback": true,
    "fallback_models": [
      "glm-4-7-251222",
      "deepseek-v3-2-251201"
    ]
  }
}
```

### Streaming

When the client sends `stream: true`, the router returns OpenAI-compatible SSE as `text/event-stream` and forwards upstream `data:` frames to the client. Usage is recorded from the latest non-null streaming `usage` chunk when the provider emits one. If no usage appears before the stream ends, the request count is still recorded and token usage is recorded as zero.

```bash
curl --no-buffer http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": true,
    "router": {
      "level": 1,
      "provider": "auto",
      "fallback": true,
      "debug": true
    }
  }'
```

Success signal:

- The response content type starts with `text/event-stream`.
- The output contains `data:` frames and ends with `data: [DONE]`.
- `/admin/usage` shows the selected key request count incrementing.

## OpenAI SDK Example

After starting the local router, test the OpenAI-compatible endpoint with the OpenAI Python SDK. The script calls local `http://127.0.0.1:8000/v1/chat/completions`, then prints the model response, usage, and `X-Router-*` headers.

```bash
. .venv/bin/activate
python examples/openai_chat_test.py
```

Defaults:

```text
base_url: http://127.0.0.1:8000/v1
model: doubao-seed-2-0-mini-260428
provider: volcengine_ark
level: 3
```

The local router does not currently validate the client OpenAI API key, so the example script uses a placeholder key. Real upstream provider keys are still read from `.env` and `config.yaml`.

Successful output should include:

```text
router_headers.X-Router-Provider = volcengine_ark
router_headers.X-Router-Model = doubao-seed-2-0-mini-260428
response.message.content
response.usage.total_tokens
```

Override the target with environment variables:

```bash
ROUTER_MODEL=auto ROUTER_PROVIDER=volcengine_ark ROUTER_LEVEL=3 python examples/openai_chat_test.py
```

Test a specific model:

```bash
ROUTER_MODEL=doubao-seed-2-0-mini-260428 \
ROUTER_PROVIDER=volcengine_ark \
ROUTER_LEVEL=3 \
python examples/openai_chat_test.py
```

If the script cannot connect, check the background service first:

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/health
tail -n 80 logs/router.log
```

## Admin Models

```bash
curl -s http://127.0.0.1:8000/admin/models
```

## Usage Dashboard

```text
http://127.0.0.1:8000/admin/usage
```

## Daily Model Evaluation

The daily evaluator uses fixed Tavily daily-news queries with `topic` set to `general`, `news`, and `finance`, then asks enabled `model_instance` and `key_id` entries in `config.yaml` to summarize the same hotspot context. `volcengine_ark` is excluded from daily evaluation; normal routing can still use those models.

Run it manually from the project root:

```bash
. .venv/bin/activate
python scripts/daily_model_eval.py
```

Model/key evaluations run concurrently. The default concurrency is `4`; override it with:

```bash
DAILY_EVAL_CONCURRENCY=6 python scripts/daily_model_eval.py
```

When the router service starts, it also starts a background scheduler if `TAVILY_API_KEY` is set. With the normal service command, the evaluator runs automatically every day at `00:00` in `config.refresh.timezone`:

```bash
nohup .venv/bin/python -m uvicorn token_router.app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  > logs/router.log 2>&1 &
```

Use `DAILY_EVAL_MAX_TOKENS`, `DAILY_EVAL_CONCURRENCY`, and `TOKEN_ROUTER_REPORTS_DIR` to tune the background run. If `TAVILY_API_KEY` is missing, the web service still starts and logs that the daily evaluator is disabled.

Outputs:

- `reports/daily-model-eval/YYYY-MM-DD/report.md`
- `reports/daily-model-eval/YYYY-MM-DD/results.jsonl`
- `reports/daily-model-eval/YYYY-MM-DD/tavily.json`
- `reports/daily-model-eval/latest.json`

Successful model calls are recorded through the existing SQLite usage tables, so the evaluation token usage appears in `/admin/models` and `/admin/usage` and affects later routing. Failed calls are logged in `request_logs` but are not counted against model daily token quota.

Open the latest report in the local router:

```text
http://127.0.0.1:8000/
```

The page only reads saved report files. Refreshing it does not call Tavily or any model.

## MVP Limits

- Provider adapters assume OpenAI-compatible APIs.
- API keys are loaded from local config/environment variables.
- There is no web UI, user auth, or Redis locking; runtime cooldown is stored in-process only.

## Tests

```bash
. .venv/bin/activate
python -m pytest -v
```
