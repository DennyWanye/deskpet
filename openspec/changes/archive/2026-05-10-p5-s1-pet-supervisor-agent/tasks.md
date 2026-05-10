## 1. S1 — 活动追踪 + Watchdog Loop + 工具硬超时

### 1.1 SessionActivity 数据结构

- [x] 1.1 创建 `backend/agent/session_activity.py` —— `SessionActivity` 数据类（last_event_ts / status / recent_events / tool_signature_window / current_iteration / max_iterations）
- [x] 1.2 实现 `SessionActivityStore` 单例（dict[sid, SessionActivity] + asyncio.Lock）
- [x] 1.3 `bump(sid, event)` 方法：append 到 ring buffer (cap 5) + 更新 last_event_ts + 更新 signature window
- [x] 1.4 `drop(sid)` 方法：删除条目（Code mode exit 时调用）
- [x] 1.5 `get(sid)` 方法：只读快照（用于 build_snapshot）
- [x] 1.6 工具调用签名哈希辅助函数 `args_hash(args: dict) → str`（稳定 hash，忽略 dict key 顺序）

### 1.2 集成到 main.py 事件转发处

- [x] 1.7 在 `backend/main.py:2197` 的 `async for ev in _agent.run(...)` 循环里，每个 event 在转发到 ws 之前调用 `session_activity.bump(_sid, ev)`
- [x] 1.8 仅对 Code-mode session bump（用 `_in_code_mode` 已有变量过滤）
- [x] 1.9 `chat_v2_error` 事件特殊处理：标记一个临时 `error_pending_supervisor=True` flag
- [x] 1.10 `code_mode_exit` IPC handler 调 `session_activity.drop(sid)`

### 1.3 Watchdog Loop

- [x] 1.11 创建 `backend/agent/watchdog.py` —— `WatchdogLoop` 类（独立 asyncio.Task）
- [x] 1.12 启动逻辑：backend startup 完成 30s 后启动；从 `[supervisor].enabled` 读配置；disabled 时不启动
- [x] 1.13 主循环：每 60s tick；扫描 `code_mode_manager.all_sessions()` 拿 enabled sids
- [x] 1.14 触发判定：`status==error AND error_pending_supervisor` OR `(now - last_event_ts > 900) AND status in {running, permission}`
- [x] 1.15 12 分钟去重：维护 `_last_scan_ts: dict[sid, ts]`，跳过近期已扫的
- [x] 1.16 异常隔离：tick 内任何 exception catch + 日志 + 不退出主循环
- [x] 1.17 注册到 `service_context["watchdog"]` 供其它模块查询状态

### 1.4 工具级硬超时

- [x] 1.18 在 `backend/deskpet/tools/registry_v2.py`（或同等 dispatch 入口）的 `dispatch(name, args, task_id)` 中包 `asyncio.wait_for`
- [x] 1.19 默认超时 60s，工具元数据可覆写（bash_run=300s，MCP=60s）
- [x] 1.20 超时返回 `{"ok": false, "error": "tool_timeout", "tool": name}` 不抛异常
- [x] 1.21 给 bash_run 工具添加 timeout 元数据 (300s)

### 1.5 配置 + 测试

- [x] 1.22 `config.toml` 新增 `[supervisor]` 段（enabled/scan_interval_seconds/stuck_threshold_seconds/max_hints_per_session/llm_provider）
- [x] 1.23 单测：`test_session_activity.py`（bump/drop/signature window 计数 / cap 5）
- [x] 1.24 单测：`test_watchdog.py`（mock LLM call 跳过；只测触发判定 + 去重 + 异常隔离）
- [x] 1.25 单测：`test_tool_timeout.py`（构造慢工具，验证 timeout 返回 ok=false 不破坏 loop）
- [x] 1.26 端到端 manual smoke：dev backend 启动 + 触发 15min 阈值（用 mock 时间）观察日志

## 2. S2 — Supervisor LLM Agent

### 2.1 Snapshot Builder

- [x] 2.1 创建 `backend/agent/snapshot.py` —— `SessionSnapshot` 数据类 + `build_snapshot(sid) → dict` 函数
- [x] 2.2 提取 user_goal：从 SessionDB 拉这个 session 的第一条 user 消息，截断到 200 字符
- [x] 2.3 提取 todos_state：从 `code_todos` 表查 in_progress + pending，附加 stale_seconds
- [ ] 2.4 估算 context_token_pressure：用 tiktoken 估算当前对话总 token / 模型上下文窗口
- [x] 2.5 序列化 snapshot 为可读 JSON 字符串（便于 log + LLM 输入）

### 2.2 Supervisor LLM 调用

