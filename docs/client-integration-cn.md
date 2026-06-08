# 其它项目调用指南

本文面向调用方项目，说明如何把本地 `llm-token-router` 当作 OpenAI-compatible Chat Completions 服务使用。

## 前提

router 服务默认在后台用 nohup 启动，如未启动，需要先在本机启动：

```bash
cd /Users/wendy/code/python/llm-token-router
. .venv/bin/activate
python -m uvicorn token_router.app.main:app --host 127.0.0.1 --port 8000
```

调用方使用的基础地址：

```text
http://127.0.0.1:8000/v1
```

健康检查：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/health
```

成功时返回：

```json
{"status":"ok"}
```

当前 MVP 不校验调用方传入的 API key。OpenAI SDK 通常要求设置 `api_key`，调用方传任意占位值即可。真实上游供应商 key 由 router 自己从 `.env` 和 `config.yaml` 读取。

不要把当前服务直接暴露到不可信网络；它目前是本地自用网关，没有客户端鉴权。

## 最小 HTTP 调用

非流式请求：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [
      {"role": "user", "content": "用一句话解释什么是 GraphRAG。"}
    ]
  }'
```

`model: "auto"` 表示让 router 根据当前配置、等级、配额和优先级选择模型。

### 推荐的 OpenAI 标准参数

默认接入建议把本地 router 当成标准 OpenAI Chat Completions provider 调用，再由 router 在选好模型后做供应商适配：

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

流式请求才传 `stream_options`：

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
  "stream_options": {
    "include_usage": true
  },
  "router": {
    "level": 1,
    "provider": "auto",
    "fallback": true
  }
}
```

适配边界：

- `router.provider` 省略、为 `null` 或为 `"auto"` 时，router 会按最终选中的 provider 转换标准字段。
- 显式指定 `router.provider` 时，除 `router.thinking` 这个 router 私有开关外，其它请求字段按调用方意图透传给上游。
- `max_tokens` 是旧字段；自动路由时会改成 `max_completion_tokens`。新调用方应直接传 `max_completion_tokens`。
- 非流式请求里的 `stream_options` 会在自动路由时移除，避免发给不接受该字段的上游。
- `reasoning_effort` 会在自动路由时转成 OpenRouter 的 `reasoning.effort`，或转成 MiMo/Ark 的 `thinking.type`。如果同时传 `router.thinking`，以 `router.thinking` 为准。

## Responses API

调用方可以请求本地：

```text
POST http://127.0.0.1:8000/v1/responses
```

这个接口只做原生 Responses 代理：router 仍负责选模型、选 key、检查配额、记录 usage，但不会把 Responses 请求转换成 Chat Completions。只有配置了 `responses_api: native` 的 endpoint 会进入候选，当前是 `volcengine_ark` 和 `openrouter`。

示例：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "input": "Reply with OK.",
    "max_output_tokens": 64,
    "router": {
      "provider": "volcengine_ark",
      "level": 1,
      "fallback": true,
      "debug": true
    }
  }'
```

当前能力表：

| Provider | `/v1/responses` 状态 | 备注 |
| --- | --- | --- |
| `volcengine_ark` | 原生支持，已启用 | 方舟官方 Responses API 支持创建、查询、上下文、删除和流式响应。 |
| `openrouter` | 原生支持，已启用 | OpenRouter Responses API 仍是 beta，并且是 stateless；不要依赖服务端保存 `previous_response_id` 状态。 |
| `xiaomi_mimo` | 未启用 | 当前官方兼容文档只列出 Chat Completions endpoint。 |
| `agnes` | 未启用 | 当前可查文档只确认 OpenAI Chat Completions 兼容。 |

## OpenAI Python SDK

调用方项目安装 OpenAI SDK：

```bash
python -m pip install openai
```

非流式：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-router-client",
)

response = client.chat.completions.create(
    model="auto",
    messages=[
        {"role": "user", "content": "用一句话解释什么是 GraphRAG。"},
    ],
)

