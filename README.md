# Local LLM Token Router

本项目是一个本地自用的 LLM 网关，用来把 OpenAI 兼容的 Chat Completions 请求，按模型等级、每日 token 配额和 25% 用量阶段路由到不同模型实例。

当前 MVP 目标是本地个人使用，不包含 Web 管理后台、用户鉴权、Redis 锁、复杂重试和流式响应。

## 快速开始

```bash
python -m pip install -e ".[dev]"
cp config.example.yaml config.yaml
cp .env.example .env
```

编辑 `.env`，填入你当前的小米 MiMo Token/Coding Plan key 和火山方舟 key：

```bash
MIMO_TOKEN_PLAN_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
MIMO_TOKEN_PLAN_KEY=tp-...
MIMO_TOKEN_PLAN_MODEL=mimo-v2.5-pro

VOLCENGINE_ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
VOLCENGINE_ARK_API_KEY=...
VOLCENGINE_ARK_MODEL=doubao-seed-2-0-lite-260215
```

`load_config()` 会先读取 `config.yaml` 同目录下的 `.env`，再解析配置里的 `${VAR_NAME}`。如果同名变量已经存在于 shell 环境变量中，shell 里的值优先。

`config.example.yaml` 默认只启用两个供应商：

- `xiaomi_mimo`
- `volcengine_ark`

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

## 启动服务

```bash
uvicorn token_router.app.main:app --reload
```

默认读取：

- 配置文件：`config.yaml`
- SQLite 数据库：`token_router.sqlite3`

可以用环境变量覆盖：

```bash
export TOKEN_ROUTER_CONFIG=/path/to/config.yaml
export TOKEN_ROUTER_DB=/path/to/token_router.sqlite3
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

## 配置维护

本地配置主要在两个文件里：

- `config.yaml`：供应商、endpoint、模型实例、额度和路由配置。
- `.env`：真实 URL、API key 和模型名。

这两个文件都只保留在本地，已经被 Git 忽略。

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
    key_id: mimo_token_plan_2
    level: 1
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
    key_id: volcengine_ark_1
    level: 2
    daily_quota: 10000000
    priority: 30
    groups: [general]
```

字段说明：

- `name`：上游平台真实模型名。
- `provider`：`providers` 下面的供应商名。
- `endpoint`：该供应商下面的 URL/key 池。
- `key_id`：该 endpoint 下面的 key。
- `level`：等级，数字越小优先级越高，`1` 最高。
- `daily_quota`：这个模型实例每天可用 token 额度。
- `priority`：同等级、同阶段内的排序，数字越小越优先。
- `groups`：可选标签，例如 `coding`、`general`、`reasoning`、`fallback`。

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

## 当前限制

- 暂不支持流式响应。
- Provider adapter 默认面向 OpenAI 兼容接口。
- API key 从本地 `.env` 和 `config.yaml` 读取。
- 暂无 Web UI、用户鉴权、Redis 锁、失败重试和 cooldown 策略。

## 测试

```bash
python -m pytest -v
```
