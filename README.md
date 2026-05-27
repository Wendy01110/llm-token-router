# Local LLM Token Router

Local-only LLM gateway that routes OpenAI-compatible chat requests across configured model instances by level, daily token quota, and 25% usage stage.

The MVP is designed for personal local use.

## Setup

```bash
python -m pip install -e ".[dev]"
cp config.example.yaml config.yaml
```

Edit `config.yaml` and export the API keys referenced in it:

```bash
export DASHSCOPE_API_KEY_1="sk-..."
export DASHSCOPE_API_KEY_2="sk-..."
export DEEPSEEK_API_KEY_1="sk-..."
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
