# Streaming Chat Completions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add client-requested `stream=true` support to the local OpenAI-compatible router while preserving SSE passthrough and daily usage/request accounting.

**Architecture:** Add a small streaming policy layer that mutates outgoing payloads only when a provider explicitly supports a usage option. Add a provider streaming method that yields upstream SSE bytes. Add an SSE accounting wrapper used by the FastAPI endpoint to forward bytes immediately while parsing `data:` frames for the latest non-null `usage`.

**Tech Stack:** FastAPI `StreamingResponse`, `httpx.AsyncClient.stream`, SQLite-backed `UsageManager`, existing Pydantic config models, pytest with `httpx.MockTransport` and FastAPI `TestClient`.

---

## Assumptions

- Non-streaming behavior must remain unchanged.
- Streaming is controlled only by the downstream client request. The router must not force streaming when the client omits `stream` or sets `stream: false`.
- When the client requests a concrete model, the upstream streamed response must come from the selected route for that requested model, subject to the existing `strict_model` and fallback rules. When the client requests `model: "auto"`, the upstream streamed response comes from the model selected by the existing router.
- The router should not buffer the whole stream before returning data to the client.
- The MVP records request count for every successful stream. If no usage chunk is observed, token usage is recorded as zero.
- Provider-specific behavior is configured per endpoint, not hard-coded from provider name.
- Real provider smoke tests require local credentials and should be run manually by the user after unit tests pass.

## File Structure

- Modify `token_router/app/config.py`: add endpoint stream usage mode configuration.
- Create `token_router/app/providers/streaming.py`: payload mutation and SSE usage extraction helpers.
- Modify `token_router/app/providers/openai_compatible.py`: add a streaming method that yields raw upstream bytes.
- Modify `token_router/app/api/chat.py`: branch on `stream=true`, return `StreamingResponse`, and record usage after stream completion.
- Modify `token_router/app/schemas/chat.py`: no required schema change; it already allows extra OpenAI fields, but tests should assert `stream` is preserved.
- Modify `config.example.yaml`: set stream usage modes for Xiaomi MiMo, Ark, and OpenRouter.
- Modify `README.md` and `README-CN.md`: document stream support and manual smoke tests.
- Test `tests/test_streaming.py`: new focused unit/API tests for policy, parser, provider streaming, and endpoint behavior.
- Modify existing tests only when new config fields affect expected fixtures.

## Request Semantics

The router has two independent decisions:

1. **Response shape:** determined by the client request. If `stream: true`, return `text/event-stream`; otherwise return the existing JSON response.
2. **Route selection:** determined by the existing model/router rules. `model: "auto"` selects a configured model instance. A concrete `model` selects that model first, and may only relax according to current `strict_model` and fallback behavior.

Provider stream usage modes only decide whether the router should add provider-specific usage-accounting parameters to a request that is already streaming. They must not turn a non-streaming request into a streaming request.

## Provider Stream Usage Modes

Use these string values in config:

```yaml
stream_usage_mode: parse_only
```

Supported modes:

- `openai_include_usage`: add `stream_options.include_usage=true` if absent.
- `ark_include_usage`: add `stream_options.include_usage=true` if absent and preserve `chunk_include_usage`.
- `no_option_usage_chunk`: do not add `stream_options`; parse final empty-choice usage chunk.
- `final_chunk_usage`: do not add `stream_options`; parse usage on any chunk, including the stop chunk.
- `parse_only`: do not add `stream_options`; parse usage if present.

Initial local config:

- `xiaomi_mimo.token_plan`: `no_option_usage_chunk`
- `volcengine_ark.api`: `ark_include_usage`
- `openrouter.api`: `no_option_usage_chunk`

---

### Task 1: Configure Stream Usage Mode

