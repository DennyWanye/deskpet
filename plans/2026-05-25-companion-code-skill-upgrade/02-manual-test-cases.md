# 人工测试 — Companion + Code 模式升级 v1

**关联**: `00-PRD.md` + `01-implementation-plan.md`
**测试环境**: Windows + DeskPet dev (backend port 8100 + Tauri)
**执行方式**: windows-mcp 实机 / WS 直发 slash_command 模拟（绕开 LLM 不确定性）

---

## ★ 一票否决用例（≤ 3）

按 sp-goal-management 规则 5：condition 必须 LLM 自己能客观判断 + 实机硬证据。

| 用例 | 硬证据 | 退出码 |
|------|--------|--------|
| ★ MR-S-0 zero regression | `pytest tests/ -q` 末行匹配 `\d+ passed.*0 failed` 且 ≥ 2061 | 0 = pass |
| ★ MR-S-1 `/help` 实触发 | WS 发 `slash_command {command: "help"}` → 后端返 `slash_command_result {payload.result.skills.length > 0}` | result 含 skill 列表 |
| ★ MR-S-2 `/goal` 实接电 | WS 发 `/goal write a haiku` → `session_goal_store.get(sid)` 真有 SessionGoal 实例 + AgentLoop end_turn 真调 goal_checker.check 至少 1 次 | log 含 `goal_checker_invoked` |

---

## 完整用例 MR-S-0 ~ MR-S-10

### ★ MR-S-0 zero regression

**前置**：所有 feature flag OFF（默认）
**步骤**：
1. `cd G:/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/ -q --maxfail=10`
**期望**：`\d+ passed, \d+ skipped, \d+ deselected, 0 failed`
**通过条件**：passed ≥ 2061 + 0 failed

### ★ MR-S-1 `/help` 实触发

**前置**：`[features] slash_commands = true` + backend 重启
**步骤**：
1. WS 客户端连 `ws://127.0.0.1:8100/v1/chat/control`（或 control_ws）
2. 发 `{type: "slash_command", payload: {command: "help", args: "", session_id: "test-sid"}}`
3. 等 `slash_command_result` 响应
**期望**：response.payload.result.skills 是 list 且 length > 0（含 ppt-generate / deep-research 等）
**实机替代**：跑 `backend/scripts/manual_slash_smoke.py`

### ★ MR-S-2 `/goal` 实接电

**前置**：`[features] goal_mode = true` + backend 重启
**步骤**：
1. WS 发 `slash_command {command: "goal", args: "write a haiku about cats"}`
2. WS 发 `chat_v2 {text: "hi"}` 启 AgentLoop
3. 等 AgentLoop end_turn
4. Grep backend log：`grep "goal_checker_invoked\|goal_active" backend.log`
**期望**：log 中有 ≥ 1 条 `goal_checker_invoked`；`session_goal_store.get("test-sid")` 真返 SessionGoal 实例
**实机替代**：跑 `backend/scripts/manual_goal_smoke.py`

### MR-S-3 `/goal clear` 清除

**步骤**：
1. 先 `/goal set X`
2. 再 `/goal clear`
3. `store.get(sid)` 应返 None
**期望**：clear 后无 goal

### MR-S-4 `/<skill>` 真触发 skill_loader

**前置**：slash_commands flag ON
**步骤**：
1. WS 发 `slash_command {command: "ppt-generate", args: "test topic"}`
2. 等 response
**期望**：response.payload.result.type == "skill_result" + output 含 .pptx 路径

### MR-S-5 未知命令报错

**步骤**：发 `/notexistcmd`
**期望**：response.payload.error 含 "unknown command"

### MR-S-6 InputBar 前端真发 slash_command

**前置**：Tauri 真启 + slash_commands flag ON
**步骤**：
1. windows-mcp 启 Tauri
2. 桌宠 chat 框输入 `/help` + Enter
3. windows-mcp Screenshot 抓 UI 反馈
**期望**：UI 显示 skill 列表
**注**：需真 Tauri + 真用户输入；时间允许才做

### MR-S-7 agent_parallel 并发 2 子代理

**前置**：`[features] agent_parallel = true`
**步骤**：
1. 直接 import + 调 `agent_parallel_handler({subagents: [{...}, {...}]})`
2. 看 timestamp 差
**期望**：两个子代理启动 timestamp 差 < 200ms（真并发）

### MR-S-8 agent_parallel 4 个 max 限制

**步骤**：传 5 个 subagents
**期望**：error "max 4 subagents"

### MR-S-9 subagent_progress WS event 真发

**步骤**：连 WS + 调 agent_parallel + 抓 event stream
**期望**：每个 subagent 至少 2 帧（starting + completed）

### MR-S-10 feature flag OFF 时字节级一致

**前置**：所有 3 个 flag OFF（默认）
**步骤**：
1. 发 `slash_command` WS → 期望 "feature disabled" 错
2. 发 `/<skill>` 在 chat_v2 → 当普通消息处理（前端不解析）
**期望**：与 v3 工具层 ship 后状态字节级一致

---

## 退出标准

- ★ MR-S-0/1/2 三大用例全 ✅
- 功能 bug 数 = 0
- backend pytest 0 failed
- 至少 1 个实机硬证据（manual_*_smoke.py 真跑通）
