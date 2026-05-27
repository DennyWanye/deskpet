# PRD — 内测付费版：relay 登录 + 中转站充值接入

**创建日期**: 2026-05-22
**目标版本**: `0.6.0-beta`（100 人内测付费版）
**状态**: 已过架构评审，按 M1-M6 修订（第 2 版，2026-05-22）
**关联**: `tauri-app/src/auth/`（已有 relay 体系）、`docs/中转站建议.md`、
`plans/2026-05-22-builtin-skills-beta-plan.md`

> **第 2 版修订说明**：第 1 版误判后端机制（以为 LLM 热重载入口需新增、
> 以为改 `keys.py` 有效）。架构师核对代码后纠正：`POST /config/cloud`
> 已是完整热重载入口，本版改为**复用**它；后端唯一改动是给它加一个
> `persist_key=false` 旁路（key 不明文落盘）。详见 `01-TDD.md` 修订说明。

---

## 1. 背景

DeskPet 内测要做到"**注册/登录 → 自动配好模型 → 开箱即用**"。代码库里
已有一套相当完整的 relay（中转站）账户体系（`tauri-app/src/auth/`，
横跨 W1~W3.3 多个 commit），但**没有接通最后一公里**：

- **缺口 A — 构建 edition 默认不是 relay**：`VITE_AUTH_EDITION` 无任何
  `.env` 配置，默认 `"manual"`，导致 `RelayEdition` 登录 UI 根本不挂载。
- **缺口 B — 登录态未桥接到后端 LLM 调用**：`RelayAuthAdapter` 登录后把
  中转站下发的 `tsk_xxx` 设备 key 存进 keyring 槽位
  `deskpet-relay/device_key`，但后端 `[llm]` 调用从不读这个槽位
  （后端 key 来源是 `llm_runtime.json` / 环境变量 / keyring 槽位
  `deskpet/<provider>_api_key`）。两个命名空间之间没有桥。

结果：即便用户登录成功，聊天仍用不上中转站的 key。

## 2. 现有资产盘点（已存在，本次复用，不重写）

| 资产 | 位置 | 状态 |
|---|---|---|
| AuthAdapter 抽象（Null/Manual/Relay） | `auth/types.ts` `auth/index.ts` | ✅ 完成 |
| RelayAuthAdapter（login/register/refresh、device id、keyring 三槽位、`/v1/providers` 拉取 + key 轮换） | `auth/RelayAuthAdapter.ts` | ✅ 完成 |
| 登录/注册弹窗 | `auth/RelayAuthModal.tsx` | ✅ 完成 |
| 账户面板（余额/用量/改密/设备/登出） | `auth/AccountSettingsPanel.tsx` | ✅ 完成 |
| relay edition UI 外壳（restoreSession + 强制登录 modal + 账户 pill） | `auth/RelayEdition.tsx` | ✅ 完成 |
| keyring 三槽位 + device id 的 Rust IPC | `bindings/relay.ts` + `src-tauri` | ✅ 完成 |
| App.tsx 条件挂载 RelayEdition | `App.tsx` | ✅ 完成 |
| 后端 LLM **热重载入口** `POST /config/cloud` | `main.py:1639` `update_cloud_config` | ✅ 完成（热替换 `local_llm` + 持久化 `llm_runtime.json`，**无需重启**）|
| 前端配置推送 binding（经 Rust IPC，解决混合内容拦截）| `bindings/config.ts` `updateCloudConfig` + `commands.rs:30` | ✅ 完成 |
| 成本护栏 80% 警告 | `backend/billing/ledger.py` | ✅ 完成（WI-04）|
| 中转站集成指南（流式/重试/池） | `docs/中转站建议.md` | ✅ 文档 |

**本 PRD 只补"接线"，不重做账户体系。**

## 3. 目标与非目标

### 目标

1. 内测包构建为 relay edition，启动即进登录/注册流程。
2. 登录成功后**自动**把中转站下发的 provider（base_url + model + key）
   配置到后端，聊天和 8 个办公技能直接可用，用户零手填。
3. 中转站 key **轮换**时后端能跟上（不靠重启）。
4. 付费：账户面板 + 余额不足处提供**跳转链接**，用户在中转站网页用
   同一账号登录后充值。
5. OSS 默认构建（manual edition）**零回归**。

### 非目标

- ❌ 不做应用内支付（不集成微信/支付宝 SDK）——付费只给跳转链接。
- ❌ 不做账户后端（中转站已有注册/鉴权/计费后端）。
- ❌ 不做多 provider 选择 UI——内测固定 中转站。
- ❌ 不改 RelayAuthAdapter 的登录/注册/refresh 协议本身。

## 4. 用户故事

- **US-1**：新用户装好 DeskPet → 弹登录窗 → 点"注册" → 填邮箱密码 →
  注册成功自动登录 → 模型自动配好 → 直接对桌宠说话就有回应。
