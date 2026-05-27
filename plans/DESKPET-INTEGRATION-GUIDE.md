# Token Relay × DeskPet 集成对接文档

> **版本**：v1.0 · **生效日期**：2026-05-20 · **生产环境**：`https://your-llm-relay.example.com`
> **协议版本**：所有面向集成方的端点挂在 `/v1/*`
> **对接对象**：DeskPet 本地 Python 后端
>
> 本文档是基于你方提交的 `RELAY-INTEGRATION-REQUEST.md` 的逐项确认 + 完整对接细节。
> 任何端点不一致以**本文档**为准；本文档的来源是已部署到 `your-llm-relay.example.com` 的生产代码。

---

## 0. TL;DR — 5 步跑通

```
1. POST  /v1/auth/register          → { access_token, refresh_token, activation }
2. POST  /api-direct/auth/activate  → 用 activation.token 激活账号（见 §3.2 注解）
3. POST  /v1/auth/login             → 拿新 access_token + refresh_token
4. GET   /v1/providers              → 自动签发 device key（tsk_xxx），返回 OpenAI 兼容 base_url
5. POST  /v1/chat/completions       → 用 device key 直接打 LLM（OpenAI SDK 即可）
```

每次步骤 4 都会**自动作废上一把 device key 并签发新的**，所以最简策略是：每次启动 DeskPet 都先调 `/v1/providers` 拿最新 key。

---

## 1. 基础信息

| 项 | 值 |
|---|---|
| 生产 base URL | `https://your-llm-relay.example.com` |
| 协议 | HTTPS only（Let's Encrypt 自动续期） |
| API 版本化 | 路径前缀 `/v1/` |
| 内容类型 | `application/json`（除特别说明） |
| 鉴权 | `Authorization: Bearer <token>` |
| 沙盒环境 | ⏳ 暂无（已记入 followups，可用生产 + 测试账号联调） |

**Caddy 路由分发**（你方只需要知道路径前缀）：
- `/v1/*`、`/anthropic/*`、`/public/*` → Nest 后端（**你方所有调用走这里**）
- `/api/*` → Next.js BFF（仅 web 控制台用，你方**不要走这里**）

---

## 2. 鉴权机制总览

两层凭据，互相解耦：

### 2.1 用户会话凭据（access_token + refresh_token）

| 字段 | 形态 | 有效期 | 用途 |
|---|---|---|---|
| `access_token` | HMAC-SHA256 三段式（`{alg:HS256,typ:JWT}` 头风格） | **1 小时** | `Authorization: Bearer …` 调用 `/v1/me`、`/v1/providers`、`/v1/usage/summary` 等账户级端点 |
| `refresh_token` | 不透明随机串 `rt_...`（base64url，~256 bit） | **30 天** | 换新 access；服务端只存 SHA-256 哈希 |

- access 失效（401 `EXPIRED_TOKEN`）→ 调 `/v1/auth/refresh` 换一对新的；旧 refresh 立即作废（rotation）
- refresh 也过期或被吊销 → 弹出登录框，重新走 `/v1/auth/login`
- `/v1/auth/logout` 撤销该用户**所有** refresh_token

### 2.2 LLM 调用凭据（device key, `tsk_xxx`）

| 字段 | 形态 | 有效期 | 用途 |
|---|---|---|---|
| `api_key` | `tsk_` + 48 字符随机十六进制 | 直到下一次 `GET /v1/providers` 或用户在 console 撤销 | `Authorization: Bearer tsk_xxx` 调 `/v1/chat/completions`、`/anthropic/v1/messages`、`/v1/images/generations`、`/v1/videos/tasks` |

- **自动轮换**：每次 `GET /v1/providers` 调用时，**该设备的旧 device key 立刻 disabled**，新 key 在响应里以**明文**返回。客户端责任：拿到就用，过期再调 `/v1/providers`。
- **同账号、多设备**：通过 `X-Device-Id` 请求头区分，每个 deviceId 独占一把 device key（互不干扰）。
- 用户可在 `/console/devices` 控制台页随时撤销任意 device key（设备 key 与手动 API key 分页管理）。

---

## 3. 端点详尽规范