- [x] 2.6 创建 `backend/agent/supervisor.py` —— supervisor system prompt（中文）+ JSON output schema 描述
- [x] 2.7 实现 `call_supervisor_llm(snapshot) → SupervisorAction`：构造 messages + 走 LLM provider + 30s `asyncio.wait_for`
- [x] 2.8 SupervisorAction dataclass（action / severity / diagnosis / hint_for_main_agent / user_message / suggested_buttons）
- [x] 2.9 LLM 输出解析：strict JSON parse + schema validate；失败回退 `wait/green`
- [x] 2.10 cancel 强制降级为 ask_user（带预设中文 user_message）
- [x] 2.11 supervisor LLM provider 选择：默认走主 LLM 同 endpoint，可通过 `[supervisor].llm_provider` 切换
- [x] 2.12 异常隔离：任何 exception → 默认 wait/green + 日志 supervisor_llm_failed

### 2.3 SessionDB v14 迁移

- [x] 2.13 写迁移文件 `backend/deskpet/memory/migrations/003_p5s1_supervisor_hints.sql`
- [x] 2.14 表结构：`supervisor_hints (id INTEGER PK AUTOINCREMENT, session_id TEXT, hint_text TEXT, action TEXT, severity TEXT, ts INTEGER, alert_id TEXT)`
- [x] 2.15 索引：`(session_id, ts)`
- [x] 2.16 把 schema_version 从 13 升到 14（同一迁移脚本里）
- [x] 2.17 在 SessionDB 类里加 `append_supervisor_hint(...)` 和 `query_supervisor_hints_for_sid(...)` 方法

### 2.4 Watchdog 接入 Supervisor

- [x] 2.18 watchdog 触发时调 `build_snapshot(sid)` → `call_supervisor_llm(snapshot)`
- [x] 2.19 拿到 SupervisorAction 后：写入 supervisor_hints + 决定是否 broadcast supervisor_alert
- [x] 2.20 alert_id = uuid4，每次 broadcast 生成新的；持久化时也写入

### 2.5 supervisor_alert 广播

- [x] 2.21 实现 `_broadcast_supervisor_alert(payload)` —— 复用 `_todo_broadcaster` 的 fan-out 模式
- [x] 2.22 失败 ws send 个别捕获 + 日志 + 跳过，不影响其它 ws
- [x] 2.23 wait action 不广播；nudge / ask_user 广播

### 2.6 测试

- [x] 2.24 单测：`test_snapshot.py`（验证 user_goal 截断 / token 估算 / todos_state 提取）
- [x] 2.25 单测：`test_supervisor.py`（mock LLM 返回各种 action / 超时 / 解析失败）
- [x] 2.26 单测：`test_supervisor_hints_db.py`（写入 + 查询 + 迁移）
- [x] 2.27 端到端 manual：dev backend 启动 → 让 LLM 卡死循环（mock provider 反复返回 same tool_call） → 观察 SessionDB supervisor_hints 表 + ws broadcast

## 3. S3 — 桌宠 UI 反馈层

### 3.1 sessionsStore severity 字段

- [x] 3.1 在 `tauri-app/src/stores/sessionsStore.ts` 给 SessionState 加字段：`status / last_activity_age / current_iteration / max_iterations / tool_signature_window / last_supervisor_severity`
- [x] 3.2 加 selector `severity_score(sid) → number` 实现 5 项加权公式
- [x] 3.3 加 selector `pet_focus_sid() → string | null` 返回 max severity_score 的 sid

### 3.2 PetStateMachine

- [x] 3.4 创建 `tauri-app/src/pet-state/PetStateMachine.ts` —— 类，订阅 sessionsStore
- [x] 3.5 实现 5 状态枚举 + `compute_state(score) → State` 含滞后阈值（enter@60/exit@50, enter@100/exit@90）
- [x] 3.6 维护 `_last_transition_ts` —— 强制最短驻留 10s
- [x] 3.7 `intervening` overlay：3 秒后自动回到 score-derived state
- [x] 3.8 emit state change 事件给 Live2D 驱动器

### 3.3 Live2D 扩展

- [x] 3.9 `Live2DCanvas.tsx` 扩展 `Live2DHandle`：新增 `setIdleSubset(ids: string[])` / `setBlinkRate(hz: number)` / `setHeadTilt(degrees: number)`
- [x] 3.10 实现 motion pool 切换：自定义 idle motion 选择器（替换默认随机），按 switch_period 调度
- [x] 3.11 实现 ParamEyeLOpen / ParamEyeROpen 手动驱动（眨眼频率叠加在原 motion 上，最简方案：周期性把 param 拉到 0 再恢复）
- [x] 3.12 实现 ParamAngleZ 偏移（头部角度）
- [x] 3.13 5 状态对应的 motion_pool / blink / head_tilt / tap_on_entry 配置表（const Map）

### 3.4 Hiyori motion 校准 spike

