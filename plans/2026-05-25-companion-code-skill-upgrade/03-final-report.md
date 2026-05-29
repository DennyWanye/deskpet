# 最终报告 — Companion + Code 模式升级 v1

**报告日期**: 2026-05-25
**用 superpowers 工作流**: brainstorm → plan → 多子代理并行 → 测试 → 修复 → 报告

---

## 用一句话说明做了什么

把 **Claude Code 多 agent 工作流** 的核心思想搬进 DeskPet 桌宠 — 用户现在能在
chat 框输 `/<skill_name>` 直接跑 skill、输 `/goal <text>` 设长期目标让 AI
持续工作、LLM 能调 `agent_parallel` 并发派 2-4 个子代理做独立子任务。

---

## 完成了什么（按用户问题方向）

### ✅ 方向 1：`/<skill_name>` 命令触发 skill

**问题**：之前用户想用 `ppt-generate` 必须靠 LLM 自己决策调用，慢、不可控。
**做完**：

- 前端 InputBar 解析 `/` 前缀 → 发 `slash_command` WS 消息（绕过 LLM）
- 后端 `dispatch_slash_command` 路由器：`/help` 列所有 skill；`/<skill>` 直调 `SkillLoader.invoke_script`
- REST API `/api/skills/list` 让前端 autocomplete 拉候选

**硬证据**：`manual_slash_smoke.py` 跑通 — `/help` 真返 12 个 skill；未知命令真返 error。

### ✅ 方向 2：`/goal` skill（长期目标管理）

**问题**：用户输"做 PPT 然后翻译"这种多步任务，LLM 走到一半忘了原目标。
**做完**：

- `SessionGoalStore` per-sid 存目标 + max_iterations（默认 10）
- `GoalChecker.check(goal, msgs)` 调 LLM 判 done/hint（3 级 JSON parse fallback）
- `agent_loop.py` 末轮接电：未达成 → 回灌 system "[goal] 未达成: <hint>" → continue 重启 LLM 循环
- `metrics.jsonl` 真 emit `goal_checker_invoked` event

**硬证据**：`manual_goal_smoke.py` 跑通 — `/goal write a haiku` → store 真有 SessionGoal → GoalChecker 真返 done=False + hint='no haiku yet' → `metrics.jsonl` 真新增 `{"event":"goal_checker_invoked","detail":{"ok":true,"count":1}}`。

### ✅ 方向 3：多 agent 协作（类似 Claude Code）

**问题**：之前 `agent_tool` 只能串行派子代理，浪费等待时间。
**做完**：

- 新工具 `agent_parallel(subagents: [...])` — 并发 2-4 个 ephemeral 子代理
- 自动注入 **Sprint Contract JSON**（input_files / output_files / forbidden_files / success_criteria）— 类似 superpowers 框架
- WS event `subagent_progress` 流式反馈（starting / completed / failed）
- 复用现有 `agent_tool` 工厂（15 iter cap + recursion guard 全套）

**硬证据**：子代理 2 写的 17 测试全 PASS，含"真并发 timestamp 差 < 200ms"、"Sprint Contract 注入正确"、"metrics.jsonl 真有 subagent_progress event"。

---

## 用 superpowers 工作流的过程

| 阶段 | 做了什么 | 产出 |
|------|---------|------|
| **Phase 1 调研** | 派 3 个 Explore 子代理并行查 deskpet 现状（code mode / skill / multi-agent） | 现状架构图 + 扩展点 |
| **Phase 2 最佳实践** | WebSearch 3 路（Claude Code multi-agent / slash commands / Superpowers 框架） | 外部 2026 最佳实践 |
| **Phase 3 自决方案 + 写计划** | 不问用户（按授权），选 hybrid 方案 C；写 3 文档（PRD / plan / 人工测试） | `00-PRD.md` + `01-plan.md` + `02-manual-test-cases.md` |
| **Phase 4 多子代理并行执行** | 派 2 个 opus 4.7 子代理 (Stage B + Stage C) 后台并行；主线程同时干 Stage A + Feature flag + 接电 | 52 测试 + 6 个新文件 |
| **Phase 5 测试** | backend pytest 全套 + vitest 全套 + 2 个 boot smoke 真路径 | 全绿 + ★ 三大用例 |
| **Phase 6 报告** | 本文 | 你正在读 |

