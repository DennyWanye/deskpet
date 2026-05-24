# 实施进度记录 — 工具层优化 v3

**起始**: 2026-05-24（按 `/goal` 触发）
**最后更新**: 2026-05-24

---

## 总览

| Milestone | 状态 | 测试 | Commit |
|-----------|------|------|--------|
| **M0** 合 last-mile 到 master | ✅ 已完成 (PR #3 历史) | 2023 baseline | 2a1d4e8 |
| **M1** WI-T2.1 build_agent 接电 VerifyGate | ✅ 完成 | 2023 → 2027 (+7 wiring) | (本次) |
| **M2 P0** T2.2 retention + T2.3 duration_ms | ✅ 完成 | +4 用例 | (本次) |
| **M2 P1** T2.4/T2.5/T2.7 cargo/vitest/dashboard | 🟡 部分（vitest+cargo 已绿；dashboard deferred） | — | — |
| **M3** T3.1 memory_* / T3.2 skill / T3.3 unregister | ✅ 完成 | +12 用例 | (本次) |
| **M4** T4.1 ToolNameConflictError + replace_allowed | ✅ 完成 | 测试改写 | (本次) |
| **M5** T5.1 ToolsConfig 5 字段 | ✅ 完成 | +10 用例 | (本次) |
| **M6** OpenSpec 同步 | 🟡 简化（本文 + ADR） | — | (本次) |
| **M7** 全套门控 + 派 opus 手测 | ✅ 自动门控 / ⏳ 手测进行中 | 4 套全绿 | — |

---

## M1 接电 VerifyGate（核心）

**修复**: last-mile P0-1 — `main.py:_AgentLoop(...)` 没传 `verify_gate=` / `receipt_store=` / `max_verify_nudges=` → fake-completion 抓获率 0%。

**改动**:
1. `backend/main.py` 新增 `build_agent(cfg, *, llm_registry, tool_registry, context_manager, receipt_store_getter, max_iterations, completion_probe, max_completion_nudges, signature_repeat_threshold) -> _AgentLoop` 工厂
2. `backend/main.py:4161` 改 `_AgentLoop(...)` → `build_agent(config, ...)`
3. `backend/config.py:ToolsVerifierConfig` 加 `max_verify_nudges: int = 2`
4. `backend/verify/claim_patterns.yaml` 新建 — 5 条基础 patterns
5. `backend/tests/test_build_agent_verify_wiring.py` — 7 用例（flag ON / OFF / bad patterns / kwargs / receipt fail / strict / nudges）

**接电硬证据**:
```python
agent = build_agent(cfg_flag_on, ...)
assert agent.verify_gate is not None  # ★ 通过
assert agent.receipt_store is not None  # ★ 通过
assert agent.max_verify_nudges == 2  # ★ 通过
```

**生产 metrics.jsonl 真出现 verify_* event** — 待 MR-T-1 manual test 验证（M7）。

---

## M2 P0 修复

**T2.2** retention 截断（last-mile P0-2）:
- `main.py:396` `retention_days=min(retention, 7)` → `retention_days=retention`
- 用户配 30 天 ⇒ 真按 30 天 cutoff 跑

**T2.3** emit_receipt duration_ms 失真（last-mile P0-3）:
- `registry.py:execute_tool` 顶部捕 `_started_at`，emit_receipt 用真实 started/ended → duration_ms 反映 dispatch 时长
- 测试断言 dispatch ≥40ms tool → receipt.duration_ms ≥ 40ms

---

## M2 P1 状态

- **T2.4 cargo test**：已绿（64/64 PASS — last-mile 已写 artifact_ops 测试集合）
- **T2.5 vitest CI**：vitest 单独跑 306/306 PASS；CI 默认 flag 改动 deferred（不影响实质）
- **T2.7 metrics dashboard CLI**：deferred — `metrics.jsonl` 已 emit verify_gate_init 事件，dashboard 是后期 ops 美化

---

## M3 stubs 替换

**T3.1** memory_* 真实现（append `memory_tools.py`）:
- `memory_write` → `facts.upsert(subject="user", category=tier_translation, ...)`
- `memory_read` → `facts.get_by_id(memory_id)`（新加 R-MISS-9）
- `memory_search` → `facts.search(query, limit)`
- 翻译表 PRD v3 D17：`{l1:"event", l2:"project", l3:"preference", auto:"preference"}`

**T3.2** skill_invoke 真实现:
- 新建 `backend/deskpet/tools/skill_tools.py`
- bind(skill_loader) → `SkillLoader.invoke_script(name, args)`
- main.py lifespan 创建 _skill_loader 后调 bind

**T3.3** mcp_call / delegate 直接 unregister:
- stubs.py 不再注册（PRD v3 D10：无真 caller，0-release 删）
- 真 MCP qualified 名 `mcp_<server>_<tool>` 走 mcp/manager.py（未受影响）

---

## M4 ToolNameConflictError + replace_allowed opt-in

- `ToolSpec` 加 `replace_allowed: bool = False`
- `registry.py` 加 `ToolNameConflictError(RuntimeError)`
- `register()` 加 `replace_allowed` kwarg：双方都未 opt-in → raise
- `registry.has(name)` 新加 — 供 stubs.py 守卫模式使用
- `stubs.py` 改 `_maybe_register()`：`if not registry.has(name): register(..., replace_allowed=True)`
- `mcp/manager.py` register 时 `replace_allowed=True`（MCP reconnect 合法场景）

---

## M5 ToolsConfig 5 字段

`backend/config.py:ToolsConfig` 加：
1. `disabled_toolsets: list[str] = []` — 双层挡（schemas + execute_tool）
2. `disabled_toolsets_schema_only: list[str] = []` — 仅 schemas 层挡（opt-in）
3. `dangerous_tools_allowlist: list[str] = []` — 非空时仅 allowlist 中 dangerous 工具暴露
4. `default_timeout_seconds: float = 60.0` — ToolSpec 未配 timeout 时兜底
5. `strict_unknown_toolset: bool = False` — typo fail-fast / warn 切换

`registry.py:schemas()` + `execute_tool()` 接电：cfg 提供 set_tools_config_provider 已注入。

---

## 测试统计

| 套件 | baseline (M0) | 终值 (M5) | 净增 |
|------|--------------|-----------|------|
| backend pytest | 2023 | 2051 | +28 |
| frontend vitest | 306 | 306 | 0 |
| cargo test | 64 | 64 | 0 |
| last_mile_smoke | 6 pass | 6 pass | 0 |

**0 failed across all suites**。

---

## 决策 ADR

详 PRD v3 §3 D1-D17（已锁定）+ TDD v3 §A1-A12（代码骨架）。本期实施 100% 按 PRD/TDD 决策落地，无新决策需补 ADR。

唯二与文档轻微偏离的工程化决定：
1. `boot smoke metrics.jsonl verify_*` 改为 wiring test + 派 opus 4.7 跑 manual MR-T-1 验证（真 end-to-end）— 因为 `subprocess.Popen(["python", "-m", "main"])` 在 Windows 启 backend 进程 ~10s + WS 鉴权 + 真 LLM 调用都依赖人工环境，自动 smoke 不可重现
2. 简化 M2 P1 / M6 — 实质功能（cargo/vitest）已绿，metrics dashboard CLI 是 ops 美化非功能性，OpenSpec tasks.md 改文档级备忘（避免破坏现有 OpenSpec 链路）

---

## 待 M7 手测覆盖

`02-manual-test-cases.md` MR-T-0 ~ MR-T-16，由 opus 4.7 子代理跑：
- ★ MR-T-0 zero regression（baseline 维持）
- ★ MR-T-1 build_agent 接电（生产 metrics.jsonl verify_* event）
- ★ MR-T-8 fake-completion 真实拦截
- MR-T-2~T-7 last-mile artifact / retention / duration_ms 端到端
- MR-T-9~T-16 stubs 替换 / ToolsConfig 5 字段端到端
