# DeskPet 接入中转站账户体系 — 需求清单

> 一次性发给中转站对接方。逐项回 **Y / N / 我们的方案是 X** 即可。
> 任何字段命名、URL 路径、错误码风格你们已有标准的，以你们的为准。

---

## 背景

DeskPet 是一个本地桌面 AI 助手，目前由用户自己在 Settings 里手填各家 LLM provider（OpenAI 兼容接口、base_url、API key）。现在希望接入贵站的账号体系：

**用户在桌宠里用中转站账号登录后，桌宠自动拿到可用的 provider 列表 + 该账号下的接口凭证，无需用户再手填 key；之后所有 LLM 调用都通过中转站，走该账号的额度。**

桌宠侧形态：Tauri 桌面应用（Windows/macOS/Linux），通过 WebSocket 连本地 Python 后端，本地后端再向 OpenAI 兼容上游发请求。所以对中转站的真正调用方是**本地 Python 后端**（非浏览器），鉴权头我们可以自由附带。

---

## 一、认证 API（**必需**）

请确认以下端点 / 字段。如有现成实现请直接给文档链接；没有也请按以下契约提供（命名可微调）。

### 1.1 登录

```
POST {base}/api/auth/login
Content-Type: application/json

{ "username_or_email": "...", "password": "..." }

200 → {
  "access_token": "<jwt or opaque>",
  "refresh_token": "<opaque>",
  "token_type": "Bearer",
  "expires_in": 3600,                  // 秒
  "user": { "id": "...", "username": "...", "email": "..." }
}

401 → { "code": "INVALID_CREDENTIALS", "message": "..." }
429 → { "code": "TOO_MANY_ATTEMPTS", "retry_after": 60 }
```

请明确：
- access_token 是 **JWT** 还是 **opaque bearer**？
- 有没有 **refresh_token** 机制？refresh 端点 URL + 行为？
- token 默认有效期？
- 登录失败的速率限制策略？
- 是否支持 **邮箱登录 + 用户名登录** 二选一？

### 1.2 注册

```
POST {base}/api/auth/register
{ "username":"...", "email":"...", "password":"..." }

201 → { "user": {...}, ...同 login 的 token 字段 }   // 注册即登录？还是要走邮箱验证？
409 → { "code": "USERNAME_TAKEN" | "EMAIL_TAKEN" }
422 → { "code": "VALIDATION", "fields": { "password": "至少 8 位" } }
```

请明确：
- 注册后**是否立即返回 token**（直接登录），还是必须先**邮箱验证**？
- 邮箱验证用什么方式？点击邮件链接 / 6 位验证码？
- 密码强度规则（长度、字符集）？
- 是否需要图形验证码 / 滑块？如果需要，能否提供 headless 友好方案（桌面客户端没有 reCAPTCHA 环境）？

### 1.3 找回密码

```
POST {base}/api/auth/forgot-password { "email": "..." }
→ 邮件含验证码或重置链接

POST {base}/api/auth/reset-password { "email":"...", "code":"123456", "new_password":"..." }
→ 200 即可
```

请明确：
- 走 **重置链接**（用户点击邮件里的 URL）还是 **OTP 验证码**（桌宠里输入）？OTP 更适合桌面客户端；链接的话需要 deeplink/URL scheme 处理，会更复杂。

### 1.4 登出 / token 失效

```
POST {base}/api/auth/logout         Authorization: Bearer <token>
→ 撤销 refresh_token；access_token 自然过期即可
```

### 1.5 当前用户信息

```
GET {base}/api/me                   Authorization: Bearer <token>
→ {
  "id": "...",
  "username": "...",
  "email": "...",
  "plan": "free | pro | ...",
  "avatar_url": "...",
  "created_at": "..."
}

401 → token 失效，桌宠会触发 refresh 或弹登录
```

---

## 二、Provider 自动配置（**核心需求**）

登录后桌宠最关心的就是这一步。我们希望调用一个端点拿到"这个账号能用哪些模型 / 走什么 URL / 用什么 key"，然后写进本地 provider registry。

### 2.1 拿可用 provider 列表

