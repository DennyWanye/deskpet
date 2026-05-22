# TDD — relay 登录 + 中转站充值接入：技术设计 + 自动化测试规格

**关联**: `00-PRD.md`
**修订**: 2026-05-22 第 2 版 —— 按架构师评估 M1-M6 修订。第 1 版误判后端
机制（见下方"修订说明"）。
**原则**: 测试先行。每个模块下方用例在写实现前定稿；实现以"让用例全绿"
为完成标准。所有改动遵守 Strangler-Fig：relay edition 的行为变化只在
`VITE_AUTH_EDITION=relay` 下生效，manual/null edition 字节级不变。

> ## 修订说明（第 1 版 → 第 2 版）
>
> 架构师核对代码后指出第 1 版两处方向性错误，本版已改正：
> - **后端 LLM 热重载入口"已存在"**，不是要新增。`POST /config/cloud`
>   （`backend/main.py:1639` `update_cloud_config`）已能热替换 `local_llm`
>   且持久化；前端 binding `updateCloudConfig()`（`bindings/config.ts`）也
>   已存在，并已通过 Rust IPC（`commands.rs:30`）解决 release 构建
>   https→http 混合内容拦截。本版方案改为**复用**它。
> - **`backend/llm/keys.py` 不在 `[llm]` endpoint 的 key 解析链上** ——
>   它只服务 anthropic/openai/gemini 三个独立 adapter。`[llm]` 的 key 走
>   `main.py:198 _resolve_llm_api_key` + `config.py:56 resolve_cloud_api_key`。
>   本版**不改 keys.py**。

---

## A. 技术设计

### A1. 构建 edition 切换（WI-R1）

- 新增 `tauri-app/.env.relay`：`VITE_AUTH_EDITION=relay`。
- `package.json` 加脚本：`build:relay` = `vite build --mode relay`
  （Vite `--mode relay` 自动加载 `.env.relay`）。
- OSS 默认 `npm run build` 不变 → `import.meta.env.VITE_AUTH_EDITION`
  为 undefined → `getAuthAdapter()` 落 `"manual"`。
- 内测 Tauri 打包脚本走 `build:relay`。
- **不改** `auth/index.ts` 的 `getAuthAdapter` / `buildAdapter` 逻辑
  （已支持三 edition）。

### A2. 登录态 → 后端 LLM 桥接（WI-R2，核心 —— 复用现成入口）

**现状（已存在，本次复用，不重写）**：

- `POST /config/cloud`（`main.py:1639` `update_cloud_config`）：接收
  `{base_url, model, api_key?, strategy?}`，新建 `OpenAICompatibleProvider`
  并**热替换模块级 `local_llm`**（`main.py:1686`，"no restart needed"），
  再 `_save_llm_runtime_overrides()` 持久化。
- 前端 `bindings/config.ts` 的 `updateCloudConfig(secret, update)` →
  Rust IPC `commands.rs:update_cloud_config` → 后端（Rust 持有
  `SHARED_SECRET`，前端不碰；解决 https→http 混合内容）。Rust 侧把
  `update` 当不透明 `Value` 透传，**新增 JSON 字段无需改 Rust**。

**唯一需要的后端改动 —— `persist_key` 旁路（M3）**：

当前 `update_cloud_config`（`main.py:1697`）：只要 `body.api_key` 非空
就把 key 明文写进 `llm_runtime.json`。relay 的 `tsk_xxx` 是轮换的短期
设备 key，**绝不能明文落盘**。改动：

- `CloudConfigRequest`（`main.py:1587`）新增字段 `persist_key: bool = True`。
- `update_cloud_config` 逻辑：
  - key **始终**应用到内存 `local_llm`（live 生效）。
  - `persist_key=True`（默认，manual edition 行为不变）→ 既有逻辑：
    `body.api_key` 非空则写入 `llm_runtime.json`。
  - `persist_key=False`（relay 调用）→ `base_url`/`model`/`temperature`
    写 `llm_runtime.json`，**`api_key` 永不写入**。