print(response.choices[0].message.content)
print(response.usage)
```

强制走指定 provider：

```python
response = client.chat.completions.create(
    model="mimo-v2.5-pro",
    messages=[
        {"role": "user", "content": "Reply with OK only."},
    ],
    extra_body={
        "router": {
            "provider": "xiaomi_mimo",
            "level": 1,
            "fallback": False,
        }
    },
)
```

流式：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-router-client",
)

usage = None
stream = client.chat.completions.create(
    model="auto",
    messages=[
        {"role": "user", "content": "用三句话介绍这个项目。"},
    ],
    stream=True,
    extra_body={
        "router": {
            "level": 1,
            "fallback": True,
        }
    },
)

for chunk in stream:
    if chunk.usage is not None:
        usage = chunk.usage
    if not chunk.choices:
        continue
    delta = chunk.choices[0].delta
    if delta.content:
        print(delta.content, end="", flush=True)

print()
print("usage:", usage)
```

流式响应中可能出现 `choices: []` 的 usage chunk，调用方需要先判断 `chunk.choices` 是否为空。

## OpenAI JavaScript SDK

调用方项目安装 SDK：

```bash
npm install openai
```

非流式：

```js
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8000/v1",
  apiKey: "local-router-client",
});

const response = await client.chat.completions.create({
  model: "auto",
  messages: [
    { role: "user", content: "用一句话解释什么是 GraphRAG。" },
  ],
});

console.log(response.choices[0]?.message?.content);
console.log(response.usage);
```

流式：

```js
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8000/v1",
  apiKey: "local-router-client",
});

let usage = null;
const stream = await client.chat.completions.create({
  model: "auto",
  messages: [
    { role: "user", content: "用三句话介绍这个项目。" },
  ],
  stream: true,
  router: {
    level: 1,
    fallback: true,
  },
});

for await (const chunk of stream) {
  if (chunk.usage) {
    usage = chunk.usage;
  }
  const content = chunk.choices?.[0]?.delta?.content;
  if (content) {
    process.stdout.write(content);
  }
}

console.log("\nusage:", usage);
```

如果当前 SDK 类型定义不允许直接传 `router`，可以改用普通 `fetch`，或者按 SDK 支持方式传额外 body 字段。

## 普通 fetch 调用

```js
const response = await fetch("http://127.0.0.1:8000/v1/chat/completions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "auto",
    messages: [
      { role: "user", content: "用一句话解释什么是 GraphRAG。" },
    ],
    router: {
      level: 1,
      fallback: true,
    },
  }),
});

if (!response.ok) {
  throw new Error(`${response.status} ${await response.text()}`);
}

const data = await response.json();
console.log(data.choices?.[0]?.message?.content);
```

## 流式 HTTP 调用

router 会把上游 SSE 原样转发给客户端：

```bash
curl --noproxy '*' -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "stream": true,
    "router": {
      "level": 1,
      "fallback": true
    }
  }'
```

成功信号：

- 响应 `content-type` 以 `text/event-stream` 开头。
- 输出包含多段 `data: {...}`。
- 最后以 `data: [DONE]` 结束。

如果上游 provider 在 stream 里返回 usage，router 会在 stream 结束后记录 token 用量；如果没有 usage，router 仍记录请求次数，token 记 0。

## router 参数

请求体里的 `router` 字段只被本地 router 使用，不会转发给上游 provider。

| 字段                   | 示例              | 含义                                             |
| ---------------------- | ----------------- | ------------------------------------------------ |
| `provider`           | `"xiaomi_mimo"` | 指定供应商；省略或传 `"auto"` 表示不限供应商。 |
| `level`              | `1`             | 起始模型等级；数值越小优先级越高。               |
| `fallback`           | `true`          | 当前等级无可用模型时，是否向后续等级降级。       |
| `max_fallback_level` | `5`             | 允许降级到的最高等级编号。                       |
| `strict_model`       | `true`          | 指定模型不可用时，是否禁止 fallback 到其它模型。 |
| `model_group`        | `"coding"`      | 只选择带有该 group 的模型实例。                  |
| `thinking`           | `true`          | 是否让 router 为选中的上游模型开启思考模式。     |
| `thinking_effort`    | `"high"`        | 思考强度；只在上游模型支持强度参数时转发。       |
| `debug`              | `true`          | 返回 `X-Router-*` 调试响应头。                 |

常用模式：

```json
{
  "model": "auto",
  "router": {
    "level": 1,
    "fallback": true
  }
}
```