所有端点在生产环境的 base URL 为 `https://your-llm-relay.example.com`。

### 3.1 `POST /v1/auth/register` — 注册

**请求**

```http
POST /v1/auth/register
Content-Type: application/json

{
  "email": "alice@example.com",
  "password": "min8chars",
  "deviceId": "dp_uuid_alice_mac_001",       // optional，建议提供
  "deviceName": "Alice's MacBook"            // optional，仅显示用
}
```

**响应 201**

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "rt_xxx...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": null,
  "activation": {
    "token": "verify_<hex>",
    "expiresAt": "2026-05-21T04:48:23.521Z"
  }
}
```

- 新账号状态：`PENDING_VERIFICATION`。**access_token 在激活前不能用**（会被 `/v1/me` 等端点拒绝为 `INVALID_TOKEN`）。
- `activation.token` 在此环境直接返回（**尚未接入邮件基础设施**，详见 §10 followup）。生产正式上线邮件后，此字段会改为通过邮件下发。

**错误**

| 状态 | code | 含义 |
|---|---|---|
| 409 | `EMAIL_TAKEN` | 邮箱已注册 |
| 422 | `VALIDATION` | 邮箱格式 / 密码强度不符（最少 8 位） |
| 429 | `RATE_LIMITED` | 触发限流（默认 10/min/IP） |

### 3.2 `POST /api-direct/auth/activate` — 激活账户

> **临时路径说明**：当前活动端点挂在 `/api-direct/auth/activate`。**PR #16 待合并后会同时支持 `POST /v1/auth/activate` 镜像（请求/响应体一致）**，到时你方两条路径都可调，推荐迁到 `/v1/auth/activate`。

**请求**

```http
POST /api-direct/auth/activate
Content-Type: application/json

{ "token": "verify_<hex>" }
```

**响应 200**

```json
{
  "id": "cmpd...",
  "email": "alice@example.com",
  "role": "USER",
  "status": "ACTIVE",
  "createdAt": "2026-05-20T04:48:22.309Z",
  "updatedAt": "2026-05-20T04:48:23.521Z"
}
```

激活成功后，原 `access_token` **不需要重新发**，可直接调 `/v1/me` 等端点（前提是 access 未过期）。

**错误**

| 状态 | code | 含义 |
|---|---|---|
| 400 | `VALIDATION` | token 缺失或不合法 |
| 404 | `NOT_FOUND` | token 不存在 / 已过期 / 已使用 |

### 3.3 `POST /v1/auth/login` — 登录

**请求**

```http
POST /v1/auth/login
Content-Type: application/json

{
  "email": "alice@example.com",
  "password": "min8chars",
  "deviceId": "dp_uuid_alice_mac_001",
  "deviceName": "Alice's MacBook"
}
```

**响应 200**

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "rt_xxx...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": "cmpd...",
    "email": "alice@example.com",
    "role": "USER",
    "status": "ACTIVE",
    "created_at": "2026-05-20T04:48:22.309Z"
  }
}
```

**错误**

| 状态 | code | 含义 |
|---|---|---|
| 401 | `INVALID_CREDENTIALS` | 邮箱或密码错误 |
| 403 | `FORBIDDEN` | 账号未激活 / 已挂起 |
| 429 | `RATE_LIMITED` | 触发限流 |

### 3.4 `POST /v1/auth/refresh` — 刷新令牌

**请求**

```http
POST /v1/auth/refresh
Content-Type: application/json

{ "refresh_token": "rt_xxx..." }
```

**响应 200**

