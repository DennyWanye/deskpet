# P6 — Tasks (TDD-ordered, 6 phases + archive)

每个 phase 严格 **red → green → refactor**：先写失败的测试，再写最少代码让它绿。Phase 之间必须实测验证（pytest 全绿，0 regression）才算闭环。

依赖图：

```
Phase 0  ─→  Phase 1  ─→  Phase 3 ─┐
                                    ├─→  Phase 5 ─→  Phase 6
Phase 0  ─→  Phase 2  ─→  Phase 4 ─┘
```

预估 wallclock: ~10 工时天（2 周 sprint）。

---

## Phase 0 — 准备 + 共享 fixtures（独立）

### 0.1 OpenSpec 提案 commit

- [ ] 0.1 把 `openspec/changes/p6-agent-loop-refactor/{proposal,design,tasks}.md` commit 到 master（不 archive，作为 active change）

### 0.2 Feature flag 基础设施

- [ ] 0.2 在 `backend/config.py` 加 `P6_ENABLE_GATE` 环境变量读取（默认 False）
- [ ] 0.3 写测试 `tests/test_p6_feature_flag.py::test_flag_default_off` —— 不设环境变量时返回 False
- [ ] 0.4 写测试 `test_flag_on_when_env_set` —— `P6_ENABLE_GATE=1` 时返回 True

### 0.3 共享 test fixture

- [ ] 0.5 创建 `tests/fixtures/p6.py` —— mock LLM provider、mock tool registry、构造长 message list 的 helper
- [ ] 0.6 baseline pytest 全绿（≥ 1149）确认起点

### 0.4 验收

- [ ] 0.7 pytest 全绿 + commit `chore(p6): Phase 0 — flag + fixtures`

---

## Phase 1 — TerminationGate (独立，纯逻辑)

### 1.1 数据结构 + 状态机骨架

- [ ] 1.1 写测试 `tests/test_p6_termination_gate.py::test_default_config_values` —— GateConfig 默认值（max_turns=50, tool_budget=40, wall_clock=600s, per_tool_max=5）
- [ ] 1.2 测试 `test_gate_starts_in_running_state` —— 初始化后 allows_call 返回 (True, None)
- [ ] 1.3 测试 `test_terminate_is_idempotent` —— 调两次 terminate 第二次无效（保持第一次的 reason）
- [ ] 1.4 测试 `test_summary_includes_all_fields` —— summary() 返回 dict 含 reason/turns_used/tools_used/elapsed_seconds/cost_usd
- [ ] 1.5 实现 `agent/termination.py` 的 `TerminationReason` enum + `GateConfig` + `GateState` + `TerminationGate` 骨架

### 1.2 Hard cap 决策逻辑（核心）

- [ ] 1.6 测试 `test_blocks_when_max_turns_reached` —— record_turn × N=max_turns 后 allows_call 返回 (False, HARD_MAX_TURNS)
- [ ] 1.7 测试 `test_blocks_when_tool_budget_exhausted` —— record_tool_call × N=tool_budget 后 allows_tool 返回 (False, HARD_TOOL_BUDGET)
- [ ] 1.8 测试 `test_blocks_when_wall_clock_exceeded` —— mock `time.time` 推到 600s+ 后 allows_call 返回 (False, HARD_WALL_CLOCK)
- [ ] 1.9 测试 `test_blocks_when_max_budget_usd_exceeded` —— record_turn(cost_delta=...) 累到 > max_budget_usd → allows_call (False, HARD_MAX_BUDGET_USD)
- [ ] 1.10 测试 `test_after_terminate_all_allows_return_terminated_reason` —— terminate(PERMANENT_TOOL_ERROR) 后任何 allows_* 都返回 (False, PERMANENT_TOOL_ERROR)
- [ ] 1.11 实现完整 `allows_call` + `allows_tool` 逻辑

### 1.3 Per-tool consecutive counter (LangGraph 教训)

