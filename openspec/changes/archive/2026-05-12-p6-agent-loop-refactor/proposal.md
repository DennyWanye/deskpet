# P6 — AgentLoop Pipeline 重构（TerminationGate + ContextManager）

> **Status**: Proposed
> **Owners**: deskpet core
> **Estimated effort**: 2 weeks (1 sprint)
> **Risk**: medium — touches AgentLoop.run() hot path; mitigated by TDD + Strangler Fig
> **Replaces**: 14 inline patches (A1/A2/B1/B2/B3/C2/D1/D2/F1/G1/G2/G3) — consolidates them into two clean abstractions

## TL;DR

经过 multi-provider-management 这一轮的实战，我们累积了 14 个 inline patch 解决"agent 不收敛"和"context 爆炸"两类问题。补丁本身都对，但**散落到 600 行的 `AgentLoop.run()` 和 400 行的 `main.py chat handler`**，已经到了架构债务的临界点（参见 §"为什么现在"）。

提案：用两个明确分层抽象 **替换** 这些 inline 散点：

1. **`TerminationGate`** — 集中所有 loop 终止决策，状态化 + 硬约束。借鉴 Claude Code `maxTurns` 不可覆盖语义 + Hermes Dual-gate 设计。
2. **`ContextManager`** — 统一 B1（截断）+ B2（compaction）+ B3（token budget）+ G1（fetch tool）为单一 facade。借鉴 LangChain Anatomy 三件套 + Claude Code 三层 recovery + Hermes preflight compression。

**目标**: 消除已观测的 4 类 bug，同时让未来加新策略只改 1 个文件（不是 4 个）。

---

## 为什么现在（4 个已观测故障）

按调研结果对照我们的现状：

| 已观测 bug | 业界叫法 | 我们当前症状 | 根本归类 |
|---|---|---|---|
| Agent loop 不收敛 21 分钟，tools_used=65 仍跑 | LangGraph "infinite tool loop" / Hermes "needs IterationBudget hard gate" | `_TOOL_BUDGET_HARD_MSG` 只是 system message，模型 ignore | **TerminationStage 不存在** |
| `fetch_tool_result` 自己的 result 被 B1 再次截断 | LangChain Anatomy "tool offloading must be self-aware" | B1 不知道 fetch_tool_result 是 retrieve 工具 | **ContextManager 碎片化** |
| B1 marker 写"use fetch_tool_result"但工具不存在（G1 之前） | LangChain Anatomy "text-contract must match registration" | B1 和 G1 靠字符串约定连接，没共享数据结构 | **ContextManager 碎片化** |
| Soft system msg "stop now" 被 LLM 持续忽视 | Claude Code: "maxTurns is non-negotiable" 设计原则 | 我们的 hard cap 不硬 | **TerminationStage 不存在** |

**架构债务利息已开始扣**：G1 这个 bug 本身就是补丁产生的（B1 引入字符串 marker，G1 后补工具）。再加 5 个补丁，下次 debug 路径就要 grep 4-5 个文件。

---

## 业界调研（合成自 6 份资料）

### TerminationStage 设计学习

| 项目 | 关键设计 | 我们采纳 |
|---|---|---|
| **Claude Code SDK** | `max_turns` 是 hard cap，**non-negotiable**；终止后 `ResultMessage.subtype` ∈ {`success`, `error_max_turns`, `error_max_budget_usd`, `error_during_execution`, `error_max_structured_output_retries`} 明确状态 | ✅ 明确枚举终止原因，主代码不能跳过 hard cap |
| **Hermes Agent** | `IterationBudget` 类 — thread-safe，parent + subagent 共享；**Dual-gate** = iteration counter AND budget 都要允许才能 continue；7 个 named "continue sites" + `transition` 字段防 infinite loop | ✅ Dual-gate + 状态化 continue tracking |
| **LangGraph ReAct** | `remaining_steps` 计算法 = `recursion_limit - total_steps_taken`；`_are_more_steps_needed` 在 remaining < 2 时返回 `"Sorry, need more steps"` 优雅退出，不抛 GraphRecursionError；**Per-tool retry counter** 优于 global counter | ✅ 优雅退出消息 + per-tool 计数 |
| **OpenAI Codex** | Stateless turn 概念；quadratic context growth 用 prompt cache 缓解 | ❌ 我们已经走 stateless，无需改 |

### ContextManager 设计学习

| 项目 | 关键设计 | 我们采纳 |
|---|---|---|
| **Claude Code SDK** | **自动 compaction** 接近 context limit 时触发；emit `compact_boundary` 事件；`PreCompact` hook 允许 archive；CLAUDE.md **每个 request 重新注入**不被压；`/compact` 手动 | ✅ Preflight compaction + hook + 可观测事件 |
| **Claude Code 三层 recovery** | Tier 1 **Collapse drain** (本地归档，最便宜)；Tier 2 **Reactive compact** (LLM summarize)；Tier 3 **Token escalation** (提高 ceiling 重试) | ✅ 三层 fallback strategy |
| **Hermes Agent** | **Preflight compression** 50% threshold 触发（不是被动 overflow）；compression 在 API call **之前**运行 | ✅ Preflight 优于 reactive |
| **LangChain Anatomy** | Context Management 三件套：**Compaction** + **Tool call offloading** (大输出存 filesystem) + **Skills progressive disclosure** | ✅ 这三件套正是 B1+B2 缺的 facade |
| **Cline** | Auto-compact feature flag；`/new` 命令 fresh task；多级警告 (exceeded / slow / missing changes) | ✅ user-facing event + 配置开关 |