```json
{
  "access_token": "eyJhbGciOi...new...",
  "refresh_token": "rt_yyy...new...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

- **同时 rotate refresh**：旧 refresh 在此调用后**立刻 HTTP 403**，必须使用新 refresh。
- 不返回 `user`（保持响应体最小化）。

**错误**

| 状态 | code | 含义 |
|---|---|---|
| 401 | `INVALID_TOKEN` | refresh 不存在 / 已被吊销 |
| 403 | `FORBIDDEN` | 用户已挂起 / 删除 |

### 3.5 `POST /v1/auth/logout` — 登出（撤销该用户全部 refresh_token）

```http
POST /v1/auth/logout
Authorization: Bearer <access_token>
```

**响应 200** `{ "revoked": 3 }`

撤销后该用户**所有**未过期的 refresh_token 立即作废；access_token 因为是无状态 HMAC，会在自然过期前（≤1h）仍然可被验签——这是设计取舍。如需"立刻吊销所有 access"，调本端点 + 等 1 小时（或客户端主动清空内存中的 access）。

### 3.6 `GET /v1/me` — 当前用户

```http
GET /v1/me
Authorization: Bearer <access_token>
```

**响应 200**

```json
{
  "id": "cmpd...",
  "email": "alice@example.com",
  "role": "USER",
  "status": "ACTIVE",
  "plan": "prepaid",
  "created_at": "2026-05-20T04:48:22.309Z"
}
```

- `plan` 当前恒为 `"prepaid"`（中转站采用预付费钱包模型，不是套餐制）。

### 3.7 `GET /v1/providers` ★ **集成核心**

```http
GET /v1/providers
Authorization: Bearer <access_token>
X-Device-Id:   dp_uuid_alice_mac_001       # 强烈建议提供
X-Device-Name: Alice's MacBook             # optional
```

**响应 200**（实测样例，模型列表已截断）

```json
{
  "providers": [
    {
      "id": "relay-openai",
      "name": "OpenAI (relay)",
      "base_url": "https://your-llm-relay.example.com/v1",
      "api_key": "<redacted-tsk-key>",
      "openai_compatible": true,
      "supports_streaming": true,
      "priority": 1,
      "models": [
        {
          "id": "gpt-5.2",
          "context_window": 400000,
          "capabilities": ["chat","tools","streaming","reasoning_effort","vision"]
        },
        {
          "id": "claude-opus-4.5",
          "context_window": 200000,
          "capabilities": ["chat","tools","streaming","thinking","vision"]
        },
        {
          "id": "deepseek-v3.2",
          "context_window": 128000,
          "capabilities": ["chat","tools","streaming","thinking"]
        }
        // ... 80+ models total
      ]
    },
    {
      "id": "relay-anthropic",
      "name": "Anthropic (relay)",
      "base_url": "https://your-llm-relay.example.com/anthropic/v1",
      "api_key": "<redacted-tsk-key>",
      "openai_compatible": false,
      "supports_streaming": true,
      "priority": 2,
      "models": [/* Anthropic 协议 native 入口的模型子集 */]
    }
  ]
}
```

**重要语义**：

- **同一把 `api_key` 出现在两个 provider 上**——这是同一个 device key，可同时在 OpenAI 兼容路径和 Anthropic native 路径上鉴权。**推荐你方只用 `relay-openai`**（它支持所有 80+ 模型 alias，包括 Claude，因为中转站做了 OpenAI↔Anthropic 跨协议转换）。
- **每次调用都会作废上次的 device key**。客户端策略：
  - 启动时调一次拿 key；保存在内存
  - 调 `/v1/chat/completions` 报 `INVALID_TOKEN` 时再调一次拿新 key
  - 不要做"每分钟刷一次 key"的轮询——会自己 DOS 自己（旧 key 一直被新 key 顶掉）
- **`X-Device-Id`**：强烈建议提供，用作"同账号多设备"分组键。不提供时服务端按 `userId + User-Agent` 自动生成一个稳定 id。
- **模型 `capabilities[]` 取值**：见 §6。

**错误**

| 状态 | code | 含义 |
|---|---|---|
| 401 | `INVALID_TOKEN` / `EXPIRED_TOKEN` | access 失效，需 refresh |
| 403 | `FORBIDDEN` | 账户被挂起 |

### 3.8 `GET /v1/usage/summary` — 余额 + 用量

```http
GET /v1/usage/summary
Authorization: Bearer <access_token>
```

**响应 200**

```json
{
  "plan": "prepaid",
  "balance": {
    "amount_minor": 71317,
    "currency": "CNY"
  },
  "period": {
    "used_minor": 1234,
    "unit": "CNY_minor",
    "reset_at": "2026-06-01T00:00:00.000Z"
  },
  "rate_limit": {
    "rpm": 300,
    "tpm": null
  }
}
```

- `balance.amount_minor`：当前钱包余额，**单位 CN¥ 分（minor unit）**。`71317` = `¥713.17`。
- `period.used_minor`：**本自然月**累计 LLM 消费（分）。`reset_at` 为下月 1 日 UTC 00:00。
- `rate_limit.rpm`：当前账号的每分钟请求上限（默认 300）。`tpm`（token/min）暂未启用，恒为 `null`。

### 3.9 LLM 调用端点（用 device key）

#### 3.9.1 OpenAI 兼容（**推荐**）

```http
POST /v1/chat/completions
Authorization: Bearer tsk_xxx...
Content-Type: application/json

