# Local LLM Token Router

本项目是一个本地自用的 LLM 网关，用来把 OpenAI 兼容的 Chat Completions 请求，按模型等级、每日 token 配额和 25% 用量阶段路由到不同模型实例。

当前 MVP 目标是本地个人使用，不包含 Web 管理后台、用户鉴权、Redis 锁和复杂重试。

## 快速开始

使用项目根目录下的专用虚拟环境 `.venv`。当前 `.venv` 已用
Python 3.13.13 验证通过；项目要求 Python 3.11 或更新版本。

```bash
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp config.example.yaml config.yaml
cp .env.example .env
```

编辑 `.env`，填入你当前的小米 MiMo Token/Coding Plan key、火山方舟 key、OpenRouter key 和 Tavily key：

```bash
MIMO_TOKEN_PLAN_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
MIMO_TOKEN_PLAN_KEY=tp-...
MIMO_TOKEN_PLAN_MODEL=mimo-v2.5-pro

ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_API_KEY=...
ARK_MODEL=doubao-seed-2-0-lite-260215

AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
AGNES_API_KEY=...
AGNES_MODEL=agnes-2.0-flash

OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=...
OPENROUTER_API_KEY_2=...
OPENROUTER_FREE_MODEL=openrouter/free

TAVILY_API_KEY=tvly-...
```

`load_config()` 会先读取 `config.yaml` 同目录下的 `.env`，再解析配置里的 `${VAR_NAME}`。如果同名变量已经存在于 shell 环境变量中，shell 里的值优先。

`config.example.yaml` 默认启用四个供应商：

- `xiaomi_mimo`
- `volcengine_ark`
- `agnes`
- `openrouter`

其它项目接入本地 router 时，可参考 [其它项目调用指南](docs/client-integration-cn.md)。

## 核心概念

`provider` 和 `endpoint` 是分开的：

- `provider` 表示供应商，例如 `xiaomi_mimo`。
- `endpoint` 表示这个供应商下面的一组具体 URL 和 key，例如 `token_plan`，或者后续新增的 `api`。
- `model_instances` 指向 `provider + endpoint + key_id + model`。

这对小米 MiMo 很重要，因为 Token/Coding Plan 的 URL/key 和普通供应商 API 的 URL/key 是两套东西，应该放在同一个供应商下面的不同 endpoint：

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

鉴权方式：

- `auth_header: authorization_bearer`：使用 `Authorization: Bearer <key>`。
- `auth_header: api_key`：使用 `api-key: <key>`。

## OpenRouter 免费兜底

示例配置使用 OpenRouter 官方的 Free Models Router：`openrouter/free`，而不是把模型页里的具体 `:free` 模型全部列进配置。这样 OpenRouter 免费模型列表变化时，本地配置不需要频繁维护。

AGNES 的 `agnes-2.0-flash` 被放在 OpenRouter 前的兜底等级，OpenRouter 模型实例仍然是最低兜底等级：

```yaml
model_instances:
  - name: ${OPENROUTER_FREE_MODEL}
    provider: openrouter
    endpoint: api
    level: 5
    keys:
      - key_id: openrouter_1
        daily_quota: 5000000
        daily_request_quota: 50
        priority: 100
      - key_id: openrouter_2
        daily_quota: 5000000
        daily_request_quota: 50
        priority: 110
    groups: [general, coding, fallback, free]
```

OpenRouter 使用 `Authorization: Bearer <key>`，对应配置里的 `auth_header: authorization_bearer`。默认配置用 `OPENROUTER_API_KEY` 生成 `openrouter_1`，用 `OPENROUTER_API_KEY_2` 生成 `openrouter_2`。OpenRouter 的可选 attribution headers 不是路由必需项，当前 provider adapter 不发送这些可选 header。

`daily_request_quota: 50` 对应 OpenRouter 免费模型每个 key 每天 50 次请求限制。`priority` 放在 key 条目上，所以 router 会先用 `openrouter_1`，第一个 key 达到请求额度后再切到 `openrouter_2`。

如果想强制本地请求走 OpenRouter：

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

OpenRouter 文档：

