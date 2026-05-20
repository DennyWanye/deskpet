# DeskPet × Token Relay 集成对接 — 回复 / 决策文档

> **版本**：v1.0 · **日期**：2026-05-20
> **针对**：`plans/DESKPET-INTEGRATION-GUIDE.md` v1.0
> **状态**：技术契约已**接受**，DeskPet 侧可立即开始 adapter 实现
> **特殊说明**：本项目个人开发，两端（中转站 + DeskPet）由同一人负责，本文档同时作为 DeskPet 侧实现 TODO 与中转站侧 followup 排期参考。

---

## 1. 总体确认

逐项审过 `DESKPET-INTEGRATION-GUIDE.md` v1.0，**技术契约可用**：

- 鉴权双层（access/refresh + device key）设计合理 ✓
- `/v1/providers` 返回 OpenAI 兼容 + 独立长期 device key（方案 b）符合需求 ✓
- 能力位 `capabilities[]`、错误信封 `{code, message, request_id}`、限流头 `X-RateLimit-*` 均落地 ✓
- `GET /v1/usage/summary` 余额返回 CN¥ minor 单位明确 ✓

可以**立即开始** DeskPet 侧 adapter 编码，不等所有 followup 全部完成。

---

## 2. 已达成共识

### 2.1 接受现有所有端点契约

照 guide v1.0 §3 写客户端，路径 / 字段 / 错误码以 guide 为准。

### 2.2 **不做沙盒环境**

接受用生产 `https://chinzy.com` 直接联调，配合**充值小额的测试账号**（自己充 ¥10-20 走完所有路径）。

**约束**：

- DeskPet 自动化测试**不**对生产真打 LLM 请求；mock 在 fixture 层（用 guide §8 的样例响应做 fake server）
- 联调期间真实请求只走手动 E2E

### 2.3 **品牌与法务资产暂缓**（GA 前必须，内测不阻塞）

以下 4 项**先不做**：

- ❌ Logo（SVG + PNG）
- ❌ 用户协议 URL
- ❌ 隐私政策 URL
- ❌ 客服 / 反馈邮箱

**前提**：DeskPet 内测阶段（≤100 用户）可以不勾选协议；正式上架 App Store / Microsoft Store / 微信小程序之前**必须**全部补齐（合规硬要求），那时再处理。

DeskPet 侧实现：登录 UI 留出协议勾选框 + URL 占位，临时指向 `https://chinzy.com/`，资产到位后只改 URL 不改 UI。

### 2.4 联系人 = 同一人

本项目个人开发，DeskPet 侧 + 中转站侧由同一人维护。

- **不开联调群**，所有决策直接更新本文档与 `RELAY-INTEGRATION-FOLLOWUPS.md`
- 出问题不会"邮件等回复"——直接两端同时改

---

## 3. 仍需要处理的技术项（按优先级）

### 3.1 P0 — 阻塞 DeskPet 客户端代码定型

| # | 项 | 说明 | 谁负责 | 预期 |
|---|---|---|---|---|
| P0-1 | `/v1/auth/activate` 镜像（PR #16） | 当前激活只能走 `/api-direct/auth/activate`，违反 `/v1/*` 约定 | 中转站侧 | **本周合并**，否则客户端要写两套兜底 |

### 3.2 P1 — 影响 GA / 付费版上线

| # | 项 | 说明 | 谁负责 | 预期 |
|---|---|---|---|---|
| P1-1 | 邮件基础设施 + 找回密码 | 当前 `/v1/auth/register` 直接返回 `activation.token`，没有 `/v1/auth/forgot-password` 端点。付费用户忘密码不能依赖人工 | 中转站侧 | **GA 前必须**，内测期可以容忍 |
| P1-2 | 充值 API（DeskPet 内一键跳转） | 现只有 `/console/billing` web 页，无 deeplink。付费版必须能在桌宠 UI 里发起充值 | 中转站侧 | 设计 `POST /v1/billing/checkout-session?return_to=deskpet://billing-done` |
| P1-3 | 客户端把 device key 存 OS Keyring | macOS Keychain / Windows Cred Manager / Linux Secret Service | DeskPet 侧 | Week 2 |