- `CloudConfigUpdate`（`bindings/config.ts`）TS 接口新增可选
  `persist_key?: boolean`。
- 冷启动安全：`persist_key=False` 时 json 无 key → 下次重启后
  `local_llm` 暂无有效 key，直到前端 `restoreSession` → bridge 重新推送。
  这是预期的（见 A5 冷启动窗口处理）。

**前端 `relayProviderBridge.ts`（新文件，归属 relay 闭源资产）**：

- 输入：`RelayAuthAdapter` 的 `listProviders()` 首条 provider。
- 动作：调 `updateCloudConfig(secret, {base_url, model, api_key: tsk_xxx,
  persist_key: false})`。
- 触发点：`RelayEdition` 在以下时机调 bridge：
  `restoreSession` 成功后、`onEvent({type:"login"})`、
  `onEvent({type:"providers-updated"})`（key 轮换跟随）。
- **并发收敛（O1 → 提级为必做）**：三个触发点可能近乎同时（登录会先后
  emit `login` 与 `providers-updated`）。bridge 内部维护单个 inflight
  promise + 末值防抖，串行化对 `updateCloudConfig` 的调用，避免后端
  `local_llm` 全局变量赋值竞争。
- bridge 失败要**可感知**：失败时 emit 一个事件 / 设状态，让
  `RelayEdition` 能给用户"模型配置失败，请重试"提示，而非静默（避免
  "登录成功了但一聊天就报错"）。

### A3. onboarding 步骤数组化重构（WI-R3，M5）

`OnboardingWizard.tsx` 当前 `OnboardingStep = 1 | 2 | 3` 硬编码，step
渲染、step-dot、`nextStepAllowed`、上一步/下一步导航全基于固定三步。
**不是加 `edition` 入参能解决的** —— 做结构化重构：

- 把步骤定义抽成数组：`StepDef[]`，每个 `StepDef` 含 `id`、`render`、
  `canAdvance(state)`。
- `manual`/`null` edition：`[welcome, connectModel, ready]`（三步，行为
  与今日逐字节一致）。
- `relay` edition：`[welcome, ready]`（两步，**无 connectModel** —— 模型
  由登录自动配置）。
- step-dot 渲染、导航索引、`nextStepAllowed` 全部基于数组长度 + 当前
  index，不再写死 `1|2|3`。
- 导出的纯函数 `nextStepAllowed` 改签名为按 `StepDef` + index 判定，
  保留可单测性。
- 启动时序由 `App.tsx` 协调：relay edition 时 `RelayEdition` 的强制
  `RelayAuthModal` 优先级高于 onboarding；登录成功后才渲染 onboarding。

### A4. 充值链接（WI-R4）

- 新增 `auth/relayConfig.ts`：集中常量 `RECHARGE_URL`、`DEVICE_CONSOLE_URL`
  等（归属 relay 闭源资产）。
- `AccountSettingsPanel` "账户余额"区加"去充值"按钮 → `@tauri-apps/
  plugin-opener` 的 `openUrl(RECHARGE_URL)`。
- `billing/ledger.py` 80% 预算警告文案追加充值提示（纯文案改动）。

### A5. 错误边界与 key 轮换重试闭环（WI-R5，M4）

**key 轮换 / 401 重试 —— 前端驱动的跨层闭环**（M4，纠正第 1 版"后端重试
一次"的错误 —— 后端没有 relay 凭证，自己重试还是旧 key）：

```
聊天请求 → 后端用当前 local_llm（可能持旧 tsk_）调中转站
  └─ 中转站 401（key 已轮换失效）
       └─ 后端把 401 / key 失效如实透传给前端（不自行重试）
            └─ 前端 RelayAuthAdapter.listProviders()（rotate）拿新 tsk_
                 └─ relayProviderBridge → updateCloudConfig(persist_key=false)
                      └─ 前端重发该聊天请求（最多 1 次）
```

- 后端：对 LLM 调用的中转站 401 / 余额 402，归一为结构化错误码
  （`relay_key_invalid` / `insufficient_balance`）透传，**不静默吞**。