- [Free Models Router](https://openrouter.ai/docs/cookbook/get-started/free-models-router-playground)
- [API Quickstart](https://openrouter.ai/docs/quickstart)

## 启动服务

开发时前台运行，方便直接看日志：

```bash
. .venv/bin/activate
python -m uvicorn token_router.app.main:app --reload
```

本地长期自用时，可以放到后台运行。下面命令需要在项目根目录执行：

```bash
mkdir -p logs
nohup .venv/bin/python -m uvicorn token_router.app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  > logs/router.log 2>&1 &
echo $! > .router.pid
```

默认读取：

- 配置文件：`config.yaml`
- SQLite 数据库：`token_router.sqlite3`

可以用环境变量覆盖：

```bash
export TOKEN_ROUTER_CONFIG=/path/to/config.yaml
export TOKEN_ROUTER_DB=/path/to/token_router.sqlite3
```

检查服务是否启动成功：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/health
```

成功时返回：

```json
{"status":"ok"}
```

查看后台日志：

```bash
tail -f logs/router.log
```

停止后台服务：

```bash
kill "$(cat .router.pid)"
rm -f .router.pid
```

## 调试路由

预览会选择哪个模型实例：

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

查看模型状态和用量：

```bash
curl -s http://127.0.0.1:8000/admin/models
```

打开用量页面：

```text
http://127.0.0.1:8000/admin/usage
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
        "content": "用一段话解释 GraphRAG。"
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

### OpenAI 标准参数适配

默认建议调用方使用 `model: "auto"`，并传标准 OpenAI Chat Completions 参数：

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

router 会在选好模型后，把这些标准字段转换成最终 provider 支持的形态。Chat Completions 自动路由中，OpenRouter 使用 `reasoning.effort`，MiMo 使用 `thinking.type`，Ark 使用 `thinking.type`，并且仅对支持强度的 Ark Chat 模型保留顶层 `reasoning_effort`。显式指定 `router.provider` 时，除 `router.thinking` 外，标准字段保持透传，便于直接调试某个供应商。`stream_options` 只应随 `stream: true` 发送；自动路由的非流式请求会移除这个字段。

注意 Chat 和 Responses 的 Ark 字段不同：Ark Chat API 的思考强度是顶层 `reasoning_effort`，Ark Responses API 的思考强度是 `reasoning.effort`。当前 `/v1/responses` 是原生代理，不翻译 `router.thinking` / `router.thinking_effort`；需要控制 Responses 思考模式时，请直接传上游原生 `thinking` 和 `reasoning` 字段。

### Runtime fallback 与 cooldown

除本地配额外，router 还会处理上游运行时瞬时失败和 model/key route 并发满载：`400`、`401`、`403`、`429`、`5xx`、网络错误和超时会把当前 `(provider, endpoint, key_id, model)` 放入短暂 cooldown，并在本次请求内继续选择下一个可用 route；当前 model/key route 达到 `max_concurrency` 时也会跳过当前 route 继续选路。其它 `4xx` 错误会直接返回给调用方。

默认 cooldown 为 `routing.runtime_cooldown_seconds: 30`。非流式 Chat Completions 和原生 Responses 都支持 runtime fallback；流式请求只支持“首包前 fallback”，一旦已有 SSE chunk 发给客户端，就不会在同一个流里切换模型。

OpenAI-compatible 上游调用的 router 侧单次 attempt HTTP 超时为：非流式 1800 秒，流式 150 秒；触发超时时按上述 runtime fallback 规则处理。

可用 `router.fallback_models` 指定运行时 fallback 的模型顺序；它只在已经需要 fallback 时生效，不改变第一次正常选路，且列表内模型仍需通过 provider、level、model_group、能力、配额、响应格式和并发过滤：

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

### 流式响应

当客户端传 `stream: true` 时，router 会以 `text/event-stream` 返回 OpenAI 兼容 SSE，并把上游 `data:` frame 转发给客户端。若上游 stream 中出现非空 `usage`，router 会用最后一次看到的 usage 记录 token 用量；如果 stream 结束前没有 usage，也会记录 1 次请求，token 用量记 0。

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

成功信号：

- 响应 `content-type` 以 `text/event-stream` 开头。
- 输出包含 `data:` frame，并以 `data: [DONE]` 结束。
- `/admin/usage` 中对应 key 的请求次数会增加。

## Responses API

`POST /v1/responses` 只代理到上游原生 Responses API，不做 Chat Completions shim。router 会先按模型、provider、level、fallback 和配额选择路由，但只会选择 `responses_api: native` 的 endpoint；选中后移除本地 `router` 字段，把 `model` 替换成实际上游模型名，然后调用：

```text
{provider.base_url}/responses
```

流式请求会原样透传上游 Responses SSE。router 会从非流式 `usage.input_tokens/output_tokens`，或流式 `response.completed.response.usage` 中记录本地 usage；如果上游没有返回 usage，也会记录一次请求且 token 记 0。Responses 同样支持运行时 fallback 和 cooldown，但只会在标记为 `responses_api: native` 的候选 endpoint 之间切换。

当前配置里的 Responses 支持情况：

| Provider | 当前状态 | 说明 |
| --- | --- | --- |
| `volcengine_ark` | 支持，已启用 | 火山方舟官方提供 `POST https://ark.cn-beijing.volces.com/api/v3/responses`，支持流式和 `previous_response_id`。 |
| `openrouter` | 支持，已启用 | OpenRouter 官方提供 beta `/api/v1/responses`；它是 stateless，调用方需要自行带完整历史。 |
| `xiaomi_mimo` | 未启用 | 当前官方 OpenAI-compatible 文档只列出 `/v1/chat/completions`。 |
| `agnes` | 未启用 | 当前可查文档只确认 OpenAI Chat Completions 兼容。 |

## OpenAI SDK 调用示例

启动本地 router 后，可以用 OpenAI Python SDK 直接测试 OpenAI 兼容接口。这个脚本会请求本地 `http://127.0.0.1:8000/v1/chat/completions`，再打印模型回复、usage 和 `X-Router-*` headers。

```bash
. .venv/bin/activate
python examples/openai_chat_test.py
```

默认会请求：

```text
base_url: http://127.0.0.1:8000/v1
model: doubao-seed-2-0-mini-260428
provider: volcengine_ark
level: 3
```

本地 router 当前不校验客户端传入的 OpenAI API key，所以示例脚本会使用一个占位 key。真正的上游 key 仍然从 `.env` 和 `config.yaml` 读取。

成功时输出里应包含：

```text
router_headers.X-Router-Provider = volcengine_ark
router_headers.X-Router-Model = doubao-seed-2-0-mini-260428
response.message.content
response.usage.total_tokens
```

可以用环境变量覆盖测试目标：

```bash
ROUTER_MODEL=auto ROUTER_PROVIDER=volcengine_ark ROUTER_LEVEL=3 python examples/openai_chat_test.py
```

也可以测试指定模型：

```bash
ROUTER_MODEL=doubao-seed-2-0-mini-260428 \
ROUTER_PROVIDER=volcengine_ark \
ROUTER_LEVEL=3 \
python examples/openai_chat_test.py
```

如果脚本报连接失败，先检查后台服务：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/health
tail -n 80 logs/router.log
```

## 配置维护

本地配置主要在两个文件里：

- `config.yaml`：供应商、endpoint、模型实例、额度和路由配置。
- `.env`：真实 URL、API key 和模型名。

`config.yaml` 只包含非敏感值和 `${VAR_NAME}` 引用时可以纳入版本管理。`.env` 里放真实 key，需要继续保留在本地并被 Git 忽略。

每次修改 `config.yaml` 或 `.env` 后，需要重启 `uvicorn`，让服务重新加载配置。

### 增加 API Key

先在 `.env` 里增加密钥：

```bash
MIMO_TOKEN_PLAN_KEY_2=tp-another-key
```

再在 `config.yaml` 的对应 endpoint 下增加 key：

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

然后增加一个使用这个 key 的模型实例：

```yaml
model_instances:
  - name: ${MIMO_TOKEN_PLAN_MODEL}
    provider: xiaomi_mimo
    endpoint: token_plan
    level: 1
    keys:
      - key_id: mimo_token_plan_2
        daily_quota: 50000000
        priority: 10
    groups: [coding, general]
```

同一个 endpoint 下的 `key_id` 必须唯一。

### 删除 API Key

需要同时删除两类引用：

- 删除 `providers.<provider>.endpoints.<endpoint>.keys` 下面的 key。
- 删除或修改所有使用这个 `key_id` 的 `model_instances`。

服务启动时会校验配置。如果某个模型实例指向不存在的 key，配置加载会失败。

### 增加模型

增加一个 `model_instances` 条目：

```yaml
model_instances:
  - name: new-model-name
    provider: volcengine_ark
    endpoint: api
    level: 2
    max_concurrency: 4
    keys:
      - key_id: volcengine_ark_1
        daily_quota: 10000000
        priority: 30
    groups: [general]
    unsupported_response_format_types: []
```

字段说明：

- `name`：上游平台真实模型名。
- `provider`：`providers` 下面的供应商名。
- `endpoint`：该供应商下面的 URL/key 池。
- `level`：等级，数字越小优先级越高，`1` 最高。
- `max_concurrency`：每个 model/key route 允许的最大在途请求数；同一模型的不同 key 会各自拥有这个并发上限，某个 route 满载时 router 会跳过它并选择其它可用 route。
- `keys[].key_id`：该 endpoint 下面的 key。
- `keys[].daily_quota`：这个模型/key 实例每天可用 token 额度。
- `keys[].daily_request_quota`：可选的每日请求次数额度。
- `keys[].priority`：同等级、同阶段内的排序，数字越小越优先。
- `groups`：可选标签，例如 `coding`、`general`、`reasoning`、`fallback`。
- `unsupported_response_format_types`：可选的响应格式过滤列表，例如 `[json_object]`；请求该格式时会跳过这个模型实例。

### 禁用或删除模型

临时禁用模型实例：

```yaml
model_instances:
  - name: new-model-name
    provider: volcengine_ark
    endpoint: api
    key_id: volcengine_ark_1
    enabled: false
```

永久删除时，直接删掉对应的 `model_instances` 条目。

### 增加供应商

先增加 provider、endpoint 和 key：

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

再在 `.env` 里增加变量：

```bash
NEW_SUPPLIER_BASE_URL=https://example.com/v1
NEW_SUPPLIER_API_KEY=sk-...
```

最后增加指向 `new_supplier/api/new_supplier_1` 的模型实例。

`stream_usage_mode` 只控制客户端传 `stream: true` 时如何做流式 usage 记账，不会强制开启 stream。可以放在 provider 上作为默认值；如果某个 endpoint 行为不同，再在 endpoint 下覆盖。

### 给已有供应商增加新 URL

当 URL、key 类型或额度体系不同时，应该增加一个新的 endpoint。比如小米 MiMo Token Plan 和普通 API 应该分开：

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

然后给每个 endpoint/key 单独配置 `model_instances`。

### 修改 URL

优先只改 `.env`：

```bash
MIMO_TOKEN_PLAN_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
```

如果新 URL 对应的是另一套 key 或鉴权方式，建议新建 endpoint，而不是直接改旧 endpoint。这样历史用量和路由行为更容易理解。

### 检查配置是否生效

重启服务后，先预览路由：

```bash
curl -s http://127.0.0.1:8000/admin/route/preview \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","router":{"level":1,"debug":true}}'
```

再看模型状态：

```bash
curl -s http://127.0.0.1:8000/admin/models
```

查看 API Key 用量页面：

```text
http://127.0.0.1:8000/admin/usage
```

## 当前限制

- Provider adapter 默认面向 OpenAI 兼容接口。
- API key 从本地 `.env` 和 `config.yaml` 读取。
- 暂无 Web UI、用户鉴权和 Redis 锁；runtime cooldown 仅保存在当前进程内。

## 每日模型质量评测

每日评测会用固定 Tavily query 拉取每日新闻，`topic` 分别为 `general`、`news`、`finance`，再让 `config.yaml` 里启用的 `model_instance + key_id` 基于同一批热点材料生成中文总结。`volcengine_ark` 已从每日评测中排除；正常路由仍可继续使用这些模型。

手动运行：

```bash
. .venv/bin/activate
python scripts/daily_model_eval.py
```

各模型/key 会并发评测，默认并发数是 `4`。可以用环境变量覆盖：

```bash
DAILY_EVAL_CONCURRENCY=6 python scripts/daily_model_eval.py
```

Router 服务启动时，如果配置了 `TAVILY_API_KEY`，会同时启动后台调度器。使用常规服务启动命令后，评测会按 `config.refresh.timezone` 的每天 `00:00` 自动运行：

```bash
nohup .venv/bin/python -m uvicorn token_router.app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  > logs/router.log 2>&1 &
```

后台任务会复用 `DAILY_EVAL_MAX_TOKENS`、`DAILY_EVAL_CONCURRENCY` 和 `TOKEN_ROUTER_REPORTS_DIR`。如果缺少 `TAVILY_API_KEY`，Web 服务仍会启动，但日志会提示每日评测未启用。

输出文件：

- `reports/daily-model-eval/YYYY-MM-DD/report.md`
- `reports/daily-model-eval/YYYY-MM-DD/results.jsonl`
- `reports/daily-model-eval/YYYY-MM-DD/tavily.json`
- `reports/daily-model-eval/latest.json`

成功调用会通过现有 SQLite 用量表计入 Model Instances，所以 `/admin/models`、`/admin/usage` 和后续路由都会看到这部分 token 消耗。失败调用只写入 `request_logs`，不计入每日 token quota。

查看最新日报：

```text
http://127.0.0.1:8000/
```

这个页面只读取已保存的日报文件，刷新页面不会调用 Tavily 或模型。

## 测试

```bash
. .venv/bin/activate
python -m pytest -v
```
