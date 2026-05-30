# Provider Streaming Compatibility

Last researched: 2026-05-31 (Asia/Shanghai)

## Purpose

This document records how common OpenAI-compatible providers handle Chat Completions streaming so the router can add `stream=true` support later without losing token accounting.

Scope:

- Endpoint family: `POST /v1/chat/completions` or the provider's equivalent OpenAI-compatible Chat Completions endpoint.
- Transport: Server-Sent Events (SSE), usually `text/event-stream`.
- Accounting target: record final prompt, completion, and total token usage when the upstream stream exposes it.

No real provider calls were run for this document. The notes below come from official provider documentation or official SDK/API references, and runtime behavior should still be verified with a small credentialed smoke test before enabling a provider-specific stream strategy by default.

## Overall Router Strategy

For MVP streaming support:

1. Forward OpenAI-compatible SSE chunks to the client as-is.
2. Parse only `data:` frames in parallel for accounting.
3. Keep the latest non-null `usage` object seen in the stream.
4. Record usage once, after the upstream stream ends or the client disconnect path is handled.
5. If no non-null `usage` was seen, record the request log and use `0` tokens for usage.
6. Do not estimate tokens with a tokenizer in the MVP.

`stream_options` should not be injected blindly for every provider. Several providers support standard `stream_options.include_usage`, but some either document a different behavior or do not document `stream_options` at all. A later implementation should add provider-level stream usage policy, for example:

```yaml
stream_usage:
  mode: openai_include_usage
```

Suggested modes:

- `openai_include_usage`: add `stream_options.include_usage=true` if the client did not provide it.
- `ark_include_usage`: add `stream_options.include_usage=true`; optionally allow `chunk_include_usage=true` for cumulative per-chunk usage when explicitly configured.
- `final_chunk_usage`: do not add `stream_options`; parse `usage` from the final normal chunk.
- `no_option_usage_chunk`: do not add `stream_options`; parse an extra final `choices: []` usage chunk before `[DONE]`.
- `parse_only`: do not add provider-specific parameters; record any `usage` that appears.

## Platform Matrix

