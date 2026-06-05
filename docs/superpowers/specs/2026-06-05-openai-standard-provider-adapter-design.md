# OpenAI Standard Provider Adapter Design

## Goal

Let clients call the local router with standard OpenAI Chat Completions parameters while preserving provider-specific behavior after the router selects a model.

## Assumptions

- "Unspecified provider" means `router.provider` is absent, `null`, or `"auto"`.
- Explicit provider calls use `router.provider` with a non-`auto` value and should keep the current pass-through behavior.
- The router should not invent request policy. It should translate client-provided standard fields, not add `store`, `reasoning_effort`, or `max_completion_tokens` by default.
- `stream_options` is valid only for streaming requests. Non-streaming requests should not send it upstream after automatic routing.
- Existing `router.thinking` remains the explicit router-native override and takes precedence over top-level OpenAI-style reasoning fields.

## Current Behavior

`ChatCompletionRequest` accepts extra fields and the chat endpoint forwards the request body after replacing `model` with the selected upstream model and removing `router`. The only provider-specific translation today is `router.thinking`, and streaming usage options are handled only when `stream: true`.

This already allows clients to send OpenAI-style fields, but unsupported standard fields may be forwarded to providers that expect private equivalents or reject unknown parameters.

## Design

Add one narrow adapter in `token_router/app/api/chat.py` after route selection and before provider invocation:

```python
_adapt_openai_standard_params(outgoing_payload, request_payload.router, selected)
```

The adapter should run only when `router.provider` is absent, `None`, or `"auto"`. It should leave explicit provider calls unchanged.

Automatic routing rules:

- `max_tokens`: if `max_completion_tokens` is absent, rename `max_tokens` to `max_completion_tokens`; if both are present, keep `max_completion_tokens` and drop `max_tokens`.
- `stream_options`: remove it when `stream` is not `true`; when `stream` is `true`, keep it only for endpoints whose `stream_usage_mode` is `openai_include_usage` or `ark_include_usage`.
- `store`: preserve the client value.
- `reasoning_effort`: translate by final provider:
  - `openrouter`: move to `reasoning.effort` and remove `reasoning_effort`.
  - `volcengine_ark`: keep it only for `doubao-seed-2-0*`; otherwise remove it.
  - `xiaomi_mimo`: convert to `thinking.type=enabled` and remove `reasoning_effort`.
  - unknown providers: preserve it.
- `router.thinking`: preserve existing behavior and precedence over `reasoning_effort`.

## Non-Goals

- No migration to the Responses API.
- No new provider config schema for request-parameter capabilities.
- No real provider smoke tests in the agent session because they need local services, ports, credentials, or external APIs.
- No default request policy injection.

## Testing

Unit/API tests should verify:

- Auto provider routing converts `max_tokens` to `max_completion_tokens`.
- Auto provider routing removes non-streaming `stream_options` and removes streaming `stream_options` for no-option endpoints such as OpenRouter or Xiaomi MiMo.
- Auto OpenRouter routing converts `reasoning_effort` to `reasoning.effort`.
- Auto Xiaomi MiMo routing converts `reasoning_effort` to `thinking.type=enabled`.
- Explicit provider routing leaves standard fields untouched.
- Existing `router.thinking` tests still pass.

## Documentation

Update the Chinese client integration guide to recommend OpenAI-standard request fields for `model: "auto"` and document the pass-through boundary for explicit providers. Update the OpenAI SDK smoke-test example to use `max_completion_tokens`.
