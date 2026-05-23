# Stage 0 — memory-v2 死代码体检报告（WI-M0.1）

**日期**: 2026-05-22
**方法**: `tests/test_memory_v2_smoke.py` —— 逐模块构造 + 最小真实调用,
捕获接口腐烂（TypeError/AttributeError）。10/10 用例通过。

## 逐模块结论

| 模块 | 状态 | 备注 |
|---|---|---|
| `memory_v2_schema.ensure_memory_v2_tables` | ✅ 可直接用 | 7 张表全部建出 |
| `facts.FactsStore` | ✅ 可直接接入 | upsert/list_active/search 接口正常 |
| `facts.FactExtractor` | ✅ 可直接接入 | `process_message(message_id=,content=,role=)` 正常 |
| `workspace.WorkspaceMemoryStore` | ✅ 可直接接入 | `record_action` async、`get` 正常 |
| `chunker.MessageChunker` | ✅ 可直接接入 | `chunk_message(message_id=,content=)` 正常 |
| `reflection.ReflectionWorker` | ✅ 可直接接入 | `(db_path, facts_store, llm_call)` 构造正常 |
| `reflection.SkillMemoryStore` | ✅ 可直接接入 | `add(SkillMemoryEntry)` / `list_all` 正常 |
| `query_rewriter.{Noop,LLM}QueryRewriter` | ✅ 可直接接入 | `rewrite(query, context=)` 正常 |
| `reranker.MockReranker` | ✅ 可直接接入 | `rerank` 正常；`is_mock()` 可用于自动 bypass |
| `enhanced_retriever.EnhancedRetriever` | ✅ 可直接接入 | 构造签名正常；**`facts_weight` 默认 0.0** —— 接入时必须显式传 0.2 |

## 关键结论

- **接口未腐烂**：架构评审 R1（"Phase A-E 写于 2026-05，底层接口可能已变"）
  风险**解除** —— 全部模块对当前接口仍兼容。memory-v2 是"未接入"，不是
  "腐烂"。
- 体检逻辑已固化为 `tests/test_memory_v2_smoke.py`，纳入长期回归。

## 待接入时注意（非腐烂，是设计点）

- `EnhancedRetriever.facts_weight` 默认 0.0 → facts 永不进结果。WI-M1.4
  构造时必须显式传（见 PRD §3.1 / D5）。
- `EnhancedRetriever._collect_fact_hits` 当前调 `FactsStore.search()`（LIKE）
  —— WI-M1.4 须按 PRD §3.1 改为向量召回。
- `FactExtractor.process_message` 强制要 `role=` 参数 —— `_on_message_written`
  需扩签名（见 PRD WI-M1.2）。