| Provider                         | Base URL / endpoint                                                                                                                                                                  | Auth                                                   | Stream parameter | Usage parameter                                                                              | Usage chunk behavior                                                                                                                                                                     | OpenAI-style SSE              | Source                                                                                                                                                                                                                                                    | Router recommendation                                                                                                                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ | ---------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| OpenAI                           | `https://api.openai.com/v1/chat/completions`                                                                                                                                       | `Authorization: Bearer`                              | `stream: true` | `stream_options.include_usage: true`                                                       | Extra chunk before `data: [DONE]`; `choices` is empty and `usage` has full request totals. Other chunks include `usage: null`. If interrupted, final usage chunk may be missing. | Yes                           | [Streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events), [Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)          | Use `openai_include_usage`.                                                                                                                                            |
| Volcengine Ark                   | `https://ark.cn-beijing.volces.com/api/v3/chat/completions`                                                                                                                        | `Authorization: Bearer`                              | `stream: true` | `stream_options.include_usage: true`; also supports `stream_options.chunk_include_usage` | `include_usage=true` returns an extra final usage chunk before `[DONE]`; `chunk_include_usage=true` returns cumulative usage in each chunk. Defaults are false.                    | Yes                           | [Chat API](https://www.volcengine.com/docs/82379/1298454), [Streaming output](https://www.volcengine.com/docs/82379/2123275), [Volcengine Go SDK StreamOptions](https://pkg.go.dev/github.com/volcengine/volcengine-go-sdk/service/arkruntime/model#StreamOptions) | Use `ark_include_usage` with only `include_usage=true` by default. Avoid `chunk_include_usage=true` unless the user explicitly wants cumulative per-chunk usage.   |
| Alibaba DashScope / Model Studio | China:`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`; international examples use `https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions` | `Authorization: Bearer`                              | `stream: true` | `stream_options.include_usage: true`                                                       | Final data block has `choices: []` and a populated `usage`; earlier chunks have `usage: null`; stream ends with `data: [DONE]`.                                                  | Yes                           | [Streaming output](https://www.alibabacloud.com/help/en/model-studio/stream), [OpenAI compatibility](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)                                                                  | Use `openai_include_usage`.                                                                                                                                            |
| DeepSeek                         | `https://api.deepseek.com/chat/completions`                                                                                                                                        | `Authorization: Bearer`                              | `stream: true` | `stream_options.include_usage: true`                                                       | Extra chunk before `[DONE]`; `choices` is always empty on that usage chunk; other chunks include `usage: null`. Thinking models may put `reasoning_content` in `delta`.        | Yes                           | [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)                                                                                                                                                                           | Use `openai_include_usage`.                                                                                                                                            |
| Zhipu BigModel / GLM             | `https://open.bigmodel.cn/api/paas/v4/chat/completions`                                                                                                                            | `Authorization: Bearer`                              | `stream: true` | No `stream_options` requirement found in official streaming guide                          | Official streaming example shows the final normal chunk has `finish_reason: "stop"` and a populated `usage`, then `data: [DONE]`.                                                  | Yes                           | [Streaming guide](https://docs.bigmodel.cn/cn/guide/capabilities/streaming), [Chat completions API](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8)                                                         | Use `final_chunk_usage`; do not auto-add `stream_options` unless tested.                                                                                             |
| SiliconFlow                      | `https://api.siliconflow.cn/v1/chat/completions` or `https://api.siliconflow.com/v1/chat/completions`                                                                            | `Authorization: Bearer`                              | `stream: true` | No documented `stream_options.include_usage` in the official pages checked                 | Official stream FAQ shows `data:` JSON chunks and `[DONE]` parsing, but does not document streaming token usage. API reference documents `usage` for non-streaming response.       | Yes                           | [Stream mode FAQ](https://docs.siliconflow.cn/cn/faqs/stream-mode), [Chat completions API](https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions_copy)                                                                                | Use `parse_only`; do not auto-add `stream_options` until a real smoke test confirms support.                                                                         |
| OpenRouter                       | `https://openrouter.ai/api/v1/chat/completions`                                                                                                                                     | `Authorization: Bearer`                              | `stream: true` | No `stream_options` required in the official API reference                                  | OpenRouter normalizes OpenAI-style chunks. Streaming usage is returned exactly once in the final chunk before `[DONE]`, with an empty `choices` array.                            | Yes                           | [API reference](https://openrouter.ai/docs/api/reference/overview), [Free Models Router](https://openrouter.ai/docs/cookbook/get-started/free-models-router-playground)                                                                                          | Use `no_option_usage_chunk`; `openrouter/free` can be configured as the lowest-priority free fallback model.                                                        |
| Ollama OpenAI compatibility      | `http://localhost:11434/v1/chat/completions`                                                                                                                                       | API key is required by many SDKs but ignored by Ollama | `stream: true` | `stream_options.include_usage` is listed as supported                                      | Official compatibility page lists `stream_options.include_usage` for `/v1/chat/completions`, but the exact usage chunk shape was not documented on the checked page.                 | Intended OpenAI compatibility | [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility), [Streaming](https://docs.ollama.com/capabilities/streaming)                                                                                                                           | Use `openai_include_usage` only after local version smoke test; otherwise `parse_only` is safer across Ollama versions.                                              |
| Xiaomi MiMo OpenAI API           | Official API docs:`https://api.xiaomimimo.com/v1/chat/completions`; current Token Plan configs use `https://token-plan-cn.xiaomimimo.com/v1/chat/completions`                    | `api-key` or `Authorization: Bearer`               | `stream: true` | No `stream_options` required in the official streaming example                             | Normal chunks contain `content` or `reasoning_content` deltas with `usage: null`; after the stop chunk, an extra `choices: []` chunk contains final `usage`, then `data: [DONE]`. | Yes                           | [Xiaomi MiMo Open Platform OpenAI API](https://platform.xiaomimimo.com/docs/en-US/api/chat/openai-api?target=request-body)                                                                                                                                   | Use `no_option_usage_chunk`; do not auto-add `stream_options`. Verify Token Plan endpoint behavior separately because its base URL/key path differs from the standard API docs. |

## SSE Parsing Notes

The router should treat SSE as a byte stream for client forwarding and as line-oriented text only for internal accounting:

```text
data: {"id":"...","object":"chat.completion.chunk","choices":[...],"usage":null}

data: {"id":"...","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}

data: [DONE]
```

Implementation notes:

- Forward every upstream chunk to the client before or while parsing; do not wait for full completion.
- Parse only complete `data:` frames.
- Ignore comments, blank lines, and non-JSON `data: [DONE]`.
- If JSON parsing fails for a frame, keep forwarding and skip accounting for that frame.
- If multiple non-null `usage` objects appear, keep the latest value. This handles Ark cumulative usage mode without double-counting.
- Treat `choices: []` as valid. OpenAI, DashScope, DeepSeek, and Ark use an empty `choices` array for the final usage chunk when `include_usage=true`.
- Treat a populated `usage` on a non-empty final chunk as valid. BigModel documents this shape.
- Do not require `usage.total_tokens` to equal `prompt_tokens + completion_tokens`; providers may include cached, reasoning, audio, image, or web-search details.

## Request Mutation Rules

When the downstream client sends `stream=true`:

1. Preserve a client-provided `stream_options` object.
2. If provider policy is `openai_include_usage` and `stream_options.include_usage` is absent, add it with `true`.
3. If provider policy is `ark_include_usage`, add `stream_options.include_usage=true` when absent and preserve any user-provided `chunk_include_usage`.
4. If provider policy is `final_chunk_usage`, `no_option_usage_chunk`, or `parse_only`, do not add `stream_options`.
5. If the client sends `stream=false` or omits `stream`, keep the current non-streaming behavior.

This avoids sending undocumented fields to providers such as SiliconFlow or Xiaomi MiMo, where unsupported parameters could cause a 400 response.

## Provider Details

### OpenAI-compatible include_usage providers

OpenAI, DashScope, DeepSeek, and Ark all document standard `stream_options.include_usage=true` semantics: the stream emits one extra usage chunk before `[DONE]`, with empty `choices` and full token usage. Ark additionally documents `chunk_include_usage`, which reports cumulative token usage on every chunk when enabled.

For these providers, the router can reliably recover usage when the stream reaches the final usage chunk. If the client disconnects or the upstream stream is interrupted, usage may still be missing.

### Final-chunk usage providers

BigModel's official streaming example shows `usage` on the last content chunk that also has `finish_reason: "stop"`, followed by `[DONE]`. The router parser should therefore look for `usage` on every chunk, not only on empty-choice chunks.

### No-option usage chunk providers

OpenRouter's official API reference says streaming usage is returned exactly once in the final chunk before `[DONE]`, with an empty `choices` array. No `stream_options` parameter is required for this behavior. The same shape applies when the request model is `openrouter/free`, OpenRouter's Free Models Router.

Xiaomi MiMo's official streaming example sends `stream: true` without `stream_options`. The stream contains content and `reasoning_content` deltas with `usage: null`, then a stop chunk with `usage: null`, then an extra `choices: []` chunk with final `usage` before `data: [DONE]`.

MiMo's usage object can include `completion_tokens_details.reasoning_tokens`, so the router should persist the standard prompt, completion, and total token fields while keeping the raw response usage available in request logs if later detail fields matter.

### Parse-only providers

SiliconFlow documents OpenAI-style streaming, but the checked official pages do not document a request parameter that guarantees final usage in streaming mode. The router should not inject `stream_options` for this provider until a real smoke test confirms support.

Ollama documents `stream_options.include_usage` as a supported request field, but local Ollama behavior can vary by version. Keep its policy configurable, and verify against the installed Ollama version before relying on streaming usage for quota enforcement.

## Smoke Test Commands For Users

These tests require real local services, provider keys, or external APIs, so they should be run manually in the target environment.

OpenAI-style usage chunk check:

```bash
curl --no-buffer "$BASE_URL/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": true,
    "stream_options": {"include_usage": true}
  }'
```

Success signal:

- The stream ends with `data: [DONE]`.
- Before `[DONE]`, at least one JSON chunk has a non-null `usage`.
- For OpenAI-style providers, the usage chunk usually has `choices: []`.

BigModel-style final chunk check:

```bash
curl --no-buffer "https://open.bigmodel.cn/api/paas/v4/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BIGMODEL_API_KEY" \
  -d '{
    "model": "'"$BIGMODEL_MODEL"'",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": true
  }'
```

Success signal:

- The final JSON chunk before `[DONE]` has non-null `usage`.

Xiaomi MiMo no-option usage chunk check:

```bash
curl --no-buffer "https://api.xiaomimimo.com/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "api-key: $MIMO_API_KEY" \
  -d '{
    "model": "mimo-v2.5-pro",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "max_completion_tokens": 128,
    "stream": true
  }'
```

Success signal:

- Normal chunks have `usage: null` and may stream either `delta.content` or `delta.reasoning_content`.
- The stop chunk still has `usage: null`.
- The next JSON chunk before `[DONE]` has `choices: []` and non-null `usage`.

Parse-only provider check:

```bash
curl --no-buffer "$BASE_URL/chat/completions" \
  -H "Content-Type: application/json" \
  -H "$AUTH_HEADER" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": true
  }'
```

Interpretation:

- If no chunk contains `usage`, quota accounting for that provider cannot rely on streaming usage yet.
- If adding `stream_options.include_usage=true` returns HTTP 400, keep the provider in `parse_only` mode.
- If adding `stream_options.include_usage=true` returns a final usage chunk, the provider can be promoted to `openai_include_usage`.
