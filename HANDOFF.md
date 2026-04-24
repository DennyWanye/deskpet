# P4-S3 Handoff — L3 混合召回 + RRF Retriever

Branch: `worktree-agent-a338b86c`
Based on: `47a19cd feat(p4-s6): merge LLM multi-provider layer + agent loop (S6)`
Owner: P4-S3 expert agent
Slice: tasks.md §5 (P4-S3), spec memory-system Requirements "Hybrid Retrieval with RRF" / "Memory Decay and Salience" / "L3 failure degrades gracefully"

## What I built

| File | LOC | Purpose |
| --- | ---: | --- |
| `backend/deskpet/memory/retriever.py` | 655 | `Retriever` 四路 fan-out + RRF 融合 + salience boost + daily_decay（纯 helper 走最下面） |
| `backend/tests/test_deskpet_retriever.py` | 555 | 17 个测试覆盖 spec 全部 Scenarios + 多源融合 + 边界 + 降级 |

实现要点（对照 tasks.md §5）:

* §5.1 `Retriever.recall(query, top_k)` 入口
* §5.2 `asyncio.gather` 并行四路信号（vec / fts / recency / salience）
* §5.3 RRF fusion，权重从 `RetrievalPolicy` 注入，默认 0.5/0.3/0.15/0.05，可由 caller 覆盖；k=60 作为常量 `_RRF_K`
* §5.4 命中后 `_boost_salience(hits, boost=0.05)` 一条 UPDATE 批量刷 `salience` + `decay_last_touch`，salience clamp 到 `_SALIENCE_MAX=1.0`
* §5.5 `daily_decay(session_db, decay_lambda)` 顶层 async 函数，`salience *= exp(-lambda * days_since_touch)`，Python 侧算（SQLite 没内建 `exp()`），executemany UPDATE 回去
* §5.6 两个语义召回场景 + recency boost 场景（test_semantic_*，test_recent_items_boosted）
* §5.7 三个 L3 降级场景（`_vec_recall` 抛异常 / embedder 未 ready / 所有源都挂）

额外的附加设计:

* `Hit` dataclass 的 `source` 字段指向贡献 RRF 分最大的那个源，同分时按 `vec > fts > recency > salience` tie-break
* `_quote_fts_phrase` 把 query 用 `"..."` 包成 FTS5 phrase 字面量，避免 `:`、`(`、`)` 等保留字符让 MATCH 抛 `OperationalError`
* 每路 `_safe_call` 包裹 —— 单路挂不拖垮其它路，symm 和 spec "degrades gracefully" 契约
* 所有 magic number 都提成模块常量 (`_RRF_K`, `_DEFAULT_SALIENCE_BOOST`, `_SALIENCE_MAX/MIN`, `_SECONDS_PER_DAY`)，docstring 引用 spec / design

## Test results

Final pytest line:

```
============================= 17 passed in 6.95s ==============================
```

全部 17 个新测试通过:

| Scenario | Test |
| --- | --- |
| 语义相似召回 | `test_semantic_similar_recalled`, `test_semantic_literal_mismatch_still_recalled` |
| recency boost | `test_recent_items_boosted` |
| salience boost | `test_recall_boosts_salience`, `test_salience_boost_clamped_to_max` |
| daily_decay | `test_daily_decay_applies_exponential`, `test_daily_decay_idempotent_when_fresh` |
| L3 降级不抛 | `test_l3_degrade_returns_other_sources`, `test_l3_degrade_when_embedder_not_ready`, `test_all_sources_failing_returns_empty` |
| RRF 合并多源 | `test_rrf_fusion_combines_sources`, `test_rrf_pure_helper_edge_cases`, `test_rrf_dominant_source_tiebreak` |
| 其它 | `test_empty_query_returns_empty`, `test_quote_fts_phrase_escapes_quotes`, `test_retriever_exposes_policy`, `test_recall_returns_hit_dataclass` |

未破坏基线:

* `tests/test_deskpet_session_db.py` + `tests/test_deskpet_embedder.py` + `tests/test_deskpet_vector_worker.py` + `tests/test_deskpet_session_db_hook.py` → 43 passed, 1 deselected (model_required)
* `tests/test_hybrid_router*.py` (P3 legacy) → 40 passed
* 累计：**100 passed, 1 deselected in 32.88s**（合并跑一次）

## Deviations from spec (and why)

