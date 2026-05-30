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