```
GET {base}/api/providers              Authorization: Bearer <access_token>

→ {
  "providers": [
    {
      "id": "relay-openai",                 // 我们这边作为 provider 标识
      "name": "OpenAI（中转）",              // UI 显示名
      "base_url": "https://your-relay.com/v1",
      "api_key": "sk-relay-xxx...",         // 见下方"key 形态"问题
      "models": [
        { "id": "gpt-4o", "context_window": 128000, "capabilities": ["chat","tools","reasoning_effort"] },
        { "id": "gpt-4o-mini", "context_window": 128000, "capabilities": ["chat","tools"] }
      ],
      "openai_compatible": true,
      "supports_streaming": true,
      "priority": 1
    },
    {
      "id": "relay-anthropic",
      "name": "Claude（中转）",
      "base_url": "https://your-relay.com/v1",
      "api_key": "sk-relay-xxx...",          // 是否复用同一个 key？还是每家一把？
      "models": [
        { "id": "claude-opus-4.5", "context_window": 200000, "capabilities": ["chat","tools","thinking"] }
      ],
      "openai_compatible": true,
      "supports_streaming": true,
      "priority": 2
    }
  ]
}
```

需要明确的关键问题：

1. **接口形态**：所有上游是**统一封装成 OpenAI 兼容**（一个 base_url 走所有家），还是按 provider 分多个 base_url？
   - 强烈建议统一成 OpenAI 兼容（POST /v1/chat/completions），桌宠侧已经针对这个 schema 做了完整支持，不用各家适配。
2. **api_key 形态**：返回的 `api_key` 是
   - (a) 直接复用用户的 `access_token`（中转站校验 token = 校验账户）；
   - (b) 中转站为每个账号签发一把**独立的、长期的 provider key**（即使 access_token 过期，provider 调用仍能直接用 key）；
   - (c) 短期 STS-style 凭证（每小时换一次）？
   - 推荐 (b)：解耦认证 token 和 API 调用 key，桌宠后端持有 provider key 直接打 LLM，不用每次刷 token。
3. **models 字段能力位**：你们能否告诉我们每个模型支持
   - `reasoning_effort`（OpenAI o-series 风格）
   - `thinking`（Anthropic 风格）
   - `tools`（函数调用）
   - `vision`（图片输入）
   - `context_window`（上下文 token 上限）

   桌宠 UI 会根据这些位渲染对应参数控件。
4. **provider 列表多久变一次**：账户升级套餐后 provider 列表会变吗？我们是登录时拉一次缓存，还是每隔 N 分钟拉？建议加 `ETag` / `If-None-Match` 减少流量。

### 2.2 调用约定

我们对每个 provider 发出的请求样例（OpenAI 兼容）：

```
POST {provider.base_url}/chat/completions
Authorization: Bearer {provider.api_key}
Content-Type: application/json

{
  "model": "gpt-4o",
  "messages": [...],
  "stream": true,
  "tools": [...],            // 可选
  "reasoning_effort": "high" // 仅 OpenAI o-series
}
```

请确认你们的中转格式与 OpenAI 完全一致（含 `data: [DONE]` SSE 终止帧、`delta.tool_calls` 分片 schema、`function_call` 还是 `tool_calls` 字段等）。任何与上游 OpenAI/Anthropic 偏差的地方都列出来。

---

## 三、额度 / 流量可见性（**必需**）

桌宠会在 UI 里告诉用户"还剩多少额度"，避免到点没钱才知道。

### 3.1 查询额度

```
GET {base}/api/usage                  Authorization: Bearer <token>

→ {
  "plan": "pro",
  "balance": { "amount": 12.34, "currency": "USD" },     // 或 token 数 / 调用次数
  "period": { "used": 1234567, "limit": 5000000, "unit": "tokens", "reset_at": "2026-06-01T00:00:00Z" },
  "rate_limit": { "rpm": 60, "tpm": 100000 }
}
```

请明确：
- 计量单位是 **金额** / **token 数** / **调用次数** 中哪一种（或多种并存）？
- 周期是月 / 自然日 / 滚动 24h？
- 速率限制如何返回？是 HTTP 头（`X-RateLimit-*`）还是 body 字段？

### 3.2 额度耗尽时的错误码

```
HTTP 402 或 429 → {
  "code": "QUOTA_EXHAUSTED" | "RATE_LIMITED",
  "message": "...",
  "retry_after": 60,           // 秒，仅 RATE_LIMITED
  "upgrade_url": "https://your-relay.com/billing"   // 桌宠会引导用户跳过去
}
```

---

## 四、安全与协议（**必需**）