- [ ] 3.14 SettingsPanel 加一个 debug 子面板（仅 deskpet_debug=1 时显示）：列出 m01..m10 + TapBody + 一键播放按钮
- [ ] 3.15 人工试听 m01..m10 节奏感，标注"快/中/慢"分组
- [ ] 3.16 用标注结果回填 motion_pool 配置表（替换初版的占位分组）

### 3.5 PetSupervisorBubble 组件

- [x] 3.17 创建 `tauri-app/src/components/PetSupervisorBubble.tsx` —— 接收 props {severity / message / buttons / sid / alert_id / onChoice / onClickBackground}
- [x] 3.18 颜色映射：worried=黄 / alert=红 / intervening=蓝
- [x] 3.19 fade in 300ms / fade out 400ms（CSS transition）
- [x] 3.20 alert 状态时边框 1Hz pulse（CSS keyframe）
- [x] 3.21 显示截断的 sid（前 16 字符 + "..."）
- [x] 3.22 按钮区 + 背景 click target 分离（按钮不冒泡到背景）
- [x] 3.23 按钮点击 → 发 `supervisor_user_choice` ws + 即时隐藏

### 3.6 App.tsx 整合

- [x] 3.24 在 App.tsx 实例化 PetStateMachine
- [x] 3.25 接收 `supervisor_alert` ws 事件 → 更新 sessionsStore + 触发 intervening overlay
- [x] 3.26 PetStateMachine state change → 调用 liveRef.current.setIdleSubset/setBlinkRate/setHeadTilt + setExpression（容错）+ playMotion
- [x] 3.27 状态 ∈ {worried, alert, intervening} 时渲染 PetSupervisorBubble，传 max-severity sid 数据
- [x] 3.28 bubble 背景 click → invoke `open_code_panel` + 发 `pet_focus_session_clicked` window event

### 3.7 SessionGridView tile severity

- [x] 3.29 修改 `tauri-app/src/components/SessionGridView.tsx` 给每个 tile 加 severity_score 计算（用 selector）
- [x] 3.30 border color 映射：<30 绿 / 30-60 蓝 / 60-100 黄 / ≥100 红
- [x] 3.31 score ≥ 100 的 tile 加 1Hz 30% opacity 边框 pulse
- [x] 3.32 tile 头部加 severity icon (小三角 / 警告标志)

### 3.8 Settings UI

- [x] 3.33 `SettingsPanel.tsx` 加 "桌宠 supervisor" toggle（控制 `[supervisor].enabled`）
- [x] 3.34 toggle 切换 → 发 `supervisor_toggle` ws + 等 `supervisor_toggle_ack`
- [x] 3.35 supervisor LLM provider 选择 dropdown（默认 / gpt-5-mini / 主 LLM）
- [x] 3.36 当前累计 supervisor 调用次数 + 估算成本（从 supervisor_hints 表 count）

### 3.9 Debug overlay

- [ ] 3.37 创建 `tauri-app/src/components/PetDebugOverlay.tsx`（仅 localStorage.deskpet_debug=1 显示）
- [ ] 3.38 显示当前 state / focus sid / focus score + 各 session score 明细 breakdown
- [ ] 3.39 显示 PetStateMachine 内部计时器（dwell remaining / intervening countdown）

### 3.10 测试

- [ ] 3.40 Vitest 单测：severity_score 公式（覆盖各档位）
- [ ] 3.41 Vitest 单测：PetStateMachine 状态转换（滞后阈值 / 最短驻留 / intervening overlay 时序）
- [ ] 3.42 Manual UI 测试：mock 后端发 supervisor_alert，观察桌宠 motion + bubble 切换
- [ ] 3.43 Manual UI 测试：多 session 并发，故意让其中一个分高，验证桌宠跟最高分

## 4. S4 — Nudge 注入回路

### 4.1 NudgeQueue

- [x] 4.1 创建 `backend/agent/nudge_queue.py` —— `NudgeQueue` 类 + `Hint` dataclass（text / alert_id / ts）
- [x] 4.2 内部 `_pending: dict[str, list[Hint]]` + `asyncio.Lock`
- [x] 4.3 `push(sid, hint)` —— cap=3 满时丢最旧
- [x] 4.4 `pop_all(sid) → list[Hint]` —— 取走并清空该 sid 队列
- [x] 4.5 `peek(sid) → bool` —— 不消费
- [x] 4.6 `clear(sid)` —— 显式清空
- [x] 4.7 注册到 `service_context["nudge_queue"]`

### 4.2 注入点（main.py）

- [x] 4.8 在 `_msgs` 构造完毕处（约 main.py:2089 附近 / Plan injection 之后）调 `nudge_queue.pop_all(sid)`
- [x] 4.9 多个 hint 拼成一条 system message：`[Supervisor]\n- hint1\n- hint2\n...`
- [x] 4.10 用现有的 system stack head insertion 逻辑插入（同 P4-S25 plan injection 模式）
- [x] 4.11 持久化：每次注入调 `_session_db.append_supervisor_hint_dispatched(sid, ...)`（在 supervisor_hints 表里 mark dispatched_at）
- [x] 4.12 识别 `<<supervisor_followup>>` trigger text：用户消息 echo 跳过（不进 chat history 显示成"用户说"）

