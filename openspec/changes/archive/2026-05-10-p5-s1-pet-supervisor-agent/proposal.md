## Why

P4-S22/S23 把 Code 模式的多 session 能力做出来了，但**没有任何机制处理"卡住"**：

- LLM 可能反复用同一个失败命令调 `bash_run`，max_iterations=50 用满才停（用户已浪费 5+ 分钟和大量 token）
- `chat_v2_error` 喷出后，session 就僵在那里，没人催它
- 用户 permission 弹窗忽略后 session 永远停在 `permission` 状态
- 多个 session 并发时，用户根本不知道哪个出问题了——code panel 里默默挂着不会主动求救
- "假完成"——LLM 说做完了但 todo 还有一半 pending 的情况无人察觉

桌宠的核心定位是"陪伴 + 监督员工"，但目前它在 Code 模式下只是被动渲染——**它该当老板用，不是当前台用**。这次把"主动监督"能力补上：桌宠定期偷看主 agent 在干嘛，发现卡住了用 LLM 自检判断怎么干预，并通过 motion + 气泡可视化告诉用户"哪个 session 现在最危险"。

## What Changes

### 后端
- **新增** `backend/agent/session_activity.py` — 全局活动追踪，每个 agent_loop 事件登记 last_event_ts、最近 5 个事件、工具调用签名滑动窗口
- **新增** `backend/agent/supervisor.py` — Watchdog Loop（独立 asyncio.Task，60s 扫描一次）+ snapshot builder + supervisor LLM 调用 + SupervisorAction 派发
- **新增** `backend/agent/nudge_queue.py` — 异步队列 + asyncio.Lock 重入安全 + cap=3 防堆积
- **修改** `backend/main.py` — 集成 supervisor loop、注入点（_msgs 构造时 pop hints）、done_callback 触发 follow-up
- **修改** `backend/deskpet/code_mode/state.py` — code_mode_exit 时调 nudge_queue.clear(sid)
- **修改** 工具调度层 — 单工具硬超时（解决 bash_run/MCP 卡住的形态 3/6 兜底）
- **新增** SessionDB v14 迁移：新表 `supervisor_hints`（审计用，hint 文本 + ts + sid + action）

### 前端
- **新增** `tauri-app/src/pet-state/PetStateMachine.ts` — 5 状态机（idle/working/worried/alert/intervening）+ 滞后阈值 + 最短驻留时间
- **新增** `tauri-app/src/components/PetSupervisorBubble.tsx` — 桌宠头顶气泡，颜色随 severity，点击跳转最危险 session
- **修改** `tauri-app/src/App.tsx` — 接收 `supervisor_alert` ws 事件 + 计算 max severity_score session + 喂给 PetStateMachine
- **修改** `tauri-app/src/components/Live2DCanvas.tsx` — 扩展 Live2DHandle：`setIdleSubset` / `setBlinkRate` / `setHeadTilt`
- **修改** `tauri-app/src/stores/sessionsStore.ts` — 新增 severity 字段
- **修改** `tauri-app/src/code-panel/ws.ts` — dispatch supervisor_alert 到 store
- **修改** `tauri-app/src/components/SessionGridView.tsx` — tile 边框颜色随 severity
- **修改** `tauri-app/src/components/SettingsPanel.tsx` — 新增 supervisor 开关 + LLM provider 选择

### IPC 协议
- **新增** ws 事件类型 `supervisor_alert`：payload = `{session_id, severity, action, diagnosis, user_message, suggested_buttons[]}`
- **新增** ws 消息类型 `supervisor_user_choice`：用户点气泡按钮的回包

## Capabilities

### New Capabilities

- `pet-supervisor`: 后端 Watchdog Loop + 独立 supervisor LLM 调用 + SupervisorAction 决策协议 + nudge_queue 注入回路。这是 supervisor 的"大脑 + 神经"。

- `pet-state-machine`: 前端桌宠状态机 — severity_score 计算、5 状态切换规则、滞后阈值、最短驻留时间、Live2D motion/blink/head-tilt 参数映射。这是桌宠"看起来像在监督"的视觉表现层。

### Modified Capabilities

- `agent-loop`: 新增 SessionActivity hook —— 每个 AgentEvent emit 时附带写入 session_activity，agent_loop.py 本身不动，hook 在 main.py 的事件转发处加。

- `code-mode`: 退出 Code 模式时清理 supervisor 状态（nudge_queue.clear、session_activity.drop）。

- `frontend-ipc-surface`: 新增 `supervisor_alert` 出向事件 + `supervisor_user_choice` 入向消息 + `supervisor_toggle` 入向消息（开关 supervisor）。

## Impact

### 代码影响
- 后端: ~600 行新增（supervisor 模块 ~400 + main.py 集成 ~100 + 工具超时 ~100）
- 前端: ~800 行新增（PetStateMachine ~250 + Bubble ~200 + Live2D 扩展 ~100 + 路由集成 ~250）
- 数据库: SessionDB v13 → v14 迁移（新表 supervisor_hints）

### 运行时影响
- 内存: session_activity 每 session ~2KB（5 事件 × 字段），可控
- LLM 成本: 每个卡住 session 一次额外 supervisor 调用（snapshot 约 800 tokens 输入 + 200 tokens 输出，按 gpt-5-mini 计每次约 $0.001）
  - 60s 扫描 + 12min 去重 → 单 session 每小时最多 5 次 supervisor 调用
  - 串行扫描 → 不会并发打爆 provider rate limit
- 延迟: nudge follow-up 比用户重打慢 1 个 iteration（"等当前轮做完"）

### 兼容性
- 关 supervisor (settings toggle off) 时所有 P4-S22/S23 行为不变 → 默认开但可降级
- 老 SessionDB 走迁移自动升 v14；不升级 supervisor 表为空，功能降级到无审计模式
- 不需要重新打包（PyInstaller 后端代码改动；不引入新原生依赖）

### 风险
- supervisor 自己卡住 → 30s 硬超时 + 失败回退 green
- nudge 误导主 agent → cap=3 + 二次卡住升级为 ask_user
- Hiyori 表达力不足 → 接受能力上限，气泡承担语义
- 多 session 评分误判（盯错最危险的）→ 评分公式标定阶段需用真实 case 校准
