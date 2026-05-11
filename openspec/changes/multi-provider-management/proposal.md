## Why

DeskPet 当前是**单 LLM provider 架构**：启动时从 keychain 读一个 chinzy 配置，所有对话（companion + code mode 所有 session）共享这一个 endpoint。问题：

1. **单点故障**——chinzy 抽风时所有任务全挂；之前实测 supervisor 自己也用同一个 chinzy → 同归于尽
2. **无法对比模型**——想试 deepseek-v4-pro vs claude-4.7 vs gpt-5 哪个写代码好，必须改配置文件 + 重启
3. **场景分流困难**——companion 闲聊用便宜的 ollama，code 模式用强的 chinzy，无法表达
4. **添加 provider 摩擦大**——今天想加 OpenRouter 试试，要改 config.toml + 重启 + 证明配置对
5. **Code 模式无 per-session 控制**——多个 code 项目想用不同模型是合理需求（性能/成本/语种偏好）

## What Changes

- **NEW** 后端 `LLMProviderRegistry` —— 内存 + 持久化的 provider 列表，按 priority 排序，支持 add / remove / reorder / enable / disable
- **NEW** `config.toml` 新 schema `[[llm.providers]]` 列表（向下兼容旧 `[llm.local]` 单 provider 自动迁移）
- **NEW** SessionDB 表 `code_session_provider`：per-session provider/model 覆盖
- **MODIFIED** chat handler / agent_loop：接受 provider_id override，按 chain fallback
- **MODIFIED** `OpenAICompatibleProvider`：每个 provider 一个独立实例，不共享全局 timeout
- **NEW** WebSocket IPC：`settings_providers_list/add/update/remove/reorder/toggle` + `code_session_set_provider/model`
- **NEW** 前端 SettingsPanel `LLM Providers` 区段：列表 + 拖拽（@dnd-kit/core）+ 增删 modal + 启用开关
- **NEW** Code 模式每张 session 卡片顶部加 provider/model 下拉
- **NEW** `services/dnd-kit` 依赖（如果 npm list 没有就装）

**BREAKING**: `[llm.local]` 单 provider schema **保留向下兼容**——启动时自动迁移成 `[[llm.providers]]` 列表第一个，旧用户无感升级。Reading 旧 schema → 一次性写回新 schema。

## Capabilities

### New Capabilities

- `provider-registry`: 后端 LLM provider 的内存模型 + 持久化 + chain selection 算法。包含 add/remove/reorder/toggle/get_chain/get_by_id 等操作 + 跨重启迁移。是新的"provider 管理大脑"，独立于 `tool-registry`。

- `code-session-provider-binding`: Code 模式每个 base_session_id 可选地绑定一个 provider_id + 可选的 model 覆盖。SessionDB 持久化，重启恢复。覆盖全局 chain（per-session 优先于 global priority）。

### Modified Capabilities

- `frontend-ipc-surface`: 新增 7 个 ws 消息（5 个 settings_providers_* + 2 个 code_session_set_*）+ 1 个出向 broadcast event `providers_changed`（broadcast 给所有 control conn 让所有 panel 状态同步）。

- `agent-loop`: chat handler 改为接受 optional `provider_id` 参数，按 chain 而不是单 provider 跑。失败时按 priority 自动 fallback 到下一个 enabled provider（非 PermanentToolError 才 fallback）。

- `code-mode`: CodeModeManager / SessionGridView 加 per-session provider 选择 UI + 持久化。改 IPC 的 `code_session_*_response` 加 `provider_id` + `preferred_model` 字段。

## Impact

### 代码影响

- 后端：~250 行新增（provider_registry.py ~120 + main.py 整合 ~50 + agent_loop chain ~40 + SessionDB migration + IPC handlers ~40）
- 前端：~300 行新增（SettingsPanel providers section ~150 + SessionGridView 卡片下拉 ~80 + sessionsStore 字段 ~30 + ws.ts dispatch ~40）
- 数据库：SessionDB v13 → v14 迁移，新表 `code_session_provider`
- 依赖：tauri-app 新增 `@dnd-kit/core` + `@dnd-kit/sortable` + `@dnd-kit/utilities`（若未装）

### 运行时影响

- **启动开销**：构建 N 个 provider 实例 vs 之前 1 个，每个增量 < 1ms（只构造 httpx client 配置，未实际连接）
- **chat fallback**：原本失败直接弹错，现在按 chain 试到第一个成功的；最坏情况 N 次 ConnectTimeout/RemoteProtocolError（每次 ~10s + retries）。**默认 chain ≤ 3 providers** 控制最坏 wallclock
- **per-session override**：DB lookup O(1) sqlite hit，可忽略
- **IPC 增量**：5 个 settings 消息 + 2 个 code_session 消息全部点击触发，非热路径

### 兼容性

- 旧 `[llm.local]` schema → 启动时一次性迁移到 `[[llm.providers]]`，写回 config.toml（保留旧字段做注释提示"已迁移"）
- 旧 SessionDB v13 → migration 005 新表 `code_session_provider`（空表，老 session 无绑定 = 走全局 chain）
- 旧 keychain 里的 api_key → 第一个 provider 仍引用 keychain（不强迁移到 plaintext）
- 不动 PyInstaller spec（无新文件需要 bundle，新依赖在 tauri-app frontend）

### 风险

- **拖拽 UX 跨浏览器/键盘**：用 @dnd-kit/core（业界标准 + a11y 友好）+ vitest 覆盖排序逻辑
- **Provider chain 死循环**：N 个 provider 都失败 → 走 P5-S2 已有的 ErrorEvent 路径 + AutoResume 弹 ask_user。无新风险
- **Per-session override 跟全局 chain 不一致**：UI 标"覆盖中（全局 chain 不参与）"明示
- **Migration race**：单实例 backend 启动 lifespan 串行做 migration，无并发问题
- **API key 暴露**：永远不在 ws response 里返回明文 key，前端展示 `sk-***` 占位；用户改 key 时 modal 输入新值上传

### 显式不做（Non-Goals）

- ❌ Provider 健康检查 ping（followup —— 跑个轻量 GET /models 探活）
- ❌ Per-provider 计费隔离（现有 BillingLedger 按 cloud/local 标签够用，未来可扩）
- ❌ 引入 OpenAI / Anthropic 原生 SDK（继续用 OpenAI-compat 协议，所有 provider 走同一 `OpenAICompatibleProvider` 类）
- ❌ Provider 模板市场 / preset import（followup）