- [ ] 1.12 测试 `test_per_tool_consecutive_increments` —— record_tool_call("read_file") × 3 后 per_tool_consecutive["read_file"] == 3
- [ ] 1.13 测试 `test_different_tool_resets_consecutive` —— record_tool_call("read_file") × 3 + record_tool_call("grep") → per_tool_consecutive["read_file"] == 0
- [ ] 1.14 测试 `test_per_tool_blocks_at_threshold` —— record_tool_call("write_file") × 5 后 allows_tool("write_file") 返回 (False, HALLUCINATION_DETECTED)
- [ ] 1.15 实现 per-tool counter 逻辑

### 1.4 状态推进 + 错误注入

- [ ] 1.16 测试 `test_record_final_answer_terminates_with_success` —— record_final_answer() 后 terminated=True, reason=SUCCESS
- [ ] 1.17 测试 `test_record_error_propagates_reason` —— record_error(ALL_PROVIDERS_FAILED) 后 terminated=True, reason=ALL_PROVIDERS_FAILED
- [ ] 1.18 测试 `test_cost_delta_accumulates` —— 多次 record_turn(cost_delta=0.05) 累计

### 1.5 验收

- [ ] 1.19 pytest `tests/test_p6_termination_gate.py` ≥ 18 tests 全绿
- [ ] 1.20 baseline pytest 全绿
- [ ] 1.21 commit `feat(p6): Phase 1 — TerminationGate + state machine`

---

## Phase 2 — ContextManager facade (独立，但用 Phase 0 fixtures)

### 2.1 Config + facade 骨架

- [ ] 2.1 写测试 `tests/test_p6_context_manager.py::test_default_config_values` —— 默认值（threshold/head/tail/compact/budget）
- [ ] 2.2 测试 `test_facade_holds_ref_to_global_store` —— ContextManager().ref_store 是 get_global_ref_store() 返回的同一对象
- [ ] 2.3 实现 `agent/context_manager.py` 的 `ContextConfig` + `ContextManager` 骨架

### 2.2 Budget check (B3 包装)

- [ ] 2.4 测试 `test_check_budget_delegates_to_b3` —— 调 check_budget 返回 BudgetCheckResult，与直接调 token_budget.check_budget 等价
- [ ] 2.5 测试 `test_check_budget_uses_config_thresholds` —— 自定义 warn_pct=0.5 时 ratio=0.6 → WARN

### 2.3 Compaction (B2 包装)

- [ ] 2.6 测试 `test_maybe_compact_skips_below_threshold` —— 短 history 调 maybe_compact 返回 unchanged + 不调 summarize_fn
- [ ] 2.7 测试 `test_maybe_compact_calls_summarize_above_threshold` —— ≥ 20 messages 时调 summarize_fn 并返回压缩后 list
- [ ] 2.8 测试 `test_maybe_compact_failure_returns_original` —— summarize_fn raise → 返回原 list 不丢历史

### 2.4 Tool result handling (B1 + G1 unified — 核心 G1 fix)

- [ ] 2.9 测试 `test_record_tool_result_truncates_long` —— 6000 char result 返回 (truncated, ref_id)
- [ ] 2.10 测试 `test_record_tool_result_keeps_short` —— 1000 char result 返回 (original, None)
- [ ] 2.11 **测试 `test_record_tool_result_skips_fetch_tool_result`** —— **G1 fix 核心断言**：tool_name="fetch_tool_result", result=6000 chars → 返回 (original, None)（不截不存 ref，避免无限循环）
- [ ] 2.12 测试 `test_record_tool_result_custom_skip_list` —— 配置 skip_truncation_for_tools={"my_tool"} → 该 tool 不截
- [ ] 2.13 测试 `test_record_tool_result_uses_global_ref_store` —— record 后 get_global_ref_store().get(ref) == 原文

### 2.5 高层 prep 函数

- [ ] 2.14 测试 `test_prepare_chat_messages_with_compaction` —— 长 history + provider → compaction 调用 + 返回新 list
- [ ] 2.15 测试 `test_prepare_chat_messages_no_summarize_fn_skips_compact` —— summarize_fn=None 时 maybe_compact 是 no-op

### 2.6 验收

- [ ] 2.16 pytest `tests/test_p6_context_manager.py` ≥ 15 tests 全绿
- [ ] 2.17 baseline pytest 全绿
- [ ] 2.18 commit `feat(p6): Phase 2 — ContextManager facade`

---

