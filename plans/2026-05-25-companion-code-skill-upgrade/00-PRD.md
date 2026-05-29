# PRD — DeskPet Companion + Code 模式升级 v1

**创建日期**: 2026-05-25
**作者**: 主线程（自决方案 C，按用户授权）
**状态**: v1 — 待实施

> ## 一句话
>
> 给 DeskPet 加 3 个 superpowers 级能力：**`/<skill_name>` 命令**触发 skill、
> **`/goal <text>` 长期目标**持续工作、**多子代理并行**协作 — 既适用 companion
> 模式（轻量），又适用 code 模式（dev 风格）。

---

## 1. 背景

### 1.1 调研发现的现状

| 维度 | 现状 | 缺口 |
|------|------|------|
| **Skill 系统** | 14 个 builtin + SkillLoader.invoke_script 沙箱 | 用户只能靠 LLM 自动选 skill；**无 `/<name>` 直触发** |
| **/command 解析** | InputBar 直发 chat_v2 WS，**0 命令解析** | 无 |
| **/goal 长期目标** | 无；每次 chat 是无状态消息 | 无 |
| **多 Agent 协作** | `agent_tool` 已有（串行子代理） | 无并行；无 sprint contract；无 handoff |
| **Code mode** | max_iter 50 + todo_write + plan injection | 缺 multi-step orchestration / 进度可视化 |

### 1.2 用户痛点

1. **想触发某个 skill 必须等 LLM 自己选** — 慢、不可控、有时不调
2. **复杂任务（"做 PPT + 翻译 + 发邮件"）**：LLM 走到一半忘了原目标，没机制持续 nudge
3. **多步骤任务并行**（"分析 5 篇论文"）：当前只能串行调 agent_tool，浪费等待

### 1.3 对标

- **Claude Code 2026** 已有 `/skill` 触发 + multi-agent team + slash commands
- **Superpowers 框架**：brainstorm → plan → TDD → subagent → review 七步
- **我们做的是**：把这两套思想**搬进 DeskPet 这个桌宠产品**，让普通用户也能用

---

## 2. 目标 / 非目标

### 2.1 目标 (G)

- **G1** 用户在桌宠 chat 框输 `/<skill_name> [args]` 直接触发该 skill（不经 LLM 决策）
- **G2** 用户输 `/goal <自然语言目标>` 设置 session-level 长期目标，AgentLoop 每轮 end_turn 前问 LLM "目标达成了吗？" 没达成则 continue
- **G3** AgentLoop 暴露 `agent_parallel` 工具：单次调用并发派 ≥ 2 个子代理，各自独立 prompt + tools，结果聚合
- **G4** Code mode UI 显示当前 active goal + 并行子代理进度卡片
- **G5** 全过程**默认 OFF** + feature flag 守护，关掉与现状字节级一致

### 2.2 非目标 (NG)

- **NG1** 不做 git worktree 隔离（消费级桌宠用户不开发，不需要）
- **NG2** 不做 codex 调度（用户的 LLM 是中转站，没 codex CLI）
- **NG3** 不重写 SkillLoader（沿用现有沙箱）
- **NG4** 不做完整 Sprint Contract markdown 模板（简化为 JSON）
- **NG5** 不做 24h 长跑健康度测试

### 2.3 成功度量

| 指标 | 目标 |
|---|---|
| `/ppt-generate <topic>` 触发 | 1 秒内 skill 跑起来（绕过 LLM） |
| `/goal` 持续工作 | session 收到 `goal_active` event；AgentLoop 真 continue ≥ 1 次直到达成 |
| `agent_parallel` 并发 | 2 个子代理同时跑（timestamp 差 < 100ms 启动） |
| UI 进度卡片 | `subagent_progress` WS event 至少 1 帧渲染 |
| 全套 backend pytest 回归 | 2061 → ≥ 2061，0 failed |

---

## 3. 关键架构决策

### D1 — Slash command 解析位置：**前端做**

前端 InputBar 解析 `/<name>` 前缀，发 `slash_command` WS 消息（不是 chat_v2）。
后端 main.py 加新 handler，不走 AgentLoop。

理由：解析在前端可立即给用户反馈（autocomplete 候选）+ 后端逻辑简洁（按 type
路由）。如果在后端解析，前端要等回包才知道是 skill 触发还是 chat。

### D2 — /goal 实现：**Session 级 SystemMessage + GoalChecker**

后端加 `SessionGoalStore`（in-memory dict + SessionDB 持久化），AgentLoop 每次
末轮（stop_reason != tool_use）调 `goal_checker.check(session_goal, working_msgs)`
→ 返 `(done: bool, hint: str)`。done=False 时回灌 system "目标未达成: <hint>" +
continue 类似 completion_probe。

理由：复用现有 nudge 路径（completion_probe / verify_gate 同模式），不重写
AgentLoop 主循环。

### D3 — Multi-agent 并行：**agent_parallel 新工具**