**Files:**
- Modify: `token_router/app/config.py`
- Modify: `config.example.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

Add assertions to `tests/test_config.py::test_config_example_uses_current_local_providers`:

```python
assert config.providers["xiaomi_mimo"].get_endpoint("token_plan").stream_usage_mode == "no_option_usage_chunk"
assert config.providers["volcengine_ark"].get_endpoint("api").stream_usage_mode == "ark_include_usage"
assert config.providers["openrouter"].get_endpoint("api").stream_usage_mode == "no_option_usage_chunk"
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_config.py::test_config_example_uses_current_local_providers -q
```

Expected: FAIL with an `AttributeError` or assertion failure because `EndpointConfig` does not yet expose `stream_usage_mode`.

- [ ] **Step 3: Implement config field**

In `token_router/app/config.py`, add:

```python
StreamUsageMode = Literal[
    "openai_include_usage",
    "ark_include_usage",
    "no_option_usage_chunk",
    "final_chunk_usage",
    "parse_only",
]
```

Add to `EndpointConfig`:

```python
stream_usage_mode: StreamUsageMode = "parse_only"
```

Add to `ProviderConfig`:

```python
stream_usage_mode: StreamUsageMode = "parse_only"
```

When `ProviderConfig.get_endpoint()` builds a legacy default endpoint, pass through `stream_usage_mode=self.stream_usage_mode`.

- [ ] **Step 4: Update example config**

In `config.example.yaml`, set:

```yaml
providers:
  xiaomi_mimo:
    endpoints:
      token_plan:
        stream_usage_mode: no_option_usage_chunk

  volcengine_ark:
    endpoints:
      api:
        stream_usage_mode: ark_include_usage

  openrouter:
    endpoints:
      api:
        stream_usage_mode: no_option_usage_chunk
```

- [ ] **Step 5: Run the config test**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_config.py::test_config_example_uses_current_local_providers -q
```

Expected: PASS.

---

### Task 2: Add Stream Payload Policy And SSE Usage Parser

**Files:**
- Create: `token_router/app/providers/streaming.py`
- Create: `tests/test_streaming.py`

- [ ] **Step 1: Write failing tests for payload mutation**

Create `tests/test_streaming.py` with:

```python
from token_router.app.providers.streaming import (
    apply_stream_usage_policy,
    extract_usage_from_sse_bytes,
)


def test_openai_policy_adds_include_usage_when_absent():
    payload = {"model": "m", "stream": True}

    result = apply_stream_usage_policy(payload, "openai_include_usage")

    assert result["stream_options"] == {"include_usage": True}
    assert payload == {"model": "m", "stream": True}


def test_policy_preserves_client_stream_options():
    payload = {"model": "m", "stream": True, "stream_options": {"include_usage": False}}

    result = apply_stream_usage_policy(payload, "openai_include_usage")

    assert result["stream_options"] == {"include_usage": False}


def test_no_option_policy_does_not_add_stream_options():
    payload = {"model": "m", "stream": True}

    result = apply_stream_usage_policy(payload, "no_option_usage_chunk")

    assert "stream_options" not in result
```

- [ ] **Step 2: Write failing tests for SSE usage extraction**

Add to `tests/test_streaming.py`:

```python
def test_extract_usage_from_final_empty_choices_chunk():
    frame = (
        b'data: {"choices":[],"usage":'
        b'{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\\n\\n'
    )

    usage = extract_usage_from_sse_bytes(frame)

    assert usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_extract_usage_ignores_done_and_malformed_json():
    assert extract_usage_from_sse_bytes(b"data: [DONE]\\n\\n") is None
    assert extract_usage_from_sse_bytes(b"data: {bad-json}\\n\\n") is None


def test_extract_usage_from_bigmodel_style_final_chunk():
    frame = (
        b'data: {"choices":[{"finish_reason":"stop"}],"usage":'
        b'{"prompt_tokens":4,"completion_tokens":6,"total_tokens":10}}\\n\\n'
    )

    usage = extract_usage_from_sse_bytes(frame)

    assert usage == {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}
```

- [ ] **Step 3: Run the failing tests**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_streaming.py -q
```

Expected: FAIL with `ModuleNotFoundError` because `token_router.app.providers.streaming` does not exist.

- [ ] **Step 4: Implement `streaming.py`**

Create `token_router/app/providers/streaming.py`:

```python
from __future__ import annotations

import json
from typing import Any, Literal


StreamUsageMode = Literal[
    "openai_include_usage",
    "ark_include_usage",
    "no_option_usage_chunk",
    "final_chunk_usage",
    "parse_only",
]