- **US-2**：老用户重开 DeskPet → `restoreSession` 自动恢复登录 → 不弹
  登录窗，直接可用。
- **US-3**：用户想充值 → 点账户面板"去充值" → 浏览器打开中转站充值页 →
  用同一账号登录 → 充值 → 回到 DeskPet 继续用。
- **US-4**：用户余额耗尽 → 聊天报"余额不足" + 一个"去充值"链接，而不是
  裸的 402 报错。
- **US-5**：用户登出 → 三个 keyring 槽位清空 → 后端 LLM 覆盖失效 → 再次
  回到登录窗。

## 5. 功能需求（Work Items）

### WI-R1 · 构建 edition 切换

- 内测构建注入 `VITE_AUTH_EDITION=relay`（`.env.relay` 或构建脚本参数）。
- OSS 默认构建不带该 env → 仍是 `manual`。
- 提供构建命令文档：`npm run build:relay` 之类（或现有脚本加参数）。
- **验收**：relay 构建启动弹登录窗；manual 构建行为与今日完全一致。

### WI-R2 · 登录态 → 后端 LLM endpoint 桥接（**核心**）

登录成功（或 `restoreSession` 成功）且 `/v1/providers` 返回 provider 后，
把 provider 配置推给后端，使 `[llm]` 调用走中转站。

**方案（已定，复用现成入口）**：

后端 `POST /config/cloud`（`update_cloud_config`）**已是**完整热重载
入口——热替换 `local_llm`、无需重启；前端 `updateCloudConfig()` binding
也已存在并经 Rust IPC 代理（避开 release 构建混合内容拦截）。本需求
直接复用，不新建任何重载入口。

只需两处改动：

1. **后端 `persist_key` 旁路（M3，唯一后端改动）**：`update_cloud_config`
   当前只要 `api_key` 非空就把它明文写进 `llm_runtime.json`。relay 的
   `tsk_xxx` 是轮换短期 key，**绝不能明文落盘**。给 `CloudConfigRequest`
   加 `persist_key: bool = True`：`false` 时 key 只进内存 `local_llm`
   （live 生效），`base_url`/`model` 仍持久化，**`api_key` 不写 json**。
   `persist_key` 默认 `true` → manual edition / 既有调用方零回归。
2. **前端 `relayProviderBridge`**：登录 / `restoreSession` / key 轮换
   （`providers-updated` 事件）后，把 `{base_url, model, tsk_xxx}` +
   `persist_key:false` 喂给 `updateCloudConfig()`。bridge 内部串行化
   （避免并发竞争后端 `local_llm`），失败要让用户可感知。

**key 轮换跟随**：`GET /v1/providers`（rotate）每次发新 `tsk_xxx`，旧 key
立即失效。`RelayAuthAdapter` 每次轮换后 emit `providers-updated` →
bridge 重推。运行中 401（旧 key 失效）的处理见 WI-R5（前端驱动闭环）。

- **不做**：不改 `backend/llm/keys.py`（它不在 `[llm]` 调用的 key 解析
  链上）；不新建后端重载 endpoint。
- **验收**：登录后无需重启，聊天立即走中转站；中转站后台能看到来自本
  设备的请求；key 轮换后旧 key 失效但聊天不中断；`llm_runtime.json`
  全文无 `tsk_` 前缀。

### WI-R3 · onboarding 流程适配 relay edition

- relay edition 下，onboarding 向导的 **Step 2（手填 base_url/model/
  api_key + 测试连接）** 不再适用——provider 由登录自动配置。
- 方案：relay edition 时 onboarding **跳过 Step 2**，登录由
  `RelayEdition` 的强制 `RelayAuthModal` 承担；登录成功后再走 onboarding
  其余步骤（欢迎页等）。
- 启动时序：App 挂载 → `RelayEdition` `restoreSession` → 未登录则强制
  登录窗 → 登录成功 → provider 自动配好 → onboarding 剩余步骤（如有）。
- **验收**：relay edition 全新安装首启不出现"手填模型"步骤；manual
  edition 的 onboarding 三步保持原样。

### WI-R4 · 充值跳转链接

- `AccountSettingsPanel` 的"账户余额"区新增**"去充值"按钮** → Tauri
  opener 打开中转站充值页（如 `https://your-llm-relay.example.com/console/billing`）。
- 余额不足 / 扣费失败的错误提示里同样带"去充值"链接。
- 复用 `billing/ledger.py` 的 80% 预算警告（WI-04）——警告文案里加充值
  链接。
- 用已装的 `tauri-plugin-opener` 打开外部浏览器（不在应用内嵌网页）。
- **验收**：点击打开系统浏览器到充值页；用户用同账号登录网页可充值。

### WI-R5 · 错误与边界