## Phase 3 — AgentLoop 集成 TerminationGate（依赖 Phase 1）

### 3.1 AgentLoop 接口扩展（feature flag 后门）

- [ ] 3.1 测试 `tests/test_p6_agent_loop_gate.py::test_agentloop_accepts_termination_gate_kwarg` —— 构造 AgentLoop(termination_gate=gate) 不抛异常
- [ ] 3.2 测试 `test_agentloop_creates_default_gate_when_flag_off` —— 构造 AgentLoop(max_iterations=50) 不传 gate, P6_ENABLE_GATE=False → 走旧路径（gate is None）
- [ ] 3.3 测试 `test_agentloop_uses_gate_when_flag_on` —— P6_ENABLE_GATE=True 时，AgentLoop 内部 self.gate 是 TerminationGate 实例
- [ ] 3.4 在 `AgentLoop.__init__` 加 `termination_gate` 参数（可选）

### 3.2 LLM call 前的 gate.allows_call

- [ ] 3.5 测试 `test_run_yields_max_turns_error_when_gate_blocks` —— mock gate 让 allows_call 返回 (False, HARD_MAX_TURNS) → run() 第一次 iteration 就 yield ErrorEvent(reason="error_max_turns") + return
- [ ] 3.6 测试 `test_run_records_turn_after_llm_call` —— mock provider 返回 stop_reason='tool_use'，验证 gate.state.turns_used == 1
- [ ] 3.7 在 `AgentLoop.run` 的 `for iteration` 改成 `while True` + `gate.allows_call` 检查（feature flag ON 时）

### 3.3 Tool dispatch 前的 gate.allows_tool（核心 fix）

- [ ] 3.8 测试 `test_run_blocks_tool_when_budget_exhausted` —— 构造 gate 让 tool_budget_hard=3, 然后 mock LLM 返回 4 个连续 tool_call → 第 4 个 dispatch 前 yield ErrorEvent(reason="error_tool_budget") + return
- [ ] 3.9 **关键测试** `test_run_hard_breaks_on_tool_budget_unlike_old_soft_msg` —— 显式断言：tool_budget=3, LLM 想发第 4 个 → loop **不再继续** iteration，gate.terminated == True
- [ ] 3.10 测试 `test_run_records_tool_call_per_dispatch` —— 3 个 tool_call 后 gate.state.tools_used == 3
- [ ] 3.11 测试 `test_run_per_tool_consecutive_break` —— mock LLM 连续 5 次返回 write_file → 第 5 次 dispatch 前 gate 触发 HALLUCINATION_DETECTED break
- [ ] 3.12 在 `AgentLoop.run` tool dispatch loop 内每次 dispatch 前调 `gate.allows_tool(tc.name)` + record_tool_call

### 3.4 Final answer + error recording

- [ ] 3.13 测试 `test_run_terminates_on_stop_reason_end_turn` —— mock provider 返回 stop_reason='end_turn' → gate.terminated, reason=SUCCESS
- [ ] 3.14 测试 `test_run_records_error_on_all_providers_failed` —— chain mode all fail → gate.record_error(ALL_PROVIDERS_FAILED)
- [ ] 3.15 替换散落的 `tools_used_count` 变量 + `_TOOL_BUDGET_HARD_MSG` 注入，删除老 soft cap 代码（feature flag ON 时）

### 3.5 验收

- [ ] 3.16 pytest `tests/test_p6_agent_loop_gate.py` ≥ 12 tests 全绿
- [ ] 3.17 baseline 1149 pytest 全绿（feature flag OFF 时所有老测试不变）
- [ ] 3.18 commit `feat(p6): Phase 3 — AgentLoop integrates TerminationGate (flag-gated)`

---

## Phase 4 — AgentLoop + chat handler 集成 ContextManager（依赖 Phase 2）

### 4.1 AgentLoop 用 ContextManager 处理 tool_result

- [ ] 4.1 测试 `tests/test_p6_agent_loop_ctx.py::test_agentloop_uses_ctx_record_tool_result` —— mock ctx_mgr, 验证 run() tool dispatch 后调了 ctx.record_tool_result
- [ ] 4.2 **G1 关键回归测试** `test_run_does_not_truncate_fetch_tool_result_response` —— LLM 调 fetch_tool_result，handler 返回 6000 char body → 进 working_messages 是 **完整** 6000 chars 不截断
- [ ] 4.3 在 `AgentLoop.run` 把 inline `maybe_truncate_tool_result` 替换成 `self.ctx.record_tool_result(...)`（feature flag ON 时）