def apply_stream_usage_policy(
    payload: dict[str, Any], stream_usage_mode: StreamUsageMode
) -> dict[str, Any]:
    outgoing = dict(payload)
    if stream_usage_mode in {"openai_include_usage", "ark_include_usage"}:
        stream_options = dict(outgoing.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        outgoing["stream_options"] = stream_options
    return outgoing


def extract_usage_from_sse_bytes(chunk: bytes) -> dict[str, Any] | None:
    latest_usage = None
    for raw_line in chunk.splitlines():
        line = raw_line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage")
        if isinstance(usage, dict):
            latest_usage = usage
    return latest_usage
```

- [ ] **Step 5: Run streaming helper tests**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_streaming.py -q
```

Expected: PASS.

---

### Task 3: Add Provider Streaming Method

**Files:**
- Modify: `token_router/app/providers/openai_compatible.py`
- Modify: `tests/test_provider.py`

- [ ] **Step 1: Write failing provider streaming test**

Add to `tests/test_provider.py`:

```python
def test_provider_streams_raw_sse_bytes():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["json"] = request.read()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"OK"}}],"usage":null}\\n\\n'
                b"data: [DONE]\\n\\n"
            ),
        )

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        type="openai_compatible",
        base_url="https://example.test/v1",
        keys=[ApiKeyConfig(id="test", value="sk-test")],
    )

    chunks = asyncio.run(
        _collect_stream(
            provider.chat_completion_stream(
                config,
                config.keys[0],
                {"model": "model-a", "stream": True},
            )
        )
    )

    assert b"".join(chunks).endswith(b"data: [DONE]\\n\\n")
    assert captured["headers"]["authorization"] == "Bearer sk-test"
```

Also add this helper at module level:

```python
async def _collect_stream(stream):
    return [chunk async for chunk in stream]
```

- [ ] **Step 2: Run the failing provider test**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_provider.py::test_provider_streams_raw_sse_bytes -q
```

Expected: FAIL with `AttributeError` because `chat_completion_stream` does not exist.

- [ ] **Step 3: Implement provider stream method**

In `token_router/app/providers/openai_compatible.py`, add:

```python
from collections.abc import AsyncIterator
```

Add method:

```python
    async def chat_completion_stream(
        self,
        provider_config: EndpointConfig | ProviderConfig,
        api_key: ApiKeyConfig,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        url = f"{provider_config.base_url.rstrip('/')}/chat/completions"
        headers = self._headers(provider_config, api_key)
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_provider.py -q
```

Expected: PASS.

---

### Task 4: Stream From `/v1/chat/completions`

**Files:**
- Modify: `token_router/app/api/chat.py`
- Modify: `tests/test_chat_api.py`

- [ ] **Step 1: Write failing API test for SSE passthrough**

Add provider fake to `tests/test_chat_api.py`:

```python
class FakeStreamingProvider:
    async def chat_completion_stream(self, provider_config, api_key, payload):
        assert payload["stream"] is True
        assert payload["model"] == "model-a"
        assert "router" not in payload
        yield b'data: {"choices":[{"delta":{"content":"O"}}],"usage":null}\\n\\n'
        yield b'data: {"choices":[{"delta":{"content":"K"}}],"usage":null}\\n\\n'
        yield (
            b'data: {"choices":[],"usage":'
            b'{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\\n\\n'
        )
        yield b"data: [DONE]\\n\\n"
```

Add test:

```python
def test_chat_endpoint_streams_sse_and_records_usage(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeStreamingProvider(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "router": {"level": 1, "debug": True},
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-Router-Model"] == "model-a"
    assert body.endswith(b"data: [DONE]\\n\\n")
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.prompt_tokens == 3
    assert usage.completion_tokens == 2
    assert usage.total_tokens == 5
    assert usage.request_count == 1
```

- [ ] **Step 2: Write failing API test for stream without usage**

Add:

```python
class FakeStreamingProviderWithoutUsage:
    async def chat_completion_stream(self, provider_config, api_key, payload):
        yield b'data: {"choices":[{"delta":{"content":"OK"}}],"usage":null}\\n\\n'
        yield b"data: [DONE]\\n\\n"
```

Add test:

```python
def test_chat_endpoint_stream_counts_request_when_usage_missing(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeStreamingProviderWithoutUsage(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    usage = usage_manager.get_usage("test", "k1", "model-a", "2026-05-27")
    assert usage.total_tokens == 0
    assert usage.request_count == 1
```

- [ ] **Step 3: Write failing API test for non-streaming request with stream false**

Add:

```python
def test_chat_endpoint_stream_false_uses_json_path(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeProvider(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "router": {"level": 1},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["model"] == "model-a"
```

- [ ] **Step 4: Run failing API stream tests**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_chat_api.py::test_chat_endpoint_streams_sse_and_records_usage tests/test_chat_api.py::test_chat_endpoint_stream_counts_request_when_usage_missing tests/test_chat_api.py::test_chat_endpoint_stream_false_uses_json_path -q
```

Expected: FAIL because `chat.py` still calls `chat_completion()` and returns `JSONResponse`.

- [ ] **Step 5: Implement client-requested stream branch in `chat.py`**

In `token_router/app/api/chat.py`, import:

```python
from collections.abc import AsyncIterator
from fastapi.responses import JSONResponse, StreamingResponse
from token_router.app.providers.streaming import (
    apply_stream_usage_policy,
    extract_usage_from_sse_bytes,
)
```

Change return annotation to:

```python
) -> JSONResponse | StreamingResponse:
```

After building `outgoing_payload`, add this branch before the existing non-streaming provider call. This branch must be entered only for `stream: true`; requests without `stream` and requests with `stream: false` must continue through the existing JSON path.

```python
    if outgoing_payload.get("stream") is True:
        outgoing_payload = apply_stream_usage_policy(
            outgoing_payload,
            endpoint_config.stream_usage_mode,
        )
        stream = request.app.state.provider.chat_completion_stream(
            endpoint_config,
            api_key,
            outgoing_payload,
        )
        return StreamingResponse(
            _stream_and_record_usage(
                stream=stream,
                usage_manager=usage_manager,
                request_id=request_id,
                selected=selected,
                quota_date=quota_date,
                started_at=started_at,
            ),
            media_type="text/event-stream",
            headers=_router_headers(request_payload, selected),
        )
```

Move current debug header construction into:

```python
def _router_headers(request_payload: ChatCompletionRequest, selected) -> dict[str, str]:
    if not request_payload.router.get("debug"):
        return {}
    return {
        "X-Router-Provider": selected.provider,
        "X-Router-Endpoint": selected.endpoint,
        "X-Router-Key-Id": selected.key_id,
        "X-Router-Model": selected.model_name,
        "X-Router-Level": str(selected.level),
        "X-Router-Usage-Ratio": str(selected.usage_ratio),
        "X-Router-Stage": str(selected.stage),
    }
```

Add helper:

```python
async def _stream_and_record_usage(
    stream: AsyncIterator[bytes],
    usage_manager: UsageManager,
    request_id: str,
    selected,
    quota_date: str,
    started_at: float,
) -> AsyncIterator[bytes]:
    latest_usage: dict[str, Any] | None = None
    status = "ok"
    error_message = None
    try:
        async for chunk in stream:
            usage = extract_usage_from_sse_bytes(chunk)
            if usage is not None:
                latest_usage = usage
            yield chunk
    except httpx.HTTPStatusError as exc:
        status = "error"
        error_message = str(exc)
        raise
    finally:
        usage = latest_usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
        usage_manager.record_usage(
            provider=selected.provider,
            key_id=selected.key_id,
            model_name=selected.model_name,
            quota_date=quota_date,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        latency_ms = int((perf_counter() - started_at) * 1000)
        usage_manager.log_request(
            request_id=request_id,
            provider=selected.provider,
            key_id=selected.key_id,
            model_name=selected.model_name,
            level=selected.level,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            status=status,
            error_message=error_message,
            latency_ms=latency_ms,
        )
```

- [ ] **Step 6: Run API stream tests**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_chat_api.py -q
```

Expected: PASS.

---

### Task 5: Preserve Upstream Errors For Streaming Requests

**Files:**
- Modify: `token_router/app/api/chat.py`
- Modify: `tests/test_chat_api.py`

- [ ] **Step 1: Write failing test for upstream stream HTTP error before response starts**

Add fake provider:

```python
class FakeStreamingProviderRaisesStatus:
    async def chat_completion_stream(self, provider_config, api_key, payload):
        response = httpx.Response(429, text="rate limited", request=httpx.Request("POST", "https://example.test/v1/chat/completions"))
        raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)
        yield b""
```

Add test:

```python
def test_chat_endpoint_stream_logs_upstream_status_error(
    app_config, usage_manager, fixed_now
):
    app = create_app(
        app_config,
        usage_manager,
        provider=FakeStreamingProviderRaisesStatus(),
        now_fn=lambda: fixed_now,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "router": {"level": 1},
        },
    )

    assert response.status_code == 429
```

- [ ] **Step 2: Run failing test**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_chat_api.py::test_chat_endpoint_stream_logs_upstream_status_error -q
```

Expected: FAIL if the exception escapes after `StreamingResponse` starts or appears as a server error.

- [ ] **Step 3: Implement preflight stream open if needed**

If the test fails because exceptions happen after response startup, change the provider interface to return an async context object opened before `StreamingResponse` is constructed. The minimal acceptable implementation is to add provider method `open_chat_completion_stream()` that returns an object with `aiter_bytes()` and closes in the endpoint generator.

Keep the public endpoint behavior:

- Upstream HTTP error before first byte returns matching HTTP status.
- Error request is logged with zero usage.
- No partial SSE is emitted for pre-first-byte errors.

- [ ] **Step 4: Run chat API tests**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest tests/test_chat_api.py -q
```

Expected: PASS.

---

### Task 6: Update Docs And Manual Smoke Tests

**Files:**
- Modify: `README.md`
- Modify: `README-CN.md`
- Modify: `docs/provider-streaming-compatibility.md`

- [ ] **Step 1: Document supported stream behavior**

Add to README Chat Completions section:

```markdown
### Streaming

The router supports `stream: true` for OpenAI-compatible SSE responses. It forwards upstream `data:` frames as `text/event-stream` and records token usage when the provider emits a non-null `usage` object. If the stream finishes without usage, the request count is still recorded and token usage is recorded as zero.
```

- [ ] **Step 2: Document local stream curl**

Add:

```bash
curl --no-buffer http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": true,
    "router": {"level": 1, "debug": true}
  }'
```

Expected success signal in docs:

- Response content type starts with `text/event-stream`.
- Output includes one or more `data:` frames.
- Final frame is `data: [DONE]`.
- `/admin/usage` increments request count.

- [ ] **Step 3: Update provider compatibility doc**

In `docs/provider-streaming-compatibility.md`, add an "Implementation status" section:

```markdown
## Implementation Status

The local router implements SSE passthrough for `stream=true`, provider-specific request mutation through `stream_usage_mode`, and best-effort usage accounting from non-null streaming `usage` chunks. Real provider smoke tests are still manual because they require credentials and external APIs.
```

- [ ] **Step 4: Run documentation grep checks**

Run:

```bash
rg -n "stream: true|stream_usage_mode|StreamingResponse|Requests / Quota" README.md README-CN.md docs/provider-streaming-compatibility.md token_router tests
```

Expected: output includes README streaming examples, config stream modes, endpoint implementation, and tests.

---

### Task 7: Final Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run full test suite**

Run:

```bash
/opt/miniconda3/envs/llm_token_router/bin/python -m pytest -q
```

Expected: all tests pass. Existing Starlette/httpx deprecation warning is acceptable if no new warnings appear.

- [ ] **Step 2: Verify ignored local config stays untracked**

Run:

```bash
git status --short
```

Expected: `config.yaml` is not listed. The stream implementation files and docs are listed as modified or staged according to the current workflow.

- [ ] **Step 3: Provide manual smoke test commands to user**

Do not run real provider tests. Provide these commands:

```bash
curl --no-buffer http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/free",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": true,
    "router": {"provider": "openrouter", "level": 5, "fallback": false, "debug": true}
  }'
```

Success signal:

- `data:` chunks appear progressively.
- Final frame is `data: [DONE]`.
- `curl -s http://127.0.0.1:8000/admin/models` shows the OpenRouter key request count incremented.

---

## Self-Review Notes

- Spec coverage: Covers config, provider streaming, payload mutation, SSE parsing, endpoint response, usage accounting, docs, tests, and manual smoke tests.
- Placeholder scan: No placeholder tasks remain; all tests and implementation snippets are concrete.
- Type consistency: `stream_usage_mode` is used consistently across config, payload policy, endpoint config, and docs.