| 场景 | 期望行为 |
|---|---|
| 登录失败（密码错） | RelayAuthModal 内联红字报错，不关窗 |
| 网络断 / 中转站不可达 | 明确提示"无法连接中转站"，可重试 |
| access_token 过期 | 自动 refresh；refresh 也失败 → 回登录态 |
| 余额耗尽（LLM 调用返 402/余额错误）| 后端归一为 `insufficient_balance` 透传；聊天气泡友好提示 + 去充值链接，不裸报错 |
| 离线启动 | `restoreSession` 失败不崩；提示需联网登录 |
| key 失效（旧 `tsk_` 轮换后被用，中转站返 401）| **前端驱动的跨层重试闭环**：后端把 401 归一为 `relay_key_invalid` 透传（不自行重试，后端没有 relay 凭证拿不到新 key）→ 前端 `listProviders()` 轮换拿新 key → `relayProviderBridge` 重推 → 前端重发该聊天一次 |
| 冷启动窗口 | `restoreSession` 成功 → bridge 推送完成前的几秒 `local_llm` 无有效 key：此窗口聊天给"正在配置模型，请稍候"，不裸 401 |
| bridge 推送失败 | 登录成功但 bridge 失败 → `RelayEdition` 显式提示"模型配置失败，点此重试"，不静默 |

## 6. 关键时序

```
启动（relay edition）
  └─ App mount → RelayEdition useEffect
       └─ adapter.restoreSession()
            ├─ keyring 有有效 refresh → 刷新 → 已登录
            │    └─ listProviders() → {base_url, model, tsk_xxx}
            │         └─ relayProviderBridge → updateCloudConfig()
            │              └─ Rust IPC (commands.rs，持 SHARED_SECRET)
            │                   └─ POST /config/cloud (persist_key=false)
            │                        └─ 热替换 local_llm → 聊天可用
            └─ 无有效凭证 → 强制 RelayAuthModal
                 └─ 用户 登录/注册 成功
                      └─ （同上 listProviders → bridge → 后端）

注：bridge 推送完成前的窗口期聊天给"正在配置模型"提示，不裸 401。

充值
  └─ AccountSettingsPanel "去充值" → opener → 浏览器中转站充值页
```

## 7. 验收标准（DoD）

1. relay 构建：全新安装首启 → 注册 → 自动登录 → **不手填任何模型配置**
   → 对桌宠说话有正常回应 → 触发一个办公技能（如 excel-generate）成功。
2. 重启后 `restoreSession` 自动恢复，不再弹登录窗。
3. 中转站后台能看到来自该设备的真实请求。
4. 账户面板"去充值"打开浏览器到充值页。
5. 余额不足走友好提示 + 充值链接（可用测试账号或 mock 验证）。
6. 登出后凭证清空、LLM 覆盖失效、回到登录窗。
7. OSS manual 构建零回归（onboarding 三步、Settings 手填 provider 不变）。
8. 三套测试（backend pytest / Rust cargo / frontend vitest）全绿。

## 8. 风险

| # | 风险 | 缓解 |
|---|---|---|
| R1 | ~~后端 LLM endpoint 仅启动时加载~~ —— 已澄清：`POST /config/cloud` 已是完整热重载入口 | 复用现成入口；后端唯一改动是 `persist_key` 旁路，改动面极小 |
| R2 | key 轮换与并发请求竞态（旧 key 已失效、新 key 未到）| WI-R5 前端驱动的跨层重试闭环；bridge 内串行化避免并发竞争 `local_llm` |
| R3 | relay edition 与 onboarding marker 交互（先登录还是先 onboarding）| WI-R3 明确时序：强制登录在 onboarding 之前 |
| R4 | **（M6 硬前置）** 中转站充值页 URL / 余额 402 错误码契约未最终确认 | 动工前与中转站侧对齐；未确认前按合理假设 + 标注 `TODO(M6)` 实现，链路结构不依赖具体码 |
| R5 | mid-turn 热替换：bridge 在聊天进行中热换 `local_llm` 可能 mid-turn 换 provider | bridge 仅在无 inflight 聊天时热替换，或接受并显式记录；`main.py:168` 单 endpoint 设计本就规避换嗓音 |
| R6 | OnboardingWizard 步骤硬编码 `1\|2\|3`，改动面比"加入参"大 | M5：步骤数组化重构，导航/dot 基于数组，一次性解决 |
| R7 | 闭源/OSS 分仓：`RelayAuthAdapter` 未来移入闭源仓 | `relayProviderBridge.ts` / `relayConfig.ts` 随 `RelayAuthAdapter` 归属 relay 闭源资产；保持 edition 构建期可切换 |
| R8 | 多设备：改密 revoke 全部 refresh、每设备独立轮换 key | 设备 A 收到 401/refresh 失败 → 既有 `logout` 路径回登录窗（不崩）|
