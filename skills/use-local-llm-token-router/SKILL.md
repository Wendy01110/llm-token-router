---
name: use-local-llm-token-router
description: Use when integrating a personal development project with Wendy's local llm-token-router, OpenAI-compatible Chat Completions, native Responses API routing, model auto routing, router.provider, max_completion_tokens, reasoning_effort, stream_options.include_usage, runtime fallback, or local base_url http://127.0.0.1:8000/v1.
---

# Use Local LLM Token Router

## Overview

Use the local `llm-token-router` as an OpenAI-compatible Chat Completions provider and as a native Responses API proxy for providers that support `/responses`. Prefer `model: "auto"` so the router can select providers, manage quota, apply runtime fallback, and adapt standard OpenAI parameters after model selection.

## Source Of Truth

Before integrating a project, read the latest local router docs for the current supported request shape and router options:

- `/Users/wendy/code/python/llm-token-router/docs/client-integration-cn.md`
- `/Users/wendy/code/python/llm-token-router/README-CN.md`
- `/Users/wendy/code/python/llm-token-router/examples/openai_chat_test.py`

If those files are inaccessible, use the patterns in this skill as a fallback. Do not read `.env` unless the user explicitly asks; it may contain real API keys.

## Default Request Shape

Use this JSON body for automatic routing:

```json
{
  "model": "auto",
  "messages": [
    {"role": "user", "content": "Reply with OK."}
  ],
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

Streaming requests may include usage options:

```json
{
  "model": "auto",
  "messages": [
    {"role": "user", "content": "Reply with OK."}
  ],
  "store": false,
  "max_completion_tokens": 512,
  "reasoning_effort": "medium",
  "stream": true,
  "stream_options": {"include_usage": true},
  "router": {
    "level": 1,
    "provider": "auto",
    "fallback": true
  }
}
```

Only send `stream_options` with `stream: true`.

For Codex or clients that require the Responses API, use `/v1/responses` with `model: "auto"`. The router only sends these requests to endpoints configured with `responses_api: native`; it does not synthesize Responses output from Chat Completions.

```json
{
  "model": "auto",
  "input": "Reply with OK.",
  "stream": true,
  "router": {
    "level": 1,
    "provider": "auto",
    "fallback": true
  }
}
```

## Parameter Quick Reference

OpenAI-standard request fields to prefer for normal automatic routes:

| Field | Use | Notes |
| --- | --- | --- |
| `model` | Required | Use `"auto"` unless forcing a specific model. |
| `messages` | Required | Standard Chat Completions message list. |
| `store` | Optional | Usually `false`; if omitted, the router does not add it. |
| `max_completion_tokens` | Optional | Preferred output-token limit field. |
| `reasoning_effort` | Optional | Typical values: `"low"`, `"medium"`, `"high"`. |
| `response_format` | Optional | Forwarded when supported; `response_format.type` filters out configured incompatible model instances. |
| `stream` | Optional | Set `true` for SSE streaming. |
| `stream_options.include_usage` | Optional | Only send with `stream: true`. |
| other OpenAI Chat Completions fields | Optional | Forwarded unless the router adapts or removes a field for safety. |

Router-specific fields under `router`:

| Field | Use | Notes |
| --- | --- | --- |
| `provider` | Optional | Omit or use `"auto"` for automatic provider selection and parameter adaptation. |
| `level` | Optional | Starting model level; lower is preferred. |
| `fallback` | Optional | Allow fallback to later levels. |
| `max_fallback_level` | Optional | Highest fallback level allowed. |
| `fallback_models` | Optional | Ordered model-name list used only for runtime fallback attempts. |
| `strict_model` | Optional | Prevent fallback to a different model when true. |
| `model_group` | Optional | Restrict selection to a configured group such as `"coding"`. |
| `thinking` | Optional | Router-native thinking on/off override. |
| `thinking_effort` | Optional | Router-native effort value used with `thinking`. |
| `debug` | Optional | Return `X-Router-*` headers for diagnostics. |

Default behavior: missing optional OpenAI fields are not injected. The router translates or removes only fields that the client sends, except streaming usage policy may add `stream_options.include_usage` for endpoints configured to require it.

For Chat Completions automatic routes, provider thinking fields differ:

- OpenRouter uses `reasoning.effort` or `reasoning.enabled`.
- Xiaomi MiMo uses `thinking.type`.
- Volcengine Ark Chat uses `thinking.type`; Ark Chat models that support effort keep top-level `reasoning_effort`.

Ark Responses uses a different effort shape from Ark Chat: Responses effort is `reasoning.effort`, not top-level `reasoning_effort`. The local `/v1/responses` endpoint is a native proxy and currently does not translate `router.thinking` or `router.thinking_effort`; send provider-native `thinking` and `reasoning` fields for Responses thinking control.

## Runtime Fallback

Local quota records do not fully capture upstream TPS/RPM pressure. At runtime, the router falls back to another eligible route when an upstream call fails with `400`, `401`, `403`, `429`, `5xx`, network errors, timeouts, or when the selected model is at `max_concurrency`. The failed `(provider, endpoint, key_id, model)` enters an in-process cooldown controlled by `routing.runtime_cooldown_seconds` (default `30`).

Use `router.fallback_models` to order runtime fallback candidates without changing the initial normal route selection. The listed models still have to pass provider, level, model group, capability, quota, response format, and concurrency filters.

Other `4xx` responses are returned to the client. Streaming fallback is only available before the first upstream SSE chunk is sent to the client. Once the stream has started, the router keeps that stream on the selected route.

Native Responses requests use the same runtime fallback behavior, but only among endpoints configured with `responses_api: native`.

## Integration Rules

- Set `base_url` to `http://127.0.0.1:8000/v1` by default.
- Use any non-empty placeholder API key unless the target project enforces its own client key convention.
- Use `model: "auto"` for normal application code.
- Use `model: "auto"` for Codex Responses API profiles too; the router will choose only native Responses-capable endpoints for `/v1/responses`.
- Put router controls in the request body under `router`; the local router removes this field before calling upstream providers.
- Leave `router.provider` absent or set to `"auto"` to allow OpenAI-standard parameter adaptation.
- Use explicit `router.provider` only for debugging, benchmarking, or forcing a vendor path. In explicit-provider calls, standard fields are intentionally passed through.
- Prefer `max_completion_tokens`; avoid new uses of deprecated `max_tokens`.
- Prefer `reasoning_effort: "medium"` for reasoning-capable automatic routes unless the project needs faster or deeper responses.
- If sending `response_format.type`, let the router skip models configured with incompatible `unsupported_response_format_types`.
- If the user wants router-native thinking control for Chat Completions, use `router.thinking` and optional `router.thinking_effort`; these override top-level `reasoning_effort`.
- For `/v1/responses`, do not rely on `router.thinking`; pass the upstream-native Responses thinking fields directly.