### 3.3 P2 — 体验改善 / 防止踩坑

| # | 项 | 说明 | 谁负责 | 预期 |
|---|---|---|---|---|
| P2-1 | Postman collection | 暂时不做完整 OpenAPI，先导一份 Postman 方便自测和 fixture 生成 | 中转站侧 | 1 天即可 |
| P2-2 | OpenAPI 自动生成（followups #5） | 中长期，做了能 codegen client | 中转站侧 | 1 个月内 |
| P2-3 | 设备 key 不轮换查询端点 `GET /v1/providers?rotate=false` | 防止客户端竞态（重启 / 同机多进程） | 中转站侧 | 1-2 天 |
| P2-4 | `/public/models` 加 CN¥ 价格或汇率字段 | 单位统一，便于 UI 估算单次调用花费 | 中转站侧 | 1 天 |

---

## 4. 5 个待澄清 / 待修正的技术点

### 4.1 deviceId 缺失时的兜底行为

guide §3.7 说"不传 `X-Device-Id` 时按 `userId + User-Agent` 自动生成 stable id"。

**问题**：DeskPet 所有客户端 User-Agent 都是 `DeskPet/x.y.z (platform)`，同账号在两台 Mac 上会被识别为同一设备 → device key 互相吃掉。

**决策**：

- DeskPet 侧**必须传 `X-Device-Id`**（用 `crypto.randomUUID()` 首次启动生成 + 持久化到 userdata）
- 中转站侧 **`X-Device-Id` 改成必传**：缺失直接 400 `VALIDATION { fields: { "X-Device-Id": "required" } }`，停止使用 stable id 兜底（这是隐藏地雷）

### 4.2 device key 自动轮换的客户端竞态

guide §7.1 说"每次 `GET /v1/providers` 都作废旧 key"。

**场景**：
- 用户重启 DeskPet → 新进程拉 `/v1/providers` 拿到 `tsk_BBB`
- 旧进程没干净退出，还在用 `tsk_AAA` 跑 → 401

**决策**：

- 中转站侧加 `GET /v1/providers?rotate=false`：仅返回当前有效 key，不签发新 key
- DeskPet 客户端策略：
  - **冷启动**：`?rotate=false` 拿当前 key；返回 `device_key_missing` 时才不带参数调用触发签发
  - **401 时**：不带参数调用，强制轮换
  - **手动"重置 key"按钮**：不带参数调用

### 4.3 币种与汇率

`/public/models` 价格用 **USD minor**（美分），`/v1/usage/summary` 余额用 **CN¥ minor**（分）。

**问题**：DeskPet UI 想做"这次调用预计 ¥0.0X"必须做汇率换算，但没有汇率字段。

**决策**（二选一）：

- (a) `/public/models` 增加 `pricing_cny`（直接给 CN¥ minor）—— 推荐
- (b) `/v1/usage/summary` 增加 `exchange_rate_usd_to_cny: 7.13`（每日刷新）

中转站侧定哪个，DeskPet 侧适配哪个。

### 4.4 502 `UPSTREAM_ERROR` vs 503 `UPSTREAM_UNAVAILABLE` 判定规则

guide §4.2 列了两个码但描述太接近。

**决策**：

- 502 = 单一上游 5xx 但还有其他候选可重试
- 503 = 所有候选上游都 retry 完仍失败 / 上游全部熔断

DeskPet 客户端策略：
- 502 → 立即重放 1 次（中转站可能已切下一家上游，这次不一定还失败）
- 503 → 不重试，直接报错 "服务暂时不可用，请稍后再试"

### 4.5 充值 / billing API（P1-2 的具体形态）

DeskPet 付费版"余额低提示 → 一键充值"流程需要：

```
POST /v1/billing/checkout-session
Authorization: Bearer <access_token>
{
  "amount_minor": 5000,           // 充 ¥50
  "currency": "CNY",
  "return_to": "deskpet://billing-done?session=xxx"
}
→ { "checkout_url": "https://chinzy.com/pay/...?session=xxx" }
```

