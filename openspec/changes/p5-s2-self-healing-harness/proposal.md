# P5-S2 — Self-Healing Code-Mode Agent Harness

## Why

P5-S1 落地了"被动监督"（watchdog 60s 扫一次，发现 stuck 推 hint 进 NudgeQueue），P5-S2 我已经先手做完了 Hook A（completion guard）和 Hook B（idle-with-todos）—— 但 **2026-05-10 实战暴露 3 个根本性架构缺陷**：

### 实战观察（2026-05-10 16:50–17:30 UTC+8）

两个 Code 模式 session 撞 `max_iterations=50`，UI 显示 "Agent 已达到迭代上限"。日志根因：deepseek-v4-pro 连发 50 次 `tool_calls=1` 的工具调用，**每次 arguments 都被 chinzy 流式编码截掉、parse 后是 `{}`**，工具拒收 → LLM 自我反思 → 再发空 args → 死循环 50 圈。AI 自己在 content 里说："I'm completely stuck in a loop. Every call I make has empty invoke blocks with no parameters."

### 这暴露的 3 层架构问题

1. **错误分类缺失** —— `run_shell` 报"missing required parameter: path"是**永远不会自己变出来**的（PermanentError），不该跟"网络超时"（TransientError）走同一条重试路径。Towards Data Science 2026 春季的实测：19/21 次 ReAct 失败的根因都是 `hallucinated_tool_exhausted_retries`，**重试一个永远不会成功的调用是确定性的浪费**。

2. **每工具熔断器缺失** —— 一个全局 `max_iterations=50` 计数器粒度太粗。业界现行做法是每个工具一个独立 circuit breaker（CLOSED → OPEN → HALF-OPEN 三态）：`write_file` 连失败 3 次 → 这个工具熔断，让 LLM 换思路，而不是它继续撞。

3. **Sensor 反馈不带修复指令** —— Martin Fowler 在《Harness Engineering》里直接说："Sensors are particularly powerful when they produce signals that are optimised for LLM consumption, e.g. custom linter messages that include instructions for the self-correction." 我们现在工具报错只说 `{"error": "missing path"}`，**没告诉 LLM 怎么改**。如果改成带 hint + example，LLM 大概率第二次就修对，根本不需要 supervisor 介入。

### 这次还要补的（来自 P5-S1 的延续）

4. **Supervisor → 主 agent 闭环不全自动** —— P5-S1 已经实现 supervisor 推 hint 到 NudgeQueue，但 hint 必须等用户**下次发消息**才被注入。max_iterations 触发后弹"继续/取消"按钮要用户手点 —— 这不算自愈，是要求用户当继电器。

5. **AgentLoop 死循环检测不到** —— `session_activity.py` 有 `tool_signature_window` 字段但 watchdog 没用上，agent_loop 也没读，相当于装了传感器没接线。

### 真实代价支撑

- 2025-07 一个 Claude Code 实例递归 5 小时烧 **16.7 亿 tokens / $16K-$50K**（Yamishift 公开案例）
- 无 guardrail 的 ReAct 任务成功率 20-40%；做完 loop engineering 70-80%+（业界共识）
- ICLR 2026 论文 *Agent Error Taxonomy*：分类感知错误 + 定向修复 hint 提升 +24% 准确率

桌宠用户每次撞这种循环要看 50 次 LLM 推理白烧 5 分钟，体感极差，且 chinzy 计费按 token 走真烧钱。

## What Changes

### 后端

- **新增** `backend/agent/errors.py` —— `TransientToolError` / `PermanentToolError` / `HallucinationError` 三类异常 + `classify(raw_error: dict | str) -> ToolErrorClass` 分类器
- **新增** `backend/agent/circuit_breaker.py` —— `ToolCircuitBreaker` 类（per-(sid, tool) 三态机），暴露 `record_call(name, ok, error_class)` + `can_call(name) -> bool` + `try_close()` HALF-OPEN 探针
- **修改** `backend/deskpet/tools/registry_v2.py` —— 工具 dispatch 入口检查 circuit breaker；OPEN 时直接返回 `{ok: false, error: "circuit_open", hint: "<工具名> 连续失败 N 次已熔断 60s, 请换思路或换工具", available_alternatives: [...]}`
- **修改** 所有 tool 实现 (`run_shell.py` / `write_file.py` / `edit_file.py` / `read_file.py` / `list_directory.py` / `web_fetch.py` / `glob.py` / `grep.py`) —— error response 加 `hint` + `examples` 字段（"sensor with remediation"模式）
- **修改** `backend/agent/agent_loop.py` ——
  - `tool_signature_repeat_breaker`: 同 (tool, args_hash) 在最近 5 步连发 ≥3 次 → 注入 system msg "你在重复调用同一工具同一参数 3 次了，结果不会变。看一下 tool_result 的 hint 字段或者换个思路。" + 跳过本轮工具执行
  - `permanent_error_breaker`: 收到 `error_class=permanent` → 不再 ReAct loop，立刻 emit FinalEvent + 通知 supervisor
- **新增** `backend/agent/auto_resume.py` —— `AutoResumeOrchestrator`：监听三个触发器 (max_iterations / circuit_open / permanent_error)，调 supervisor LLM 拿 hint，**自动 spawn 新 chat task**（带原 context + 注入 hint），最多 N 次自愈失败才转人工弹窗
- **修改** `backend/agent/watchdog.py` —— 新增 trigger 规则 (d): tool_signature_window 显示死循环 → trigger
- **修改** `backend/providers/openai_compatible.py` —— 加诊断日志 `tool_call_args_dump`：每个 tool_call 完成时 dump args 实际值 + 长度 + json.parse 是否成功

### 前端