1. **`test_hybrid_router_fallback.py` 不存在** —— handoff prompt 要求保持该文件绿，但此分支实际没这个文件。用 `test_hybrid_router_cloud_first.py` + `test_hybrid_router_cloud_swap.py` 替代（名字差异，作用相同），确认无回归。
2. **Worktree 起点调整** —— 我的 worktree branch `worktree-agent-a338b86c` 被 checkout 在 `7d54c16`（pre-S1）上，而 handoff 说的 S6 merge baseline `47a19cd` 才是真正有 S1/S2/S5/S6 产物的点。我用 `git reset --hard 47a19cd` 把本地 worktree 基点移到正确位置（worktree 分支是 throwaway、无自有提交，这个操作安全）。
3. **RRF 权重未归一化** —— spec / handoff 注明"权重可以不归一"，我实现完全尊重 caller 传入的权重，但文档 + 测试都说明了这一点。
4. **`_vec_recall` 用 `WHERE embedding MATCH ? AND k = ?` 语法** —— 不是 spec 原文的 `ORDER BY distance LIMIT 20`（那是 brute-force 语义）；sqlite-vec 推荐的 KNN 语法会让扩展走内建的 top-k 剪枝，性能更好。返回顺序仍是 distance 升序。
5. **`_boost_salience` 走单条 SQL** 而非循环调 `SessionDB.update_salience` —— 20 条 recall 结果如果每条起一次事务开销太大；直接一次 UPDATE IN (…) 批量做，功能等价。

## Manual smoke (how a reviewer runs this by hand)

```bash
cd backend
python -m pytest tests/test_deskpet_retriever.py -v
# 期望: 17 passed
```

端到端冒烟（和我自己跑过的一致）:

```bash
python -c "
import asyncio, tempfile
from pathlib import Path
from deskpet.memory.session_db import SessionDB
from deskpet.memory.embedder import Embedder
from deskpet.memory.vector_worker import VectorWorker
from deskpet.memory.retriever import Retriever, RetrievalPolicy, daily_decay

async def smoke():
    tmp = Path(tempfile.mkdtemp())
    w_hold = []
    async def hook(mid, text):
        if w_hold: await w_hold[0].enqueue(mid, text)
    db = SessionDB(tmp / 'state.db', on_message_written=hook)
    await db.initialize()
    e = Embedder(model_path=Path('/nonexistent'), use_mock_when_missing=True)
    await e.warmup()
    w = VectorWorker(e, db, batch_size=8, flush_interval_s=0.1)
    w_hold.append(w)
    await w.start()
    sid = await db.create_session()
    for c in ['hello python', 'goodbye world', 'python async rocks', 'foo bar', 'python typing']:
        await db.append_message(sid, 'user', c)
    await asyncio.sleep(0.3)
    r = Retriever(db, e, RetrievalPolicy(top_k=5))
    hits = await r.recall('python', top_k=3)
    for h in hits: print(h.message_id, round(h.score,5), h.source, h.text)
    print('decay changed:', await daily_decay(db, decay_lambda=0.02))
    await w.stop(); await e.close(); await db.close()
asyncio.run(smoke())"
```

期望输出：3 条 hits（文本里包含 "python" 的），score 都 ≈0.016，source=vec；decay changed=0（因为 recall 刚刷过 decay_last_touch）。

## Open questions / tech debt

1. **vec rank tie-breaker 不稳定**：同文本的两条消息向量完全一样，sqlite-vec ORDER BY distance 后的二级排序依赖实现细节（大多数情况下按 rowid ASC）。我在 `_recency_recall` / `_salience_recall` 已用 `id DESC` 作 tie-break，但 vec 路交给了扩展——如果后续有场景依赖绝对稳定的 vec 顺序，可在 Python 侧二次排序。
2. **RRF weight config 未从 `config.toml` 自动读**：handoff 说"caller 传 policy"，我照做；后续 S7 的 ContextAssembler 负责把 config 解析成 policy 传下来，这一步我没做。
3. **FTS query 保留字符的稳健处理**：我把整条 query 包成 phrase 字面量，但 `"` 以外的边界字符（null byte、极长字符串、正文超过 FTS5 内部 token 限制等）理论上仍能触发 `OperationalError`；`_fts_recall` 捕获后 fallback 为 0 条——比抛异常好，但损失了这路信号。后续如果需要更智能的 query 清洗，可拆成独立 helper。
4. **daily_decay 没有分页**：10 万条一次性拉全表理论上够用（单次 < 500ms），但极端规模时可以分批。P4-S12 perf 验收再评估。
5. **Retriever 访问了 `SessionDB._db_path`** 这是 underscore-prefixed 属性。VectorWorker 也这么做了（有前例），但严格来说 SessionDB 应该暴露一个公共 `db_path` 属性。作为后续小 refactor，不是 blocker。
6. **没加 perf 测试**：P4-S3 没在 tasks.md §5 要求 perf 数字（§5 只说"单测覆盖"），但 P4-S12 性能回归会跑 `bench_phase4.py` 验证 recall p95 < 30ms。

## 后续 slice 如何接入

* **P4-S4 MemoryManager.recall** 会注入 `Retriever` 实例，policy 从 `config.toml [memory.rrf]` 解析
* **P4-S7 ContextAssembler MemoryComponent** 会传 task-type-specific 的 `RetrievalPolicy` 进来（命令类任务可能抑制 vec 权重、对话类任务强化 recency 等）
* **启动脚本** 应在 `main.py` 启动后跑一次 `await daily_decay(session_db, decay_lambda=0.02)`，建议放在 backfill_missing 后面、FastAPI 路由挂载前

---

Generated 2026-04-24, ready for Lead review.