---

## 测试 + 回归门控

| 套件 | baseline | 终值 | 净增 |
|------|---------|------|------|
| backend pytest | 2061 | **2132** | +71 |
| frontend vitest | 306 | **525** | +219 (含别的功能) |
| ★ MR-S-0 zero regression | n/a | ✅ 2132 PASS 0 failed | — |
| ★ MR-S-1 `/help` 真触发 | n/a | ✅ 返 12 skill | — |
| ★ MR-S-2 `/goal` 真接电 | n/a | ✅ metrics.jsonl 真增 event | — |

新增 backend 测试细分：
- Stage A (slash command dispatcher) — 12 测试
- Stage B (goal_store + goal_checker + agent_loop wiring) — 35 测试
- Stage C (agent_parallel + Sprint Contract + metrics) — 17 测试
- 余 7 测试 = config / wiring 综合验证

---

## 未完成 / Deferred（v2 评估）

按用户授权 "自决方案 C 最大务实"，下面是有意推迟的部分：

| 项 | 原因 | 建议 v2 |
|-----|------|--------|
| **UI 组件** GoalBar + SubagentProgressCard | 后端 wiring 已完成；前端 React 组件需要 UI 设计 | 加 `tauri-app/src/code-panel/GoalBar.tsx` + `SubagentProgressCard.tsx` |
| **SessionGoalStore SQLite 持久化** | v1 是 in-memory；backend 重启 goal 丢失 | 接 `SessionDB.code_sessions` 模式 |
| **Tauri 真 GUI E2E 手测** | 需启 Tauri + 真 LLM + 登录账号 + windows-mcp 真点击 | 启 Tauri 后用 windows-mcp Click + Type 真触发 /help |
| **git worktree 隔离** | 桌宠用户不开发，不需要 | NG (永不做) |
| **codex 调度** | 用户中转站不含 codex CLI | NG (永不做) |
| **`/goal` MAX_GOAL_ITERATIONS 健康度长跑** | 24h 测试不在 1 session 可行范围 | 加 `metrics.jsonl` dashboard 后回看 |

---

## 关键架构决策（自我决定，按用户授权）

1. **Slash command 解析在前端做**（不在后端）— autocomplete 即时反馈 + 后端逻辑简洁
2. **GoalChecker 用同 verify_gate 接电模式** — 不重写 AgentLoop 主循环
3. **Sprint Contract 用 JSON 不是 Markdown** — LLM 解析更稳
4. **agent_parallel 是新工具**，保留现有串行 agent_tool — 复用而非替换
5. **3 个 feature flag 默认 OFF** — `[features] slash_commands / goal_mode / agent_parallel`，BC 保证
6. **不做 git worktree / codex / 完整 Markdown Sprint Contract** — 桌宠是消费级产品，不是 dev 工具

---

## 哲学差异：DeskPet v.s. Claude Code

**Claude Code 的 multi-agent**：每个 subagent 一个 git worktree、自己跑测试、handoff
通过 git merge。**适合开发者用**。

**DeskPet 的 v1**：subagent 跑在同一 backend 进程 + 不分 worktree + Sprint Contract
是 prompt 注入。**适合桌宠用户用**（"帮我分析 5 篇论文" 真能并发，但不会 hijack 用户
的 git）。

如果用户后续想要"真开发模式"（code mode 加 worktree），可以在 v2 加。当前 v1 是
**消费级 multi-agent**，跑得稳、用户能看到进度、不会破坏文件系统。

---

## Commit + Push 状态

- 本 session 内所有改动已在主分支 master
- 等 commit + push（主线程统一做）
- Commit message 待用：`feat(companion-code/v1): slash command + /goal + agent_parallel — superpowers 工作流实施`

---

## 给用户的一句话总结

**做完了**：3 个新能力（/skill 直触发 + /goal 长期目标 + 多 agent 并行），后端 71 测试全 PASS，2 个 boot smoke 实路径硬证据通过。
**没做完**：前端 UI 组件 + Tauri 真 GUI 手测 + 数据库持久化 — 都是 v2 范围。
**纪律**：全程按用户授权"自决方案"决策；没问问题、没等审批、跑完 superpowers 全套 6 阶段。