DeskPet 用 Tauri `opener` plugin 拉系统浏览器打开 `checkout_url`，支付完成后中转站 redirect 到 `return_to`，DeskPet 拦截 `deskpet://` deeplink 刷新 `/v1/usage/summary`。

中转站侧需要：
- 接入支付通道（微信 / 支付宝 / Stripe 三选一起步）
- 实现 deeplink scheme 协议（return_to 必须以 `deskpet://` 或 `https://` 开头白名单）
- DeskPet 侧需要：
  - 注册自定义 URL scheme `deskpet://`（Tauri 配置）
  - 处理 deeplink 事件 → 刷新余额

---

## 5. DeskPet 客户端实现路径

整体在 `feat/relay-integration` 分支开发，跑通后合 master。

### Week 1：基础架构

- [ ] 抽离 `AuthAdapter` 接口（与之前讨论一致，先在 OSS 仓库做）
- [ ] 实现 `ManualAuthAdapter`（用户自填，保留现有手填 provider 流程）
- [ ] 实现 `NullAuthAdapter`（匿名占位）
- [ ] 单测 + tsc 全绿

### Week 2：Relay adapter

在私有仓库 `deskpet-cloud` 实现：

- [ ] `RelayAuthAdapter` 类（login / register / activate / refresh / logout / me / providers / usage）
- [ ] device id 生成 + 持久化（首次启动 uuid，存 userdata/device-id.txt）
- [ ] device key 存 OS Keyring（沿用现有 `tauri-app/src-tauri/src/secrets.rs`）
- [ ] access/refresh token 存 OS Keyring（**不写明文文件**）
- [ ] 401 自动 refresh + device key 轮换重试逻辑

### Week 3：UI

- [ ] 登录 / 注册 / 激活页面（仿现有 ChangeModelModal 样式）
- [ ] 协议勾选 UI（URL 暂指向 chinzy.com，资产到位后只改 URL）
- [ ] 找回密码入口（暂时跳转 chinzy.com console，告知用户"请联系管理员"）
- [ ] Settings panel 显示余额 + 用量 + 当前 device 名称
- [ ] "重新登录" / "登出" 按钮

### Week 4：错误处理 + 体验细节

- [ ] 余额低（< ¥5）→ 桌宠 DialogBar 提示 + 充值入口
- [ ] 余额耗尽（402）→ 强制弹充值 modal
- [ ] 限流（429）→ 尊重 `Retry-After` 退避，UI 显示"请求过于频繁"
- [ ] 502 → 自动重放 1 次
- [ ] 503 → 弹错误 + "稍后再试"

### Week 5+：充值集成（待中转站 P1-2 完成）

- [ ] Tauri 自定义 URL scheme `deskpet://`
- [ ] 拦截 deeplink → 刷新余额
- [ ] 充值 modal（金额预设 / 自定义）

---

## 6. 测试策略（无沙盒补偿方案）

由于不做 sandbox，测试用如下分层：

| 层 | 方案 |
|---|---|
| 单测 | mock HTTP client，fixture 用 guide §8 的样例响应 |
| 集成测试 | 起一个 FastAPI mock server 实现 guide 契约的子集，跑 RelayAuthAdapter 全流程 |
| 手动 E2E | 充 ¥20 的真实测试账号 `dev-test@chinzy.com`，每次 release 前手跑 5 步流程 |
| 生产监控 | 接 `request_id` 到日志，出错能直接对 chinzy.com 服务端 trace |

---

## 7. 后续 sync 节奏

- 每周写一次进度到本文档 `## 9. 进度日志`（待开）
- followup 状态变更直接改 `plans/RELAY-INTEGRATION-FOLLOWUPS.md`
- 重大决策（API 改动 / 协议变更）追加到本文档 `## 8. 变更历史`

---

## 8. 变更历史

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-05-20 | v1.0 | 初版。接受 guide v1.0；不做沙盒、暂缓品牌资产、确认 solo 维护；列出 P0/P1/P2 followup 和 5 个技术澄清；制定 Week 1-5 实现路径 |