1. **必须 HTTPS**（自签证书不接受；用 Let's Encrypt 或公网 CA）
2. **域名**：生产域名 + 沙盒域名各一个。沙盒可以放假数据，但 schema 必须一致
3. **token 撤销**：当用户在网页端改密码 / 注销账号 / 被封禁，已发出的 access_token 必须能立即失效（推 token blacklist / 短 TTL + refresh）
4. **错误响应统一 schema**：所有错误回
   ```json
   { "code": "MACHINE_READABLE_CODE", "message": "用户可读消息", "request_id": "..." }
   ```
   请提供完整 code 表（INVALID_TOKEN / EXPIRED_TOKEN / QUOTA_EXHAUSTED / RATE_LIMITED / VALIDATION / NETWORK / UPSTREAM_ERROR …）
5. **API 版本化**：URL 加 `/api/v1/` 还是 header `X-API-Version`？建议前者
6. **CORS**：桌宠不是浏览器（是 Python httpx / Rust reqwest 直发），不需要 CORS；但如果未来要接 web 前端，请确认能配置允许来源
7. **TLS pinning 期望**：你们的证书会换吗？换的话提前多久能告诉我们？（我们考虑是否做 cert pinning）

---

## 五、品牌与法务（**推荐**）

桌宠登录界面要显示中转站品牌。请提供：

- [ ] 中转站 **正式名称**（中英文）
- [ ] **Logo**（SVG + 256x256 PNG）
- [ ] **用户协议 URL**（注册时强制勾选）
- [ ] **隐私政策 URL**（注册时强制勾选）
- [ ] **客服 / 反馈 邮箱**或入口
- [ ] **官网首页 URL**（"了解更多"按钮跳转）

如果有"忘记密码 / 修改资料 / 充值 / 账户管理"的**网页版页面**，把这些 URL 也给我，桌宠里用按钮直接拉浏览器打开（避免桌宠里复刻一套）。

---

## 六、可选增强

- **OAuth 第三方登录**（Google / GitHub / 微信 / Apple）：如果支持，请说明 client_id 申请方式；桌宠会用系统浏览器走标准 OAuth code flow + custom URL scheme 回跳
- **设备绑定 / 设备列表**：同一账号在几台设备可登？要不要在桌宠里看到"我的设备"
- **使用记录**：能否拉某个时间段的 LLM 调用历史（用于桌宠里看自己花了多少 token）
- **Webhook**：账户余额低于阈值、套餐到期 push 给桌宠？（不强求）

---

## 七、开发协作（**必需**）

为了能正式联调，请提供：

1. **沙盒环境**：URL + 至少 2 个测试账号（不同套餐），余额可重置
2. **API 文档**：OpenAPI / Swagger / Postman collection 任一种
3. **联系人**：技术联调对接人邮箱 / IM，最好能拉群
4. **时间表**：你们这边能给的交付窗口（影响我这边排期）

---

## 八、我这边会做什么（同步给你们参考）

为了帮你们评估对接复杂度，列一下桌宠侧负责什么：

- 在桌宠内置 **登录 / 注册 / 忘记密码** 的 UI（不需要 web 页面）
- access_token 存到操作系统 **凭证管理器**（Windows Credential Manager / macOS Keychain / Linux Secret Service），不写明文文件
- refresh_token 在后台静默刷新；refresh 失败时弹登录框
- 登录成功后调 `GET /api/providers`，把返回的 provider 列表**完整覆盖**桌宠本地的 provider registry（用户原有的手填 provider 进入"我的私有 provider"分组，互不干扰）
- 每次 LLM 调用前后端会在请求里附带 `User-Agent: DeskPet/x.y.z (platform)` 便于你们后台统计
- 用户登出 / token 失效时，立即清空内存中的 provider key

---

## 九、给你们一个最小决策版（懒人路径）

如果你们想最快跑通 MVP，**最少**只需要确认这 5 件事：

1. `POST /api/auth/login` 返回 JWT + refresh_token，OK？
2. `GET /api/me` 返回基础用户信息，OK？
3. `GET /api/providers` 返回 OpenAI 兼容的 `{base_url, api_key, models[]}` 列表，OK？
4. `GET /api/usage` 返回余额，OK？
5. 给一个沙盒 URL + 2 个测试账号

这 5 项确认后我这边就能写代码，注册 / 找回密码 / 增强字段可以后续补。

---

**请按上面逐项回复（Y / N / 我们的方案是 X）即可。** 我这边会按你们最终确认的契约写一份桌宠侧的 adapter，对接成本由我承担。