## Python SDK Pattern

Prefer named OpenAI parameters when the installed SDK supports them, and place only `router` in `extra_body`:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-router-client",
)

response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Reply with OK."}],
    store=False,
    max_completion_tokens=512,
    reasoning_effort="medium",
    extra_body={
        "router": {
            "level": 1,
            "provider": "auto",
            "fallback": True,
        }
    },
)
```

If the target project's SDK version rejects newer fields, move them into `extra_body` with `router`:

```python
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Reply with OK."}],
    extra_body={
        "store": False,
        "max_completion_tokens": 512,
        "reasoning_effort": "medium",
        "router": {"level": 1, "provider": "auto", "fallback": True},
    },
)
```

## HTTP Pattern

Use direct HTTP when SDK typing blocks custom fields:

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "store": false,
    "max_completion_tokens": 512,
    "reasoning_effort": "medium",
    "router": {"level": 1, "provider": "auto", "fallback": true}
  }'
```

For native Responses clients:

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "input": "Reply with OK.",
    "stream": false,
    "router": {"level": 1, "provider": "auto", "fallback": true}
  }'
```

## Smoke Tests

Real tests depend on the user's local service, ports, credentials, and upstream provider state. In other projects, provide these commands and expected signals instead of running them unless the user explicitly asks.

Health check:

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/health
```

Success: `{"status":"ok"}`.

Route debug check:

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "store": false,
    "max_completion_tokens": 64,
    "reasoning_effort": "medium",
    "router": {"level": 1, "provider": "auto", "fallback": true, "debug": true}
  }'
```

Success: HTTP 200, a normal Chat Completions response, and `X-Router-*` headers when using a raw-response client.

Responses check:

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "input": "Reply with OK.",
    "router": {"level": 1, "provider": "auto", "fallback": true, "debug": true}
  }'
```

Success: HTTP 200 and a native Responses object from an endpoint marked `responses_api: native`.

## Keeping This Skill Current

Treat `/Users/wendy/code/python/llm-token-router/docs/client-integration-cn.md` as the canonical integration guide. When asked to update this skill, compare that guide with this file and update both:

- Repo source: `/Users/wendy/code/python/llm-token-router/skills/use-local-llm-token-router/SKILL.md`
- Installed copy: `/Users/wendy/.agents/skills/use-local-llm-token-router/SKILL.md`

If writing the installed copy is blocked by sandboxing, request scoped approval to write only that skill directory.
