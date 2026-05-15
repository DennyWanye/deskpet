# Evidence: P1 memory-recall session-affinity (D1)

**When**: 2026-05-16 (UTC+8)
**Who**: subagent (Phase 1 of 2026-05-16-companion-context-isolation)
**What we tested**: retriever RRF 融合阶段加 session-affinity 乘性降权 —
companion (`default`) session 的请求不被无关 code-session 项目记忆劫持，
同时保留跨 session 人物/偏好记忆；`decay=1.0` 完全退回旧行为。

## Scope implemented (tasks.md Phase 1)

- 1.1 `backend/tests/test_retriever_session_affinity.py`（TDD 先行，先 RED 后 GREEN）
- 1.2 `_session_affinity(mem_row, cur_sid, cur_kind, decay) -> float` 纯函数（无 I/O）
- 1.3 retriever `recall()` 在 RRF 融合后、裁 top_k 前乘 affinity；decay 由调用方
  从 `config.toml [companion].memory_cross_session_decay` 读出后经新 kwarg
  `cross_session_decay` 传入；缺失安全默认 `0.15`（本 Phase 不写 config.toml）
- 1.4 `_is_project_class(mem_row)` 项目类 vs 人物类判定（is_summary / tool_calls /
  `code-` 前缀 + 路径·代码特征），规则优先无 LLM；附 `_session_kind()` 纯函数
- 1.5 不回归：`test_deskpet_retriever.py` 17/17 全绿；全量后端套件 0 失败

## Design conformance (design.md §D1 / specs/memory-recall/spec.md)

affinity 矩阵实现如设计：

| 情形 | affinity |
|---|---|
| same session | 1.0 |
| cur=companion, mem from code, 项目/任务类 | `decay`（默认 0.15）|
| cur=companion, mem from code, 人物/偏好/闲聊类 | 0.8（轻降，桌宠仍记得你）|
| cur=code, mem from other code session | 0.5 |
| 其它 | 1.0 |
| `decay >= 1.0`（Strangler-Fig）| 全部 1.0（旧行为）|

关键设计点：

- **乘性、非过滤**：人物/偏好类跨 session 记忆只乘 0.8（> 0.15），保留
  "桌宠记得你"（spec: Cross-session person/preference memory still recalled）。
- **affinity 在裁 top_k 之前应用**：被降权的跨 session 项目记忆真正掉 rank，
  不只是压分仍占坑（spec: SHALL NOT rank above memories relevant to current
  request）。降权后按加权分重排，tie-break 规则与 `_rrf_fuse` 一致。
- **opt-in 退回**：`recall()` 不传 `cur_session_id`/`cur_session_kind` →
  完全跳过 affinity（现有调用方 `recall(query, top_k=...)` 字节级零影响）。
  这是 1.5 不回归 + Strangler-Fig 的双保险。
- **未动 L3 BGE-M3 / sqlite-vec 查询本身**，只在融合阶段乘权重（design.md §D1）。
- **失败隔离**：session-meta 补取异常只 log，退回未降权 fused（不破坏
  retriever 既有降级契约）。

## Steps

1. 读 design.md §D1 + specs/memory-recall/spec.md（authoritative，从 master
   分支 `git show` 取得 —— 见下方 "Setup note"）。
2. 写失败测试 `tests/test_retriever_session_affinity.py`（21 例）。
3. 跑测试 → RED：`ImportError: cannot import name '_is_project_class'`。
4. 实现 `_session_kind` / `_is_project_class` / `_session_affinity` 纯函数
   + `recall()` 新 kwargs + `_fetch_session_meta` + `_apply_session_affinity`。
5. 跑测试 → GREEN：21/21。
6. 跑既有 `test_deskpet_retriever.py` → 17/17（无回归）。
7. 跑全量后端套件（exclude vector_worker，AGENTS.md 约定）→ 0 失败。

## Observation

### Verify cmd 1 — 本 Phase 新测试（最后 10 行）

```
tests/test_retriever_session_affinity.py::test_affinity_code_from_other_code_is_050 PASSED [ 71%]
tests/test_retriever_session_affinity.py::test_affinity_code_current_from_companion_memory_is_one PASSED [ 76%]
tests/test_retriever_session_affinity.py::test_affinity_decay_one_restores_legacy_all_one PASSED [ 80%]
tests/test_retriever_session_affinity.py::test_affinity_missing_session_id_safe_default PASSED [ 85%]
tests/test_retriever_session_affinity.py::test_recall_without_session_context_is_legacy PASSED [ 90%]
tests/test_retriever_session_affinity.py::test_recall_companion_decays_cross_session_project_memory PASSED [ 95%]
tests/test_retriever_session_affinity.py::test_recall_decay_one_matches_legacy_ordering PASSED [100%]

============================= 21 passed in 0.82s ==============================
```

### Verify cmd 2 — 全量后端套件（最后 8 行，无回归）

```
........................................................................ [ 53%]
.............s....s...sssssssss......................................... [ 62%]
.............................................................s.......... [ 71%]
........................................................................ [ 80%]
........................................................................ [ 89%]
........................................................................ [ 98%]
..............s                                                          [100%]
794 passed, 13 skipped, 4 deselected in 55.45s
```

既有 retriever 套件单独跑（1.5 不回归直接证据）：`17 passed in 4.32s`。

## Conclusion

- ✅ same-session=1.0 / companion←code 项目类=decay / companion←code 人物类=0.8
  / code←code=0.5 / `decay=1.0` 退回旧行为 — 全部按 spec scenario 断言通过。
- ✅ 端到端：mock "code-tyfbt62t VPN 项目" 高权记忆 + `default` session 请求 →
  VPN 记忆融合分 ≤ legacy * 0.15（复现 2026-05-16 bug 的 D1 侧断言）。
- ✅ `_session_affinity` 是纯函数（无 I/O），21 例密集单测可独立验证。
- ✅ 现有 retriever 测试 17/17 + 全量套件 0 失败 — 无回归。
- Deviations:
  - **Setup note**: 本 worktree 分支 `worktree-agent-a2163f48f30bc4610` 实际
    branched 自 `c6ed551`（在 master `370d41b` 之前），OpenSpec change 目录
    与 `openspec/AGENTS.md` 在该提交尚不存在。authoritative 设计/spec 已从
    `git show master:...` 取得并严格遵循；evidence 目录按指示新建。
    `backend/deskpet/code_mode/state.py` 在该提交也不存在 —— 但 design.md §D1
    已完整规定 session-kind 规则（`code-` 前缀=code，其余=companion），按该
    权威规则实现，未触碰该文件（本属其它 Phase）。
  - 全量套件计数 794（< AGENTS.md 953 基线），因分支落后于 master 而非本
    改动引入失败；本 Phase 关注的 retriever 相关测试全绿、0 failure。
- Followup（移交 lead / 后续 Phase）：
  - `recall()` 已加 `cur_session_id` / `cur_session_kind` / `cross_session_decay`
    三个**可选** kwargs。**调用方接线（chat handler / memory manager
    `_safe_l3` 传当前 session + 读 config）不在 Phase 1 scope** —— 需后续
    Phase / lead 整合时把当前 session 上下文 + `config.toml [companion]
    .memory_cross_session_decay` 接进 `recall()` 调用点，否则降权不生效
    （默认 opt-out 保证零回归，但也意味着默认不启用）。
  - Phase 4 落 `config.toml [companion]` 段后，确认默认 `0.15` 与本 Phase
    安全默认一致。
```
