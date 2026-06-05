# OpenAI Standard Provider Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt standard OpenAI Chat Completions request parameters after automatic model routing while keeping explicit provider calls pass-through.

**Architecture:** Add a focused request adapter in `token_router/app/api/chat.py` after the selected route is known. The adapter runs only when `router.provider` is absent, `None`, or `"auto"` and only mutates fields whose safe provider mapping is known.

**Tech Stack:** FastAPI, Pydantic v2 extra fields, pytest, existing fake provider tests.

---

## File Structure

- Modify `token_router/app/api/chat.py`: add `_adapt_openai_standard_params`, provider detection, and small provider-specific helpers.
- Modify `tests/test_chat_api.py`: add regression tests for automatic adaptation and explicit pass-through.
- Modify `docs/client-integration-cn.md`: document OpenAI-standard auto calls and explicit-provider pass-through.
- Modify `examples/openai_chat_test.py`: use `max_completion_tokens` through `extra_body` instead of deprecated `max_tokens`.

## Task 1: Lock Request Adaptation Behavior

**Files:**
- Modify: `tests/test_chat_api.py`

- [ ] **Step 1: Write failing tests**

Add tests covering:

```python
def test_chat_endpoint_adapts_standard_openai_params_for_auto_openrouter(...):
    # provider selected by router is openrouter
    # request has max_tokens, reasoning_effort, stream_options, store
    # assert upstream payload has max_completion_tokens, reasoning.effort, store
    # assert upstream payload no longer has max_tokens, stream_options, reasoning_effort

def test_chat_endpoint_preserves_standard_params_when_provider_is_explicit(...):
    # request has router.provider = openrouter
    # assert upstream payload keeps max_tokens, reasoning_effort, stream_options
    # assert no automatic reasoning object was added

def test_chat_endpoint_adapts_reasoning_effort_for_mimo_auto_model(...):
    # provider selected by router is xiaomi_mimo
    # assert upstream payload has thinking.type = enabled
    # assert reasoning_effort is removed
```

- [ ] **Step 2: Verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_chat_api.py -q
```

Expected: the new tests fail because automatic standard-parameter adaptation is not implemented.

## Task 2: Implement The Adapter

**Files:**
- Modify: `token_router/app/api/chat.py`

- [ ] **Step 1: Add the adapter call**

Call the adapter after `router` is removed and before `_apply_router_thinking_options`:

```python
outgoing_payload.pop("router", None)
_adapt_openai_standard_params(outgoing_payload, request_payload.router, selected)
_apply_router_thinking_options(outgoing_payload, request_payload.router, selected)
```

- [ ] **Step 2: Add minimal helper behavior**

Implement:

```python
def _adapt_openai_standard_params(
    outgoing_payload: dict[str, Any],
    router_options: dict[str, Any],
    selected: SelectedRoute,
) -> None:
    provider = router_options.get("provider")
    if provider not in (None, "auto"):
        return

    if "max_tokens" in outgoing_payload:
        outgoing_payload.setdefault("max_completion_tokens", outgoing_payload["max_tokens"])
        outgoing_payload.pop("max_tokens", None)

    if outgoing_payload.get("stream") is not True:
        outgoing_payload.pop("stream_options", None)
    elif endpoint_config.stream_usage_mode not in {"openai_include_usage", "ark_include_usage"}:
        outgoing_payload.pop("stream_options", None)

    if "reasoning_effort" not in outgoing_payload or "thinking" in router_options:
        return

    effort = outgoing_payload["reasoning_effort"]
    if selected.provider == "openrouter":
        outgoing_payload["reasoning"] = {"effort": effort}
        outgoing_payload.pop("reasoning_effort", None)
        return

    if selected.provider == "xiaomi_mimo":
        outgoing_payload["thinking"] = {"type": "enabled"}
        outgoing_payload.pop("reasoning_effort", None)
        return

    if selected.provider == "volcengine_ark" and not selected.model_name.startswith("doubao-seed-2-0"):
        outgoing_payload.pop("reasoning_effort", None)
```

- [ ] **Step 3: Verify green**

Run:

```bash
.venv/bin/python -m pytest tests/test_chat_api.py tests/test_streaming.py -q
```

Expected: all selected tests pass.

## Task 3: Update User-Facing Docs And Example

**Files:**
- Modify: `docs/client-integration-cn.md`
- Modify: `examples/openai_chat_test.py`

- [ ] **Step 1: Document the preferred auto request body**

Add a section showing:

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Reply with OK."}],
  "store": false,
  "max_completion_tokens": 512,
  "reasoning_effort": "medium",
  "stream": true,
  "stream_options": {"include_usage": true},
  "router": {"level": 1, "provider": "auto", "fallback": true}
}
```

Explain that `stream_options` should be omitted unless `stream: true`.

- [ ] **Step 2: Document explicit-provider pass-through**

Add bullets stating explicit `router.provider` calls are treated as provider-specific calls and request fields are not normalized by the router.

- [ ] **Step 3: Update the SDK smoke test**

Replace `max_tokens=` with:

```python
extra_body={
    "router": router_options,
    "max_completion_tokens": int(os.getenv("ROUTER_MAX_COMPLETION_TOKENS", "160")),
}
```

## Task 4: Final Verification

**Files:**
- All modified files

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_chat_api.py tests/test_streaming.py -q
```

- [ ] **Step 2: Run broader tests if focused tests pass**

Run:

```bash
.venv/bin/python -m pytest -q
```

- [ ] **Step 3: Inspect diff**

Run:

```bash
git diff -- token_router/app/api/chat.py tests/test_chat_api.py docs/client-integration-cn.md examples/openai_chat_test.py docs/superpowers/specs/2026-06-05-openai-standard-provider-adapter-design.md docs/superpowers/plans/2026-06-05-openai-standard-provider-adapter.md
```

Expected: only the adapter, tests, and documentation changed.