- 前端：聊天层捕获 `relay_key_invalid` → 触发上面的闭环重试一次；
  仍失败 → 提示用户重新登录。

**其它边界**：

| 场景 | 期望 |
|---|---|
| 登录失败（密码错）| RelayAuthModal 内联红字，不关窗 |
| 网络断 / 中转站不可达 | 明确提示，可重试 |
| access_token 过期 | RelayAuthAdapter 既有 refresh（不动）；refresh 失败 emit `logout` → 回登录窗 |
| 余额耗尽（402）| 聊天气泡友好提示 + 去充值链接，不裸报错 |
| 离线启动 | `restoreSession` 失败不崩；提示需联网 |
| 冷启动窗口 | restoreSession 成功 → bridge 推送前的几秒，`local_llm` 无有效 key；此窗口内聊天给"正在配置模型，请稍候"而非裸 401 |
| bridge 推送失败 | RelayEdition 给"模型配置失败，点此重试" |

### A6. 余额错误码契约（M6 —— 动工硬前置）

- **前置依赖**：中转站余额不足时 LLM 调用返回的 HTTP 状态码 + 响应体
  结构**需与中转站侧对齐**（参考 `docs/中转站建议.md`）。
- 在契约确认前：实现按**合理假设**进行 —— 假设余额不足为 HTTP 402，
  响应体含可识别字段（如 `error.type` 含 "balance"/"quota"/"insufficient"
  其一）。后端做宽松匹配归一为 `insufficient_balance`。
- 所有基于此假设的代码 + 测试在注释/文档标注 **`TODO(M6): 待中转站
  402 契约最终确认`**。契约确认后只需调整匹配规则，不动链路结构。

---

## B. 自动化测试规格

### T1 · 构建 edition（前端 vitest）

`auth/auth.test.ts`（在既有文件追加）

| # | 用例 | 断言 |
|---|---|---|
| T1-1 | `VITE_AUTH_EDITION` 未设 → `getAuthAdapter()` | ManualAuthAdapter |
| T1-2 | `buildAdapter("relay")` | RelayAuthAdapter |
| T1-3 | `buildAdapter("manual")` / `("null")` | 对应 adapter |
| T1-4 | 未知 edition | 抛错（既有 exhaustive 检查）|

### T2 · relayProviderBridge（前端 vitest）

`auth/__tests__/relayProviderBridge.test.ts`

| # | 用例 | 断言 |
|---|---|---|
| T2-1 | provider 非空 → `bridge.apply()` | 调 `updateCloudConfig` 一次，参数含正确 base_url/model/api_key 且 `persist_key===false` |
| T2-2 | provider 列表为空 | 不调后端，不抛错 |
| T2-3 | `providers-updated` 再次触发（key 轮换）| bridge 用新 key 重推 |
| T2-4 | `updateCloudConfig` 抛错 | 捕获、不崩；emit/置"bridge 失败"状态（可感知）|
| T2-5 | 并发触发（login + providers-updated 几乎同时）| 串行化，后端只看到串行调用，无竞争；末值生效 |
| T2-6 | 聊天收到 `relay_key_invalid` → 闭环重试 | 触发 `listProviders` 轮换 → 再 `updateCloudConfig` → 重发一次 |

### T3 · 后端 persist_key 旁路（backend pytest）

`tests/test_relay_llm_bridge.py`

> 注：`update_cloud_config` 的热替换本身**已有既有测试覆盖**
> （`tests/test_config_cloud_endpoint.py` 等）—— 本组只测**新增的
> `persist_key` 旁路行为**，不重复测已存在的热替换。

| # | 用例 | 断言 |
|---|---|---|
| T3-1 | `persist_key=false` + 非空 api_key | 内存 `local_llm` 用上新 key（live 生效）|
| T3-2 | `persist_key=false` 落盘 | `llm_runtime.json` 写入 base_url/model，**不含 api_key**；全文无 `tsk_` |
| T3-3 | `persist_key=true`（默认）+ 非空 api_key | 既有行为不回归：api_key 写入 json |
| T3-4 | `persist_key` 字段缺省 | 等价 `true`（向后兼容 manual edition / 既有调用方）|
| T3-5 | `persist_key=false` 幂等：同参推两次 | 第二次无副作用、不报错 |
| T3-6 | `CloudConfigRequest` 接受 `persist_key` 字段 | pydantic 校验通过；未知调用方不传也 OK |