{
  "model": "gpt-5.2",
  "messages": [{"role":"user","content":"Hi"}],
  "stream": true,
  "tools": [...],            // optional
  "reasoning_effort": "high" // optional, 仅 capabilities 里有 reasoning_effort 的模型
}
```

- 完全兼容 OpenAI 协议：`stream: true` 走 SSE，`data: [DONE]` 终止帧，`delta.tool_calls` 分片格式，全部一致
- `model` 字段填 alias（如 `gpt-5.2`、`claude-opus-4.5`、`deepseek-v3.2` 等），中转站自动路由到对应上游
- 支持的协议字段：`messages`, `tools`, `tool_choice`, `stream`, `stream_options`, `response_format`, `reasoning_effort`, `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `presence_penalty`, `frequency_penalty`, `seed`, `stop`, `logit_bias`, `user`

#### 3.9.2 Anthropic native

```http
POST /anthropic/v1/messages
Authorization: Bearer tsk_xxx...                    # 也接受 x-api-key: tsk_xxx
Content-Type: application/json
anthropic-version: 2023-06-01

{
  "model": "claude-opus-4.5",
  "max_tokens": 512,
  "messages": [{"role":"user","content":"Hi"}],
  "stream": true,
  "thinking": { "type": "enabled", "budget_tokens": 2048 }   // optional
}
```

#### 3.9.3 图片生成

```http
POST /v1/images/generations
Authorization: Bearer tsk_xxx...

{ "model": "dall-e-3", "prompt": "...", "n": 1, "size": "1024x1024" }
```

#### 3.9.4 视频生成（异步任务）

```http
POST /v1/videos/tasks
Authorization: Bearer tsk_xxx...

{
  "model": "doubao-seedance-2-0-pro",
  "content": [{"type":"text","text":"a calm sunset"}],
  "ratio": "16:9", "resolution": "720p", "duration": 4
}
→ { "id": "cgt-..." }

GET /v1/videos/tasks/{taskId}
Authorization: Bearer tsk_xxx...
→ { status, content?: { video_url }, error?, usage? }
```