`model: "auto"` 下开启思考模式：

```json
{
  "model": "auto",
  "router": {
    "level": 1,
    "fallback": true,
    "thinking": true,
    "thinking_effort": "high"
  }
}
```

关闭思考模式：

```json
{
  "model": "auto",
  "router": {
    "level": 1,
    "thinking": false
  }
}
```

思考参数规则：

- 不传 `router.thinking` 时，router 不注入思考参数，使用上游模型默认行为。
- `router.thinking: true` 时，router 会按最终选中的 provider/model 翻译参数。
- `router.thinking: false` 时，router 会按最终选中的 provider/model 传关闭参数。
- 如果请求体同时传了上游私有字段，例如顶层 `thinking` 或 `reasoning`，`router.thinking` 会覆盖这些同类字段。
- `thinking_effort` 推荐值为 `"low"`、`"medium"`、`"high"`；OpenRouter 还接受 `"minimal"`、`"none"`、`"xhigh"`。不支持强度的模型只会收到开关参数。

当前翻译规则：

| 选中的 provider/model | `thinking: true` | `thinking: false` | `thinking_effort` |
| --------------------- | ---------------- | ----------------- | ----------------- |
| `xiaomi_mimo`         | `thinking.type=enabled` | `thinking.type=disabled` | 不转发 |
| `volcengine_ark`      | `thinking.type=enabled` | `thinking.type=disabled` | 仅 `doubao-seed-2-0*` 转成 `reasoning_effort` |
| `openrouter`          | `reasoning.enabled=true`，或 `reasoning.effort=<value>` | `reasoning.effort=none` | 转成 `reasoning.effort` |

强制走 MiMo：

```json
{
  "model": "mimo-v2.5-pro",
  "router": {
    "provider": "xiaomi_mimo",
    "level": 1,
    "fallback": false,
    "strict_model": true
  }
}
```

强制走 Ark mini：

```json
{
  "model": "doubao-seed-2-0-mini-260428",
  "router": {
    "provider": "volcengine_ark",
    "level": 3,
    "fallback": false,
    "strict_model": true
  }
}
```

强制走 OpenRouter 免费兜底：

```json
{
  "model": "openrouter/free",
  "router": {
    "provider": "openrouter",
    "level": 5,
    "fallback": false,
    "strict_model": true
  }
}
```

## 调试路由结果

在请求里打开 debug：

```json
{
  "router": {
    "debug": true
  }
}
```

响应头会包含：

```text
X-Router-Provider
X-Router-Endpoint
X-Router-Key-Id
X-Router-Model
X-Router-Level
X-Router-Usage-Ratio
X-Router-Stage
```

也可以在不真实调用上游的情况下预览路由：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/admin/route/preview \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "router": {
      "level": 1,
      "fallback": true
    }
  }'
```

## 错误处理

调用方至少处理这些状态：

- `200`：请求成功。
- `429`：router 没有可用模型实例，通常是配额耗尽、request quota 达到上限，或筛选条件过窄。
- 上游状态码，例如 `400`、`401`、`403`、`500`：router 会把非流式上游 HTTP 错误转换成同状态码的错误响应。

流式请求的残余限制：如果上游在 stream 开始后才报错，HTTP 响应可能已经以 `text/event-stream` 开始，无法再优雅改成 JSON 错误。调用方应同时处理 stream 中断、连接关闭和非 2xx 初始响应。

## 用量查看

查看当前模型和配额状态：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/admin/models
```

打开本地用量页：

```text
http://127.0.0.1:8000/admin/usage
```

直接查 SQLite：

```bash
sqlite3 token_router.sqlite3 \
'SELECT provider_name, key_id, model_name, prompt_tokens, completion_tokens, total_tokens, request_count
 FROM model_usage_daily
 ORDER BY updated_at DESC;'
```

## 接入建议

- 调用方默认使用 `model: "auto"`，让 router 负责供应商选择和 fallback。
- 只有在需要压测、定位问题或控制成本时，再指定 `provider` / `level` / `strict_model`。
- 生产脚本里不要依赖 debug headers；debug headers 主要用于人工排查。
- stream 调用方必须支持 `choices: []` 的 final usage chunk。
- 当前服务适合作为同机或可信内网依赖，不适合裸露到公网。