### 4.3 Done callback follow-up

- [x] 4.13 在 `_chat_inflight[_msg_sid] = _chat_task` 之后立即 `_chat_task.add_done_callback(...)`
- [x] 4.14 实现 `_maybe_supervisor_followup(sid, ws)`：
- [x] 4.15   - 检查 `_chat_inflight.get(sid) is _chat_task`（still same task） → 不是则 return
- [x] 4.16   - `await nudge_queue.peek(sid)` → False 则 return
- [x] 4.17   - 起新 `_run_chat(ws, "<<supervisor_followup>>", sid)` task + 更新 `_chat_inflight`
- [x] 4.18 callback 内任何异常 catch + 日志 supervisor_followup_failed

### 4.4 Supervisor 决策接入 NudgeQueue

- [x] 4.19 supervisor.py 的 dispatcher：action=nudge 时 `nudge_queue.push(sid, Hint(...))` + 同步调 _broadcast_supervisor_alert
- [x] 4.20 action=ask_user 时 broadcast 但不入队
- [x] 4.21 action=wait 时不入队不广播

### 4.5 二次卡住升级

- [x] 4.22 supervisor.py 的判定逻辑：scan 时检查 SessionDB `supervisor_hints` 是否有近 30 分钟内同 sid 的 nudge 记录
- [x] 4.23 有的话强制把这次的 action 升级为 ask_user（带 user_message "之前提示过但还在打转，要看一下吗？"）
- [x] 4.24 升级路径写一行日志 + 持久化

### 4.6 Code mode exit 清理

- [x] 4.25 `code_mode_exit` IPC handler 调 `nudge_queue.clear(sid)` + `session_activity.drop(sid)`（已在 S1 加，验证一致性）
- [x] 4.26 验证：清理顺序 nudge_queue 先于 code_mode_state broadcast

### 4.7 用户重打路径验证

- [x] 4.27 验证 main.py:2489 的 `_prev_task.cancel()` 路径仍然工作；新 task 起来时正常 pop hint
- [x] 4.28 done_callback 在 cancelled task 上仍然触发（asyncio 行为），且自检 `_chat_inflight[sid] != _chat_task` 时正确 skip

### 4.8 测试

- [x] 4.29 单测：`test_nudge_queue.py`（push/pop/peek/clear/cap=3 最旧丢失/asyncio.Lock 并发安全）
- [x] 4.30 单测：`test_supervisor_followup.py`（mock 死循环 case → supervisor 触发 nudge → followup 重启 task → 注入到 _msgs）
- [x] 4.31 集成测：制造死循环（让 mock LLM 反复同 tool_call）→ 真触发 supervisor → 真注入 → 验证 LLM 下一轮拿到 system [Supervisor] 提示
- [x] 4.32 用户竞态测：在 supervisor 决策完成 + done_callback 即将 fire 的窗口内人为 cancel + retry，验证不重复 follow-up 不丢 hint
- [x] 4.33 二次升级 e2e：连续两次卡住 → 第二次自动升级到 ask_user 桌宠气泡

## 5. 集成验收 + 文档

- [x] 5.1 跨 slice 集成 e2e：开 dev backend + dev frontend，制造一个 bash_run 死循环 case，全程观察：watchdog 检测 → supervisor LLM 决策 → ws broadcast → 桌宠状态切换 → bubble 显示 → 用户点继续 → nudge 注入 → 主 agent 改变思路
- [x] 5.2 性能基线：60s scan loop 在 idle 状态下 CPU 占用 < 0.1%；2 session 同时被 scan 的 LLM 调用串行延迟 < 60s
- [x] 5.3 成本监控：跑 1 小时混合负载，统计 supervisor LLM 调用次数 + 估算 token 成本，写入 release notes
- [ ] 5.4 README 增加 supervisor 段落（启用 / 关闭 / 工作原理 / 成本说明）
- [x] 5.5 `HARDWARE_COMPROMISES.md` 不变（supervisor 不影响硬件兼容性）
- [ ] 5.6 项目 memory 加一条：supervisor 的设计决策点 + 已知能力上限（Hiyori 表达力）
- [x] 5.7 Settings UI 加一行说明文字解释 "桌宠 supervisor" toggle 的影响
- [x] 5.8 PreToolUse 风险点核查：所有变更都不涉及破坏性操作；新表 / 新文件无副作用
- [ ] 5.9 archive change：`/opsx:archive p5-s1-pet-supervisor-agent` 等 4 个 slice 全部上线后调用
