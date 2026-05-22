# 测试执行结果 — relay 登录 + 中转站充值接入

**执行日期**: 2026-05-22
**关联**: `00-PRD.md` / `01-TDD.md` / `02-manual-test-cases.md` / `03-test-plan.md`

---

## 1. 文档修订（M1-M6）

按架构师评估的 6 个必须修订项,4 份文档已修订到第 2 版:
- **M1** TDD A2 重写为"复用已存在的 `POST /config/cloud` 热重载入口"
- **M2** 删除改 `keys.py` 的方案（该路径对 `[llm]` 调用无效）
- **M3** 后端唯一改动定为 `update_cloud_config` 加 `persist_key=false` 旁路
- **M4** key 轮换重试改为前端驱动的跨层闭环
- **M5** OnboardingWizard 改为步骤数组化重构
- **M6** 余额 402 契约列为硬前置,未确认前按合理假设 + `TODO(M6)` 标注

## 2. 实现（WI-R1 ~ WI-R5）

| WI | 内容 | 关键产出 |
|---|---|---|
| R1 | 构建 edition 切换 | `.env.relay`、`dev:relay`/`build:relay` 脚本、`vite.config.ts` edition 解析 |
| R2 | 后端 persist_key 旁路 | `CloudConfigRequest.persist_key`；relay `tsk_xxx` 不明文落盘 |
| R2 | 前端 relayProviderBridge | `relayProviderBridge.ts`（串行化 + 失败可感知）+ `relayConfig.ts`，接入 `RelayEdition` |
| R3 | onboarding 数组化 | `stepsForEdition()`；relay 2 步 / manual 3 步；`App.tsx` 登录前置 |
| R4 | 充值链接 | `AccountSettingsPanel` "去充值" → 中转站充值页 |
| R5 | 错误边界 | `relay_errors.classify_relay_error` + `LLMProviderError.error_class`；ledger 80% 警告含充值提示 |

## 3. 自动化测试（TDD T1-T7）

| 套件 | 结果 |
|---|---|
| 新增前端用例（T1/T2/T4/T5）| relayProviderBridge 9 / auth edition / onboarding 12 / relayConfig 等全绿 |
| 新增后端用例（T3/T6）| `test_relay_llm_bridge` 6 / `test_relay_errors` 6 / `test_billing` T6-4 / `test_openai_compatible` +2 全绿 |
| backend pytest 全量 | **1750 passed**，10 skipped，**0 回归**（基线 1734）|
| frontend vitest 全量 | **274 passed**，**0 回归**（基线 255）|
| `tsc -b` | 通过 |
| Rust cargo | 本轮无 Rust 改动（`commands.rs` 透传 `Value`），未重跑 |

## 4. 实机测试（windows-mcp 子代理，opus 4.7）

### 第 1 轮 — No-Go，发现 2 个 bug

- **Bug-1**：`restoreSession()` 不加载 device identity → 冷启动 `listProviders()`
  因空 `X-Device-Id` 被中转站拒绝 → bridge 未重推。
- **Bug-2**：`classify_relay_error` 已实现但未接进 production（`openai_compatible.py`
  抛裸 HTTP 错误）→ WI-R5 余额不足/401 链路未接通。

### 修复

- Bug-1：`RelayAuthAdapter.restoreSession()` 增 `await ensureDeviceIdentity()`。
- Bug-2：`openai_compatible.py` 的 HTTPStatusError 处理接入 `classify_relay_error`，
  把 402→`insufficient_balance` / 401→`relay_key_invalid` 写入
  `LLMProviderError.error_class`。
- 各补单测（`RelayAuthAdapter.test.ts` Bug-1 用例、`test_openai_compatible.py`
  402/401 分类用例）。

### 第 2 轮（复测，修 2 bug 后）— **Go**

| 用例 | 结果 |
|---|---|
| R1 强制登录 | ✅ |
| **R3 登录即用（一票否决）** | ✅ 零手填即可聊天 + 触发 `excel_create`，请求真实发往 `chinzy.com/v1`，model `gpt-5.5` |
| R4 重启 restoreSession | ✅ Bug-1 实机验证：无 `X-Device-Id` 报错，provider 重推 |
| R5 账户面板 + 去充值 | ✅ |
| R7 登出 | ✅ |
| R8-1 / R8-5 错误边界 | ✅ |
| **R9 manual 零回归（一票否决）** | ✅ |

第 2 轮环境受限未跑：R2 注册、R6 余额不足、R8-2/3/4/6 → 第 3 轮补完。

### 第 3 轮（补测剩余用例）— **Go**

为补测 R6/R8-6 的余额/key 失效链路，先补齐 `error_class` 端到端接线
（`ErrorEvent.error_class` + main.py `chat_v2_error` payload + 前端
`relayErrorText.friendlyChatErrorMessage` + `App.tsx` 渲染/401 自愈），
新增 T6-3 前端用例。然后:

| 用例 | 结果 | 验证方式 |
|---|---|---|
| R2 注册→自动登录 | ✅ | 真打 `chinzy.com/v1/auth/register` → 201 + token，无需验证码，happy path |
| R6 余额不足 | ✅ | mock 402 真机诱发 → `chat_v2_error.error_class="insufficient_balance"` → 友好文案，无裸 "402" |
| R8-2 断网启动 | ✅ | offline fetch 驱动 → `restoreSession` 返 false 不崩 |
| R8-3 断网登录 | ✅ | → `RelayApiError{NETWORK_ERROR}` → 窗内"网络连接失败"提示 |
| R8-4 冷启动窗口 | ✅ | stale key 态 → `relay_key_invalid` 友好文案，非裸 401 |
| R8-6 key 失效闭环 | ✅ | mock 401 → `error_class="relay_key_invalid"` → `recoverFromKeyInvalid` 自愈 |

**功能 bug：0。子代理结论：Go。**

> 第 3 轮因 computer-use UI 授权弹窗超时（用户不在机器旁），改用真机
> API / WebSocket 直驱运行实例验证 —— 验证的是真实运行栈的实际行为。

## 5. 安全核查

- `llm_runtime.json` 全文无 `tsk_` 前缀 —— relay 设备 key 不明文落盘（实机确认）。
- 登出后 keyring 三槽位清空（`clearAllRelaySecrets`，实机确认）。

## 6. 待办（非阻断）

- **M6**：余额 402 错误码契约仍待与中转站侧最终确认。`classify_relay_error`
  当前按合理假设宽松匹配（标 `TODO(M6)`），契约确认后只需收紧匹配规则。
- R6 真实余额不足路径建议内测前用零余额账号或后端 mock 补一次冒烟。

## 7. 结论

✅ **Go** — relay 登录 + 中转站充值接入：文档按 M1-M6 修订、WI-R1~R5
实现完成、自动化测试全绿 0 回归、实机三轮测试（修复 2 bug + 补齐
error_class 链路后）**R1~R9 全部通过**，两个一票否决项（R3 登录即用、
R9 manual 零回归）达标，**0 功能 bug**。

最终回归基线：backend pytest **1750 passed** / frontend vitest
**279 passed** / `tsc -b` 通过，0 回归。