### 4.2 chat handler 用 ContextManager.prepare_chat_messages

- [ ] 4.4 测试 `tests/test_p6_chat_handler_ctx.py::test_chat_handler_calls_prepare_messages` —— mock ctx_mgr, 验证 chat handler 进 chain 前调了 prepare_chat_messages
- [ ] 4.5 测试 `test_chat_handler_long_history_triggers_compaction` —— 注入 30 messages history → ctx.maybe_compact 被调
- [ ] 4.6 测试 `test_chat_handler_short_history_skips_compaction` —— 5 messages → maybe_compact 不调（or 调但 no-op）
- [ ] 4.7 在 `main.py chat handler` 删除现有 90 行 inline B2 代码，改成单调 `await ctx_mgr.prepare_chat_messages(...)`（feature flag ON 时）

### 4.3 Budget check 在 AgentLoop 用 ContextManager

- [ ] 4.8 测试 `test_run_blocks_on_context_budget` —— mock ctx.check_budget 返回 BLOCK → run() yield ErrorEvent(reason="context_budget_block") + return, gate.record_error 被调
- [ ] 4.9 测试 `test_run_warn_does_not_block` —— mock returns WARN → run() 继续但 log warning
- [ ] 4.10 替换 inline B3 调用为 `self.ctx.check_budget(...)`

### 4.4 验收

- [ ] 4.11 pytest `tests/test_p6_agent_loop_ctx.py` + `tests/test_p6_chat_handler_ctx.py` ≥ 10 tests 全绿
- [ ] 4.12 baseline 1149 pytest 全绿
- [ ] 4.13 commit `feat(p6): Phase 4 — AgentLoop + chat handler integrate ContextManager`

---

## Phase 5 — 集成测试 + dev 灰度开启 flag

### 5.1 端到端集成测试

- [ ] 5.1 测试 `tests/test_p6_integration.py::test_long_task_force_breaks_in_under_3min` —— 构造一个 mock LLM 连续返回 tool_use 50 次的场景，flag ON，断言 run() 在 wall_clock 触发前 ≤ 200 iteration loop 内退出（mock time）
- [ ] 5.2 测试 `test_long_task_breaks_with_tool_budget_reason` —— 同上但断言 ErrorEvent.reason == "error_tool_budget"（不是 max_turns，证明 hard cap 真硬）
- [ ] 5.3 测试 `test_history_compaction_keeps_loop_under_budget` —— 仿真 25 messages history → run() 内 budget WARN → 触发 compaction → 后续 budget 回 OK
- [ ] 5.4 测试 `test_fetch_tool_result_round_trip_no_truncation` —— LLM 调 fetch_tool_result 拿到 6000 char body 写进 history → 下次 iteration history 里 fetch result 仍是完整 6000 chars（之前 G1 bug 这里会再截到 2KB）

### 5.2 Dev 环境开启 flag

- [ ] 5.5 修改 `.claude/settings.local.json` 默认 `P6_ENABLE_GATE=1`（仅本机 dev，不影响 prod 默认值）
- [ ] 5.6 重启 deskpet，跑 the relay 短对话 + 30 iter 长任务，对比新旧路径 log
- [ ] 5.7 写 evidence: `evidence/5.7-dev-shadow-test.md` 含截图 + log diff

### 5.3 验收

- [ ] 5.8 pytest `tests/test_p6_integration.py` ≥ 6 tests 全绿
- [ ] 5.9 evidence file 完成
- [ ] 5.10 commit `feat(p6): Phase 5 — integration tests + dev flag-on`

---

## Phase 6 — 移除 dead code + 默认开启 flag

### 6.1 旧代码标 deprecated

- [ ] 6.1 把 `agent_loop.py` 里 feature flag OFF 路径的散落代码（_TOOL_BUDGET_HARD_MSG 注入、inline B1 import、inline B3 check）加 `# DEPRECATED P6 — remove after 2026-05-30` 注释
- [ ] 6.2 把 `main.py` 里 90 行 inline B2 代码也加同样标记