### T4 · onboarding 步骤数组化（前端 vitest）

`components/OnboardingWizard.test.ts`（在既有文件追加 / 新增 relay 用例）

| # | 用例 | 断言 |
|---|---|---|
| T4-1 | edition=relay → 步骤数组 | 长度 2，不含 `connectModel` 步 |
| T4-2 | edition=manual → 步骤数组 | 长度 3，含 `connectModel`（回归）|
| T4-3 | `nextStepAllowed` 在 relay：从 welcome 前进 | 允许（不依赖"测试连接"）|
| T4-4 | `nextStepAllowed` 在 manual 的 connectModel 步 | 仅 testState==="ok" 时允许（回归）|
| T4-5 | step-dot 数量 | 跟随步骤数组长度（relay 2 点 / manual 3 点）|
| T4-6 | 导航：relay 末步索引 | 末步显示"完成"，无"下一步"；上一步索引不串 |

### T5 · 充值链接（前端 vitest）

`auth/AccountSettingsPanel.test.tsx`（在既有文件追加）

| # | 用例 | 断言 |
|---|---|---|
| T5-1 | AccountSettingsPanel 渲染 | 存在"去充值"按钮（testid `account-recharge-btn`）|
| T5-2 | 点"去充值" | 调 opener `openUrl(RECHARGE_URL)` 一次 |
| T5-3 | `RECHARGE_URL` 常量 | https，指向中转站域名 |

### T6 · 余额不足 / key 失效错误链路（backend pytest + 前端 vitest）

| # | 用例 | 断言 |
|---|---|---|
| T6-1 | 后端：LLM 调用遇 402（按 M6 假设结构）| 归一为 `error_class="insufficient_balance"`，透传 |
| T6-2 | 后端：LLM 调用遇 401（key 失效）| 归一为 `relay_key_invalid`，透传，不自行重试 |
| T6-3 | 前端：收到 `insufficient_balance` | 聊天气泡渲染含"去充值"链接 |
| T6-4 | `billing/ledger.py` 80% 警告文案 | 含充值提示（字符串断言）|

### T7 · 回归门控

| 套件 | 基线 | 通过线 |
|---|---|---|
| backend pytest | 1734 | 新增后 ≥1740，**0 回归**（重点跑 `test_config_cloud_endpoint` 等既有 LLM 配置用例）|
| frontend vitest | 255 | 新增后 ≥275，**0 回归**（含 manual edition 全部既有 auth / onboarding 用例）|
| Rust cargo test | 59 | 0 回归（`commands.rs` 透传 `Value`，本次不改 Rust）|

**完成定义**：T1-T6 全绿 + T7 三套零回归 + PRD §7 DoD 全部满足。

---

## C. 实施排期

| Sprint | 内容 | 依赖 |
|---|---|---|
| S1 | WI-R1 构建 edition + T1 | 无 |
| S2 | WI-R2 后端 `persist_key` 旁路 + T3 | 无（后端独立、改动面小）|
| S3 | WI-R2 前端 `relayProviderBridge`（含并发收敛 + 失败可感知）+ T2 | S2 |
| S4 | WI-R3 onboarding 数组化重构 + T4 | S1 |
| S5 | WI-R4 充值链接 + WI-R5 错误边界（含 M4 重试闭环）+ T5/T6 | S2/S3 |
| S6 | 全量回归 T7 + 人工测试（见 `02-manual-test-cases.md`）| 全部 |

S2 与 S1/S4 可并行（后端 vs 前端）。S2 改动面小（仅 `persist_key` 旁路）。

> **M6 硬前置**：S5 的余额 402 链路在中转站契约确认前按 A6 的合理假设
> 实现，代码 + 测试标注 `TODO(M6)`。