详细字段见 [https://your-llm-relay.example.com/docs/video](https://your-llm-relay.example.com/docs/video)。

### 3.10 `GET /public/models` — 公开模型清单（无需鉴权）

```http
GET /public/models
```

**响应 200**

```json
{
  "models": [
    {
      "alias": "claude-3-5-sonnet",
      "displayName": "Claude 3.5 Sonnet",
      "modality": "TEXT",
      "contextWindow": 200000,
      "pricing": { "inputPer1MMinor": 21000, "outputPer1MMinor": 105000 },
      "capabilities": ["chat","tools","streaming","thinking","vision"]
    },
    ...
  ]
}
```

- 价格单位：**USD/1M token，minor**（美分）。`21000` = `$0.21/1M input tokens`。
- 用于：你方想在 UI 里列出"所有可用模型 + 定价"（不需要登录态时）。

---

## 4. 通用响应约定

### 4.1 错误信封（统一格式）

所有由本平台直接返回的错误（自身鉴权、参数校验、限流、余额等）**统一使用以下结构**：

```json
{
  "code": "MACHINE_READABLE_CODE",
  "message": "Human-readable explanation.",
  "request_id": "req_<24 hex>"
}
```

加上响应头：

```
X-Request-Id: req_<24 hex>
```

**`X-Request-Id` 透传规则**：客户端可在请求头里供 `X-Request-Id: <自定义 id>`，服务端会**原样回写**到响应头并写入 `request_id` 字段。否则服务端自动生成 `req_<random>`。这对排障非常有用——你方上报 bug 时附带 request_id，我们后端能直接定位。

### 4.2 完整错误码表

| HTTP | `code` | 含义 |
|---|---|---|
| 400 | `VALIDATION` | 请求体校验失败；可能带 `fields: { field: "reason" }` |
| 401 | `INVALID_TOKEN` | bearer token 缺失 / 格式错 / 签名错 |
| 401 | `EXPIRED_TOKEN` | access_token 已过期，调 refresh |
| 401 | `INVALID_CREDENTIALS` | login 时邮箱/密码错 |
| 402 | `QUOTA_EXHAUSTED` | 钱包余额不足 |
| 403 | `FORBIDDEN` | 角色/状态拦截（账户未激活、已挂起等） |
| 404 | `NOT_FOUND` | 资源不存在（device key id 等） |
| 409 | `EMAIL_TAKEN` | register 时邮箱已被占用 |
| 429 | `RATE_LIMITED` | 触发限流；附带 `retry_after`（秒） |
| 502 | `UPSTREAM_ERROR` | 上游 LLM 服务返回不可恢复错误 |
| 503 | `UPSTREAM_UNAVAILABLE` | 所有候选上游均不可用 |
| 500 | `INTERNAL` | 兜底（系统异常） |

### 4.3 LLM 调用错误是"透明转发"，**不走** §4.1 信封

当你方调 `/v1/chat/completions` 或 `/anthropic/v1/messages`，若上游 LLM 返回了 400/429 等，中转站**逐字转发上游响应体**，保持 OpenAI / Anthropic SDK 客户端能正常解析。例如上游返回：

```json
{
  "error": {
    "type": "invalid_request_error",
    "message": "Your input...",
    "code": "context_length_exceeded"
  }
}
```

我们的 device key / API key 鉴权失败（401）仍然使用 §4.1 信封（因为这是平台层错误，不是上游错误）。

---

## 5. 限流

### 5.1 响应头（每个响应都有）

```
X-RateLimit-Limit: 60               # 最严格桶的上限
X-RateLimit-Remaining: 58           # 该窗口剩余次数
X-RateLimit-Reset: 54               # 窗口重置剩余秒数

# 同时附带每桶变体（信息用，客户端按规范头即可）:
X-RateLimit-Limit-browser: 60
X-RateLimit-Limit-relay: 300
X-RateLimit-Remaining-browser: 58
X-RateLimit-Remaining-relay: 298
```

### 5.2 桶配置

| 桶 | 默认 | 应用于 |
|---|---|---|
| `browser` | **60 req/min/IP** | 控制面端点 `/v1/auth/*`、`/v1/me`、`/v1/providers`、`/v1/usage/summary` |
| `relay` | **300 req/min/IP** | LLM 调用 `/v1/chat/completions`、`/anthropic/v1/messages` 等 |

特定端点更紧的限流：
- `/v1/auth/login`、`/v1/auth/register`：10 req/min（防爆破）
- `/v1/auth/refresh`：30 req/min

### 5.3 429 响应

```http
HTTP/2 429
Retry-After: 17
X-RateLimit-Remaining: 0
Content-Type: application/json

{
  "code": "RATE_LIMITED",
  "message": "Rate limit exceeded.",
  "request_id": "req_..."
}
```

客户端策略：尊重 `Retry-After`；如未提供该头，则按 `X-RateLimit-Reset` 退避。

---

## 6. 模型能力位（`capabilities[]`）

`/v1/providers` 和 `/public/models` 返回的每个模型都有一个 `capabilities` 字符串数组。完整取值表：

| capability | 含义 | UI 渲染建议 |
|---|---|---|
| `chat` | 支持 chat completion（绝大多数 TEXT 模型有） | — |
| `streaming` | 支持 `stream: true` SSE | 显示流式开关 |
| `tools` | 支持 function calling / tool use | 显示工具配置面板 |
| `vision` | 支持图片输入（`content: [{type:"image_url",...}]`） | 允许用户拖图 |
| `thinking` | 支持 Anthropic-style `thinking` 或 DeepSeek reasoning_content | 显示"展开思维链"按钮 |
| `reasoning_effort` | OpenAI o-series / gpt-5 风格的 `reasoning_effort` 字段 | 显示"思考强度" low/med/high 选择 |
| `embeddings` | 嵌入模型（modality=EMBEDDINGS） | 独立分组 |
| `image_generation` | 图像生成（modality=IMAGE，走 `/v1/images/generations`） | 独立分组 |
| `video_generation` | 视频生成（modality=VIDEO，走 `/v1/videos/tasks`） | 独立分组 |

实例：
- `gpt-5.2` → `["chat","tools","streaming","reasoning_effort","vision"]`
- `claude-opus-4.5` → `["chat","tools","streaming","thinking","vision"]`
- `deepseek-v3.2` → `["chat","tools","streaming","thinking"]`
- `dall-e-3` → `["image_generation"]`

---

## 7. Device Key 详解

### 7.1 生命周期

```
GET /v1/providers (call #1)
  ↓
  签发 tsk_AAA (provenance=DEVICE, deviceId=X)
  ↓
DeskPet 调用 /v1/chat/completions 用 tsk_AAA → ✅ 200
  ↓
GET /v1/providers (call #2 from same deviceId=X)
  ↓
  tsk_AAA 立刻 disabled
  签发 tsk_BBB
  ↓
DeskPet 用 tsk_AAA 调 LLM → 401 INVALID_TOKEN
DeskPet 用 tsk_BBB 调 LLM → ✅ 200
```

### 7.2 多设备隔离

```
deviceId=mac-001 → 自己的 tsk_xxx 系列
deviceId=mac-002 → 自己的 tsk_yyy 系列
deviceId=windows-001 → 自己的 tsk_zzz 系列
```

不同 deviceId 之间互不影响。同 deviceId 的旧 key 在新调用 `/v1/providers` 时被作废。

### 7.3 用户撤销

用户登录 `https://your-llm-relay.example.com/console/devices`，可以看到所有 device key 列表（设备名、deviceId、首次签发时间、最近活跃时间），可逐个撤销。撤销后该 deviceId 下次再调 `/v1/providers` 会拿到一把全新的 key。

### 7.4 客户端责任

- 拿到 `api_key` 后**立刻存到 OS 凭据管理器**（macOS Keychain / Windows Credential Manager / Linux Secret Service）。**不要明文落盘**。
- 不要在 UI 里显示完整 key（如需调试只显示 `tsk_xxxxxxxx••••••••`）。
- 401 时**先**调 `/v1/providers` 拿新 key，**再**重放原 LLM 请求。
- 用户登出时**立刻**从内存清掉 access_token + refresh_token + device key。

---

## 8. 完整代码示例

### 8.1 Python（DeskPet 本地后端最贴近的栈）

```python
import os
import httpx
from typing import Optional

BASE = "https://your-llm-relay.example.com"

class RelayClient:
    def __init__(self, device_id: str, device_name: str):
        self.device_id = device_id
        self.device_name = device_name
        self.access: Optional[str] = None
        self.refresh: Optional[str] = None
        self.providers: dict = {}

    async def login(self, email: str, password: str):
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{BASE}/v1/auth/login", json={
                "email": email, "password": password,
                "deviceId": self.device_id, "deviceName": self.device_name,
            })
            r.raise_for_status()
            d = r.json()
            self.access, self.refresh = d["access_token"], d["refresh_token"]

    async def refresh_session(self):
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{BASE}/v1/auth/refresh", json={
                "refresh_token": self.refresh,
            })
            r.raise_for_status()
            d = r.json()
            self.access, self.refresh = d["access_token"], d["refresh_token"]

    async def fetch_providers(self):
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{BASE}/v1/providers", headers={
                "Authorization": f"Bearer {self.access}",
                "X-Device-Id": self.device_id,
                "X-Device-Name": self.device_name,
            })
            if r.status_code == 401:
                await self.refresh_session()
                return await self.fetch_providers()
            r.raise_for_status()
            d = r.json()
            self.providers = {p["id"]: p for p in d["providers"]}
            return self.providers

    async def chat(self, model: str, messages: list, stream: bool = False, **kw):
        if not self.providers:
            await self.fetch_providers()
        # 全部模型都能走 relay-openai，含 Claude/DeepSeek 等
        p = self.providers["relay-openai"]
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as c:
            r = await c.post(
                f"{p['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {p['api_key']}"},
                json={"model": model, "messages": messages, "stream": stream, **kw},
            )
            if r.status_code == 401:
                # device key 被轮换，重拉
                await self.fetch_providers()
                return await self.chat(model, messages, stream=stream, **kw)
            r.raise_for_status()
            return r.json() if not stream else r  # streaming 自己处理

# 用例
import asyncio, uuid
async def demo():
    client = RelayClient(device_id=f"dp-{uuid.getnode():x}", device_name="DeskPet/macOS")
    await client.login("alice@example.com", "min8chars")
    await client.fetch_providers()
    result = await client.chat("gpt-5.2", [{"role":"user","content":"Hi"}])
    print(result["choices"][0]["message"]["content"])

asyncio.run(demo())
```

### 8.2 OpenAI 官方 SDK（推荐——只换 base_url + api_key 即可）

```python
from openai import OpenAI
client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])
stream = client.chat.completions.create(
    model="claude-opus-4.5",         # ← 中转 alias，原生 SDK 接走 Claude
    messages=[{"role":"user","content":"Hi"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

---

## 9. 对照原 `RELAY-INTEGRATION-REQUEST.md` 的逐项回复

### §1 认证

| 项 | 回复 |
|---|---|
| access_token 是 JWT 还是 opaque | **HMAC-SHA256 三段式**，结构同 JWT（`{alg:HS256,typ:JWT}` 头），但实现是项目内手写。客户端按 opaque bearer 处理即可，**不要尝试自己解析或验签**。 |
| 有无 refresh_token | **有**。`POST /v1/auth/refresh` 走 rotation（旧 refresh 立刻吊销） |
| token 有效期 | access **1h**；refresh **30d** |
| 登录限流 | **10/min/IP**（`/v1/auth/login`、`/v1/auth/register`） |
| 邮箱 + 用户名二选一 | **目前只支持邮箱**。无 username 字段 |
| 注册即登录 vs 邮箱验证 | **当前是邮箱验证模型**（PENDING_VERIFICATION → activate → ACTIVE）。但**邮件基础设施未接入**，activation token 在当前生产是注册响应直接返回的（详见 §10）。 |
| 密码强度规则 | **最少 8 位**；无大小写/特殊字符要求（可后续收紧） |
| 图形验证码 / 滑块 | **无**（headless 友好） |
| 找回密码 | **未实现**（依赖邮件基础设施，详见 §10） |
| 登出 | `POST /v1/auth/logout` 撤销所有 refresh_token；access 自然过期 |
| `/api/me` | **已提供：`GET /v1/me`**（路径以 `/v1` 为准） |

### §2 Provider 自动配置

| 项 | 回复 |
|---|---|
| 统一 OpenAI 兼容 | ✅ **是**。单一 base_url `https://your-llm-relay.example.com/v1`，POST `/chat/completions` 接所有模型（含 Claude/DeepSeek 等，通过跨协议路由）。Anthropic native 路径并存但不必用。 |
| api_key 形态 | **(b) 独立长期 device key**——你方选定的方案。每次 `/v1/providers` 自动轮换一把新 key；旧 key 立刻 disabled。 |
| 模型能力位 | ✅ 已实现 `capabilities[]`，含 `reasoning_effort`、`thinking`、`tools`、`vision`、`streaming` 等（详见 §6） |
| provider 列表变化频率 | 账户级别基本恒定（同账号 + 同设备 → 同模型集）；上游配置变化时管理员侧会刷新数据库，下次调用自然反映。**ETag 当前未实现**——可加入 followup（流量不大暂不优先） |

### §3 额度可见性

| 项 | 回复 |
|---|---|
| 计量单位 | **CN¥ minor（分）金额**；非 token 数也非次数 |
| 周期 | **自然月**（UTC 1 日 00:00 重置） |
| 速率限制 | **HTTP 头 `X-RateLimit-*`** + 429 时 `Retry-After` |
| 402/429 错误码 | ✅ `QUOTA_EXHAUSTED` (402)、`RATE_LIMITED` (429)，均走 §4.1 信封 |

### §4 安全与协议

| 项 | 回复 |
|---|---|
| HTTPS | ✅ Caddy + Let's Encrypt 自动续期 |
| 沙盒域名 | ❌ **暂无**（followups #1） |
| token 撤销 | ✅ refresh 端实时撤销；access 1h 自然过期 |
| 错误统一 schema | ✅ `{code, message, request_id}` + `X-Request-Id`（详见 §4.1） |
| API 版本化 | ✅ 路径前缀 `/v1/` |
| CORS | 开启（`Access-Control-Allow-Origin: *`），你方非浏览器场景无关 |
| TLS pinning | **建议不做**——Caddy 每 60 天自动续期，证书指纹会变。如确需，followups #6 列了变化通知机制思路 |

### §5 品牌与法务

| 项 | 状态 |
|---|---|
| 正式名称（中英） | Token Relay |
| Logo | ❌ 暂无（followups #8） |
| 用户协议 / 隐私政策 URL | ❌ 暂无（followups #8） |
| 客服 / 反馈邮箱 | ❌ 暂无 |
| 官网首页 | `https://your-llm-relay.example.com/` |
| 充值页 | `https://your-llm-relay.example.com/console/billing` |
| 账户管理 | `https://your-llm-relay.example.com/console` |

### §6 可选增强

| 项 | 状态 |
|---|---|
| OAuth 第三方登录 | ❌ followups #3 |
| 设备绑定 / 列表 | ✅ **已实现**：`https://your-llm-relay.example.com/console/devices`（用户可视 + 撤销） |
| 使用记录 | ✅ `https://your-llm-relay.example.com/console/usage`（per-key / per-model 明细 + CSV 导出） |
| Webhook | ❌ followups #4 |

### §7 开发协作

| 项 | 状态 |
|---|---|
| 沙盒环境 | ❌ followups #1 |
| API 文档 | ⏳ 本文档 + followups #5（OpenAPI 自动生成） |
| 联系人 / 联调群 | （待补——请回复邮箱 / IM） |

---

## 10. 已知缺口（followups，按优先级）

详见 [`plans/RELAY-INTEGRATION-FOLLOWUPS.md`](./RELAY-INTEGRATION-FOLLOWUPS.md)，本节列出对**你方** DeskPet 联调影响最大的几项：

1. **沙盒环境**：暂无。先用生产 + 测试账号联调，提交真实交易前可调小金额验证。
2. **邮件基础设施未接通**：当前 `/v1/auth/register` 响应里直接返回 `activation.token`。生产正式上线邮件后会改为发邮件（响应里不再含 activation 字段）。**你方实现时建议同时兼容两种形态**——如果响应有 `activation.token`，直接调 `/api-direct/auth/activate`（PR #16 合并后改 `/v1/auth/activate`）；如果没有，提示用户"请去邮箱点验证链接 / 输入收到的验证码"。
3. **找回密码**：未实现（依赖上一项）。当前用户忘密码暂时需要管理员重置。
4. **`/v1/auth/activate` 路径**：当前激活只能走 `/api-direct/auth/activate`。PR #16 合并后会有 `/v1/auth/activate` 镜像，请到时迁过去。

---

## 11. 联调流程建议

1. **你方先按本文档写 adapter**，跑通 register → activate → login → providers → chat 五步
2. 把请求 / 响应样例（含 `request_id`）反馈给我们，对照确认契约
3. 我们补 sandbox（如必要）+ 你方关心的 capability 字段精度
4. 正式发版前我们一起跑一遍 E2E（包含限流、refresh 轮换、device key 旋转、余额耗尽场景）

---

## 12. 联系方式

（待补——请回复邮箱 / IM 群链接，方便随时同步）

---

## 13. 变更历史

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-05-20 | v1.0 | 初版。覆盖 P0+P1（auth v1、providers 自动轮换、usage summary、错误信封、限流头、capabilities、devices console 页）。沙盒 / 邮件 / OAuth / Webhook / OpenAPI 自动生成入 followups。|