### 6.2 默认开启 flag

- [ ] 6.3 改 `config.py` 让 `P6_ENABLE_GATE` 默认 True（环境变量未设时）
- [ ] 6.4 跑 baseline pytest 1149+ 应全绿（因为新路径已经通过 Phase 3-4 测试覆盖）

### 6.3 1 周观察期（手动 task）

- [ ] 6.5 在 master 上观察 7 天，看 supervisor_hints 表是否有新 `error_tool_budget` 或 `error_wall_clock_exceeded` 出现 — 这是预期行为，证明 gate 真在保护
- [ ] 6.6 若有 supervisor_unavailable 或新 ErrorEvent，写 patch 修复（应该 0 个；如有 1 个是临界值需调参）

### 6.4 删除 dead code

- [ ] 6.7 删除 `agent_loop.py` 中标 DEPRECATED 的旧路径代码
- [ ] 6.8 删除 `main.py` 中标 DEPRECATED 的 90 行 inline B2 代码
- [ ] 6.9 删除 `P6_ENABLE_GATE` flag 代码（永远 ON 后 flag 无意义）
- [ ] 6.10 跑 pytest + vitest + tsc 全绿

### 6.5 文档

- [ ] 6.11 写 `docs/P6-agent-loop-architecture.md` 含分层图 + 各模块职责 + 后续扩展点
- [ ] 6.12 写 `docs/P6-migration-decisions.md` 记录每个选择的业界 cite

### 6.6 验收

- [ ] 6.13 pytest ≥ 1149 + 60 new 全绿
- [ ] 6.14 vitest 109 passed 不变
- [ ] 6.15 tsc 0 errors
- [ ] 6.16 backend 重启 smoke import OK
- [ ] 6.17 commit `chore(p6): Phase 6 — remove deprecated code + docs`

---

## Phase 7 — Archive

- [ ] 7.1 `openspec archive p6-agent-loop-refactor`
- [ ] 7.2 把本 change 从 `openspec/changes/` 移到 `openspec/changes/archive/2026-05-XX-p6-agent-loop-refactor/`
- [ ] 7.3 update `docs/INDEX.md` 加入 P6 reference

---

## 依赖图

```
Phase 0 (flag + fixtures)  ──┐
                              │
                              ├──→ Phase 1 (TerminationGate) ──┐
                              │                                  │
                              └──→ Phase 2 (ContextManager) ────┤
                                                                 │
                                                                 ├──→ Phase 3 (AgentLoop + Gate)  ──┐
                                                                 │                                   │
                                                                 └──→ Phase 4 (AgentLoop + Ctx)   ──┤
                                                                                                     │
                                                                                                     ├──→ Phase 5 (integration + dev flag) ──┐
                                                                                                     │                                        │
                                                                                                     │                                        ├──→ Phase 6 (remove dead) ──→ Phase 7
                                                                                                     │                                        │
                                                                                                     └──→──→──→──→──→──→──→──→──→──→──→──→──→──┘
```

并行可能（`/opsx:oneshot` 风格）：
- **Batch 1**: Phase 0 + Phase 1 + Phase 2（独立）
- **Batch 2**: Phase 3 + Phase 4（依赖 Batch 1，可并行）
- **Batch 3**: Phase 5 + Phase 6 + Phase 7（串行）

---

## 不做的（明确）

- ❌ 不引入 LangGraph / Claude Agent SDK 等外部 framework — 保持 deskpet 本地实现
- ❌ 不重写 ProviderAdapter（openai_compatible.py） — 留给 P7
- ❌ 不动 Supervisor / WatchdogLoop — 它们已经够干净
- ❌ 不改前端 UI — 全部后端架构
- ❌ 不改 system prompt / D2 prompt — 保留作 soft 引导，与 hard gate 互补
- ❌ 不实现 Tier 3 "fork session with summary"（Claude Code 风格三层 recovery） — 留 stub 接口，未来需要时再做
- ❌ 不支持 per-session gate config override 通过 UI — 配置走 `[supervisor]` config block 即可