- **修改** `tauri-app/src/code-panel/MessageBubble.tsx` —— `ToolResultCard` 检测到 `error.hint` 字段时高亮渲染（让用户也能看到修复建议）
- **修改** `tauri-app/src/stores/sessionsStore.ts` —— 新增 `auto_resume_attempts` 字段
- **新增** `tauri-app/src/code-panel/AutoResumeBanner.tsx` —— "agent 自愈中..."轻量 toast，不阻断
- **修改** `tauri-app/src/code-panel/ws.ts` —— 处理新事件 `auto_resume_started` / `auto_resume_succeeded` / `auto_resume_exhausted`

### IPC 协议

- 新增 ws 出向事件：`auto_resume_started` / `auto_resume_succeeded` / `auto_resume_exhausted`
- 新增 ws 出向事件：`tool_circuit_opened` (诊断用，前端轻量提示)

### 配置

`config.toml [supervisor]` 段新增：
- `auto_resume_enabled = true`
- `max_auto_resume_attempts = 2` —— 连续 2 次自愈失败才弹用户
- `circuit_breaker_threshold = 3` —— 同工具连失败几次 OPEN
- `circuit_breaker_cooldown_seconds = 60` —— OPEN 多久后允许 HALF-OPEN 探针
- `tool_signature_repeat_threshold = 3` —— 同 (tool, args) 连发几次拦截

## Capabilities

### New Capabilities

- **`agent-loop/error-taxonomy`** — `TransientToolError` / `PermanentToolError` / `HallucinationError` 三类区分 + 分类器。AgentLoop 收到 PermanentError 立刻 break；TransientError 走指数退避重试；Hallucination 触发 supervisor。

- **`tool-registry/circuit-breaker`** — Per-(session, tool) 三态熔断器（CLOSED/OPEN/HALF-OPEN），独立于全局 max_iterations，挡掉单工具反复失败的循环。

- **`pet-supervisor/auto-resume`** — Supervisor 推完 hint 后自动 spawn 新 chat task 重跑（不等用户输入），带 max_attempts cap，闭合"检测 → 修复 → 验证"自愈环。

### Modified Capabilities

- **`tool-registry/sensor-feedback`** —— 所有工具 error response 升级到"sensor + remediation"格式：`{ok: false, error: "...", hint: "...", examples: [...]}`。LLM 看到 hint 大概率自己修对。

- **`agent-loop`** —— 新增两个 break 早退出条件（permanent_error + tool_signature_repeat），且 ReAct 每轮的工具调用要先过 circuit breaker。

- **`pet-supervisor`** —— Watchdog 新增触发规则 (d) tool_signature_window 检测死循环。Auto-resume 触发后写 `supervisor_hints.action='auto_resumed'` 到审计表。

- **`frontend-ipc-surface`** —— 新增 4 个 ws 事件 (auto_resume_started / succeeded / exhausted / tool_circuit_opened) + 新增 settings 开关 `auto_resume_enabled`。

## Impact

### 代码影响
- 后端: ~600 行新增（errors.py ~80 + circuit_breaker.py ~150 + auto_resume.py ~250 + 8 个工具 hint 字段 ~80 + agent_loop break 条件 ~40）
- 前端: ~150 行新增（AutoResumeBanner ~80 + MessageBubble hint 渲染 ~50 + ws.ts dispatch ~20）
- 数据库: 不需要新表（复用 supervisor_hints 表 + 新增 action 枚举值 `auto_resumed`）

### 运行时影响
- **正常 case 零开销** —— circuit breaker check 是 O(1) dict lookup；error 分类只在 error 时跑
- **自愈 case 节省**：从平均"50 iteration × 5 秒 / iteration = 250 秒撞墙"变成"3 iteration → 检测到永久错误 → supervisor 1 调 → 新 task 1-3 iteration 修复"≈ **节省 90% token + 时间**
- **LLM 成本**：每次 auto_resume 多 1 次 supervisor 调用（约 800 input + 200 output tokens，gpt-5-mini ≈ $0.001）。比起浪费的 50 iteration（每次 4000+ sse_lines），净省

### 兼容性
- 关 `auto_resume_enabled = false` 时退化到 P5-S1 行为（弹用户按钮）
- 工具 error response 加字段不破坏现有调用方（旧字段保留，hint 是 additive）
- 不动 SessionDB schema，不需要新 migration
- 不需要重打包

### 风险
- **Auto-resume 死循环** → max_auto_resume_attempts=2 硬上限，单测覆盖
- **Circuit breaker 过激** → cooldown 60s 后 HALF-OPEN 单次探针 + 配置开关 + 单测覆盖
- **Hint 误导 LLM** → 工具自己 own hint 文本（开发者明确写过），不是 LLM 即兴生成；hint 字段 LLM 看不看自由
- **Supervisor 自己卡** → 沿用 P5-S1 的 30s 硬超时 + 失败回退 wait/green
- **Error 分类误判** → "未知错误"默认归到 TransientError 走重试（不中断现有路径），HallucinationError 必须有强证据（如 tool_name 不在 registry）

## Non-Goals

- **不做** Plan-Execute-Verify (PEV) 完整重构 —— 那是 P6 级别，这次只做"出错 → 自愈"闭环
- **不引入** 新 LLM provider 或新模型 —— supervisor 复用现有配置
- **不动** SessionDB schema —— 复用 P5-S1 留下的 supervisor_hints 表
- **不做** 多 worktree Lead-Expert subagent 模式 —— 太重，跟桌宠定位不符
- **不加** 任何新的"先弹窗确认"权限/沙箱护栏（违背 [feedback_no_sandbox_constraints](C:\Users\24378\.claude\projects\G--projects-deskpet\memory\feedback_no_sandbox_constraints.md)）