### 关键设计原则（提炼）

1. **Hard limits 不依赖 LLM 听话** — Claude Code maxTurns "non-negotiable"
2. **终止状态明确枚举** — 不要散落在代码里靠 `if/elif` 找
3. **Per-tool retry budget** > global budget — LangGraph 教训
4. **Preflight compaction** > reactive compaction — Hermes 设计
5. **Tool offloading** 用 ref_id + 自我感知 — fetch tool 自己的 result 不再截
6. **Text-contract 必须有 tool-registration 兜底** — G1 教训

---

## 提案范围

### 实现的（in scope）

1. 新建 `backend/agent/termination.py` — `TerminationGate` 类 + `TerminationReason` enum
2. 新建 `backend/agent/context_manager.py` — `ContextManager` facade (wraps B1+B2+B3+G1)
3. 重构 `backend/agent/agent_loop.py::AgentLoop.run()`：
   - 删除散落的 `tools_used_count`、`_TOOL_BUDGET_HARD_MSG` 注入、token budget inline check、tool_result_truncator import
   - 改为：`while gate.allows_continue(state): ...`
   - 调 `await ctx_mgr.prepare_messages(...)` 而不是 inline B1/B2/B3 hack
4. 重构 `backend/main.py chat handler`：
   - 删除 90 行 inline B2 compactor 调用
   - 改为：`await ctx_mgr.prepare_chat_messages(...)` 一次调
5. 新增 6 个 unit test 模块（每个 ≥ 10 tests，覆盖各类终止/压缩场景）
6. 集成 e2e 测试：仿真 33+ iteration 长任务，断言 gate 强制 break

### 不动的（out of scope）

- ToolRegistry / SessionDB / Supervisor / WatchdogLoop — 这些已经够干净
- multi-provider-management / the relay 集成 — 工作正常
- F1 (httpx keep-alive=0) / D1 (stream→non-stream) — 在 ProviderAdapter 层，本提案不动
- 前端 / UI — 全在后端

### Non-goals

- ❌ 不重写 ProviderAdapter（openai_compatible.py 1100 行）— 留给 P7
- ❌ 不引入 LangGraph / Claude Agent SDK 等外部 framework — 保持本地实现
- ❌ 不改 system prompt 设计（D2 prompt 保留作为 soft 引导）

---

## 量化目标

提案落地后，应该满足：

| 指标 | Baseline (当前) | Target (P6 后) |
|---|---|---|
| `tools_used` 超 hard cap 后还能继续跑的最大次数 | 25+ (实测 65) | **0** (硬强制 break) |
| Agent loop 平均不收敛时间 | 20+ 分钟 (实测) | **≤ 3 分钟** (wall-clock cap) |
| B1 截断 fetch_tool_result 自己结果的概率 | ~100% (每次 fetch >2KB) | **0%** (self-aware skip) |
| 找根因需 grep 的文件数 | 4-5 | **≤ 2** |
| 加新终止规则需改的文件数 | 3 (agent_loop + main + supervisor) | **1** (termination.py) |
| 加新 context 优化策略需改的文件数 | 3 (B1+B2+B3 各自) | **1** (context_manager.py) |
| 单元测试覆盖率（agent loop 终止逻辑） | ~30% (散落在 P5-S2 各测) | **≥ 80%** |

---

## 风险 + 缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| AgentLoop.run() 改动破坏 1149 pytest baseline | 中 | 高 | 严格 TDD：先写 termination/context_manager 单测全绿，再改 run()，再跑回归 |
| 真实长任务行为变化（用户感知）| 低 | 中 | Strangler Fig：新代码 behind feature flag (`P6_ENABLE_GATE`)，默认关闭，灰度开启 |
| 实现复杂度高于估计 | 中 | 中 | Phase 化 (6 phases)，每 phase 独立 mergeable，可中途暂停 |
| 重构期间业务需求来插队 | 高 | 低 | Strangler Fig + flag 让插队改动可走老路径 |

---

## 依赖

- 现有 P5-S2 14 个 commit 都在 master，**不需要先 archive 任何 change**
- 不依赖任何 the relay 服务端改动
- 不依赖前端改动

---

## 验收

P6 完成的定义：

1. 所有 7 个 phase 的 TDD task 全绿（详见 `tasks.md`）
2. pytest **≥ 1149 + 60 new** 全绿，0 regression
3. vitest 109 passed 不变
4. Live E2E：构造 30+ iteration 长任务，应该看到：
   - `termination_force_break reason=tool_budget_exhausted iter=N`
   - 时间 < 3 分钟
   - WS emit `chat_v2_terminated` 事件让前端能显式渲染
5. `docs/p6-architecture.md` 写明新架构图 + 决策记录

完成后 commit `chore(p6): archive change` 把这个 change 移到 `archive/`。

---

## 决策记录（小节）

为什么用 OpenSpec 走全流程而不是直接 patch？

因为补丁堆叠这种风险**只能用流程约束**。如果不写 spec/tasks，下一个补丁很容易 sneak in，2 周后又是 14 个 patch。OpenSpec 强制把"重构"作为显式工件，让任何 inline patch 必须先去问"是不是该走 P6 路径"。这是流程层面的 self-discipline。
