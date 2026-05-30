# Local LLM Token Router

Local-only LLM gateway that routes OpenAI-compatible chat requests across configured model instances by level, daily token quota, and 25% usage stage.

The MVP is designed for personal local use.

## Setup

```bash
python -m pip install -e ".[dev]"
cp config.example.yaml config.yaml
cp .env.example .env
```

Edit `.env` and set your current Xiaomi MiMo Token/Coding Plan key and Volcengine Ark key:

```bash
MIMO_TOKEN_PLAN_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
MIMO_TOKEN_PLAN_KEY=tp-...
MIMO_TOKEN_PLAN_MODEL=mimo-v2.5-pro

VOLCENGINE_ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
VOLCENGINE_ARK_API_KEY=...
VOLCENGINE_ARK_MODEL=doubao-seed-2-0-lite-260215
```

`load_config()` reads `.env` from the same directory as `config.yaml` before resolving `${VAR_NAME}` references. Existing shell environment variables take precedence over values in `.env`.

`config.example.yaml` only enables the two providers above by default.

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

## Configuration Maintenance

Local configuration lives in:

- `config.yaml`: providers, endpoints, model instances, quota and routing settings.
- `.env`: real URLs, API keys, and model names referenced by `${VAR_NAME}`.

Keep both files local. They are ignored by Git.

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
    key_id: mimo_token_plan_2
    level: 1
    daily_quota: 50000000
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
    key_id: volcengine_ark_1
    level: 2
    daily_quota: 10000000
    priority: 30
    groups: [general]
```

Fields:

- `name`: upstream model name sent to the provider.
- `provider`: supplier name under `providers`.
- `endpoint`: URL/key pool under that provider.
- `key_id`: key under that endpoint.
- `level`: smaller is higher priority; `1` is strongest.
- `daily_quota`: daily token budget for this model/key instance.
- `priority`: lower wins within the same level/stage.
- `groups`: optional tags such as `coding`, `general`, `reasoning`, `fallback`.

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

## Run

```bash
uvicorn token_router.app.main:app --reload
```

By default the service reads:

- config: `config.yaml`
- SQLite database: `token_router.sqlite3`

Override them with:

```bash
export TOKEN_ROUTER_CONFIG=/path/to/config.yaml
export TOKEN_ROUTER_DB=/path/to/token_router.sqlite3
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

## Admin Models

```bash
curl -s http://127.0.0.1:8000/admin/models
```

## MVP Limits

- Streaming responses are not supported yet.
- Provider adapters assume OpenAI-compatible APIs.
- API keys are loaded from local config/environment variables.
- There is no web UI, user auth, Redis locking, or retry/cooldown policy in this version.

## Tests

```bash
python -m pytest -v
```