不改现有 `agent_tool`（串行版本保留），新增 `agent_parallel(subagents: list)`
工具。LLM 调时 `asyncio.gather` 并发跑每个 subagent，结果按 index 聚合返回。

理由：现有 agent_tool 的 15-iter 限制 + recursion guard 直接复用。

### D4 — Sprint Contract：**JSON Schema 不是 Markdown**

子代理 prompt 自动 prepend：
```json
{
  "task_id": "subagent_0",
  "input_files": ["..."],
  "output_files": ["..."],
  "forbidden_files": ["..."],
  "success_criteria": "..."
}
```

理由：JSON 让 LLM 解析更稳，markdown 容易格式漂。

### D5 — UI 反馈：**Code mode 新增 GoalBar + SubagentProgressCard**

Companion mode 不改 UI（避免影响主桌宠交互），只 code mode 显示。

### D6 — Feature flag：**`[features] slash_commands / goal_mode / agent_parallel`**

全 3 个 flag 默认 OFF；ON 才出现在 schemas + 接电。

---

## 4. 工作项 (WI)

### Stage A — Slash Command（核心 + 最大用户价值）

- **WI-A1** `backend/main.py` 加 `slash_command` WS handler
- **WI-A2** `backend/deskpet/commands/` 新建 — slash command registry + parser
- **WI-A3** `tauri-app/src/components/InputBar.tsx` 加 `/` 解析 + autocomplete UI
- **WI-A4** 新增 `/api/skills/list` REST endpoint 供 autocomplete 拉候选
- **WI-A5** 测试：`/ppt-generate test` 触发 → 1s 内有 skill 执行 receipt

### Stage B — /goal Command

- **WI-B1** `backend/deskpet/agent/goal_store.py` — SessionGoalStore（in-mem + SessionDB）
- **WI-B2** `backend/deskpet/agent/goal_checker.py` — GoalChecker.check(goal, msgs) → (done, hint)
- **WI-B3** `agent_loop.py` 接电：末轮调 goal_checker，类似 completion_probe 路径
- **WI-B4** `/goal <text>` 命令处理 + `/goal clear` 命令
- **WI-B5** UI 显示 active goal（code mode GoalBar）

### Stage C — Multi-agent 并行

- **WI-C1** `backend/deskpet/tools/code_tools/agent_parallel_tool.py` 新工具
- **WI-C2** Sprint Contract JSON 注入子代理 prompt
- **WI-C3** WS event `subagent_progress` 流式反馈（启动 / 完成）
- **WI-C4** UI SubagentProgressCard 渲染（code mode）

### Stage D — Feature flag + 配置

- **WI-D1** `backend/config.py:FeaturesConfig` 加 3 字段默认 OFF
- **WI-D2** 启动期 invariant 校验 + log

### Stage E — 测试 + 文档

- **WI-E1** backend pytest 新增 ≥ 15 测试（A 5 + B 5 + C 5）
- **WI-E2** 人工测试 testcase doc（02-manual-test-cases.md）
- **WI-E3** README 开发日志章节

---

## 5. 里程碑

| 里程碑 | 内容 | 预估 |
|---|---|---|
| **M1** | Stage A (Slash Command 全套 + 测试) | 主线程亲做（前端 + 后端 + 测试） |
| **M2** | Stage B (/goal Command) | 子代理 1 (backend GoalStore + Checker) |
| **M3** | Stage C (Multi-agent) | 子代理 2 (agent_parallel 工具) |
| **M4** | Stage D + E (Flag + 测试 + 文档) | 主线程整合 |
| **M5** | windows-mcp 实机 ★ 3 用例真测 | 主线程 |
| **M6** | 修 bug + 报告 | 主线程 |

---

## 6. 风险

| # | 风险 | 缓解 |
|---|------|------|
| R1 | InputBar 前端解析与 backend 路由不一致 → "/foo" 跑成 chat 消息 | 前后端约定 `slash_command` type + WS 直发；测试覆盖 |
| R2 | /goal infinite loop（永不达成） | max_goal_iterations=10 硬限；超限强退 + log |
| R3 | agent_parallel 子代理同时改文件 race | Sprint Contract `forbidden_files` 隔离 + 主代理责任 |
| R4 | feature flag OFF 时性能开销 | 模块 import 时 flag 检查；OFF 时所有新代码 short-circuit |
| R5 | 实机 E2E 需启 Tauri + 真 LLM | M5 用 boot smoke + WS 直发 slash_command 模拟（绕开 LLM） |

---

## 7. 关联文档

- 调研结果：本 session Phase 1 三路子代理报告（in-message）
- 外部最佳实践：[Anthropic Multi-agent 文档](https://platform.claude.com/docs/en/managed-agents/multi-agent), [Superpowers 框架](https://github.com/obra/superpowers), [Slash commands 指南](https://dev.to/daviddacruz/claude-code-skills-agents-build-custom-slash-commands-for-real-work-3865)
- 本地 skill 蓝本：`~/.claude/skills/sp-multi-agent-orchestration/SKILL.md`, `~/.claude/skills/sp-goal-management/SKILL.md`
