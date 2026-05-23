# TDD — DeskPet 记忆系统 Stage 2：技术设计 + 测试规格

**关联**: `00-PRD.md`（**第 2 版**）
**状态**: **第 2 版** —— 已过架构评审 round1，按评审意见修订
**原则**: 测试先行。每个 WI 的实现以"让本文用例全绿"为完成标准。
**核心纪律**: 沿用 Stage 1 教训 ——
> Phase A-E 当初单元测试全绿但功能零生效，根因是只测了"模块本身"、没测"接入后真实运行栈行为"。

本文每个 WI **必须有集成测试**，断言"wire 进生产链路后，真实调用链里 X 确实发生了"。单元测试不可替代它。

> ## 第 2 版修订要点
> 1. §A1.3 重写工具注册：模块顶层 `registry.register(...)` + `bind()` setter（无 @register_tool / 无 _ctx）
> 2. §A1.4 新增 MemoryPanel facts view 详细设计（5th view + 🗑 + undo + op_id）
> 3. §A2.1 LIKE 只查 value 列（不查 subject/key 避免噪声）
> 4. §A2.2 RegexEntityExtractor 加停用词集
> 5. §A2.3 entity_weight 默认 0.10（v1 是 0.15）
> 6. §A3.1 strict 模式 CI 自动化触发（git diff），不靠 PR template
> 7. §A4.1 同时改 VALID_CATEGORIES + _CATEGORY_DECAY + process_message source
> 8. §A4.2 background_tasks set + add_done_callback + shutdown gather
> 9. TG-S0 加 TS0-6 ALTER 失败兜底用例
> 10. TG-S1 加 TS1-13 NULL old_id 用例
> 11. TG-S2 加 TS2-8 forgotten_at 防覆盖用例

---

## A. 技术设计

### A0. schema 演进（v2 修订 D1 双列）

新文件 `backend/deskpet/memory/schema_v2_migrator.py`：

```python
"""Additive column migration for memory-v2 tables.

Strategy: PRAGMA table_info(<table>) introspection + ALTER TABLE ADD
COLUMN. Idempotent. Cached per-path.

v2: 双列 — superseded_by + forgotten_at；ALTER 失败时记录到全局
state 让 main.py 关相关 flag（R8 v2 加固）。
"""

import aiosqlite
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# table -> list[(col_name, ddl_fragment)]
_COLUMN_ADDS = {
    "facts": [
        ("superseded_by", "INTEGER REFERENCES facts(id)"),
        ("forgotten_at", "REAL"),
    ],
}

# Tracks ALTER failures for main.py to consult and disable dependent flags.
# {col_name: True} means that column is NOT available (ALTER failed or table missing).
_ALTER_FAILURES: dict[str, bool] = {}


async def ensure_memory_v2_columns(db_path: str | Path) -> dict[str, bool]:
    """Return: {col_name: True/False(=available)}. main.py 据此关 flag."""
    availability = {}
    async with aiosqlite.connect(db_path) as conn:
        for table, cols in _COLUMN_ADDS.items():
            existing = await _list_columns(conn, table)
            if not existing:
                # 表不存在（理论上 ensure_memory_v2_tables 先跑）
                for col_name, _ in cols:
                    availability[col_name] = False
                continue
            for col_name, ddl in cols:
                if col_name in existing:
                    availability[col_name] = True
                    continue
                try:
                    await conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {ddl}"
                    )
                    await conn.commit()
                    availability[col_name] = True
                    log.info("v2_migrator: ALTER TABLE %s ADD %s OK",
                             table, col_name)
                except Exception as e:  # noqa: BLE001
                    log.warning("v2_migrator: ALTER %s.%s FAILED: %s",
                                table, col_name, e)
                    availability[col_name] = False
                    _ALTER_FAILURES[col_name] = True
    return availability


async def _list_columns(conn, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    return {row[1] for row in rows}
```

**接入**：`memory_v2_schema.ensure_memory_v2_tables` 在 `await conn.commit()` 之后调用：

```python
availability = await ensure_memory_v2_columns(db_path)
# main.py lifespan 读 availability：
#   if not availability.get("superseded_by"): cfg.memory.v2.cross_key_merge = False
#   if not availability.get("forgotten_at"):  cfg.memory.v2.memory_forget = False
```

**`_DDL`** 中 facts 表 CREATE TABLE 同步更新带 `superseded_by INTEGER REFERENCES facts(id), forgotten_at REAL`（fresh DB 一次到位）。

### A1. cross-key 矛盾治理（WI-S2.1a）

#### A1.1 FactsStore 新方法（v2 加 mark_forgotten + restore_from_undo）

```python
async def mark_superseded(
    self, *, old_id: int, superseded_by: int,
) -> None:
    """原子标 old_id 为 inactive 且记录被谁推翻。"""
    await self._ensure_schema()
    async with aiosqlite.connect(self._db_path) as conn:
        await conn.execute(
            "UPDATE facts SET is_active = 0, superseded_by = ? "
            "WHERE id = ? AND is_active = 1",
            (int(superseded_by), int(old_id)),
        )
        await conn.commit()


async def mark_forgotten(
    self, fact_id: int, *, op_id: str, ts: float,
) -> None:
    """memory_forget 标 inactive + forgotten_at（v2 R-MISS-2 防覆盖）。"""
    await self._ensure_schema()
    async with aiosqlite.connect(self._db_path) as conn:
        await conn.execute(
            "UPDATE facts SET is_active = 0, forgotten_at = ? "
            "WHERE id = ? AND is_active = 1",
            (float(ts), int(fact_id)),
        )
        # 记 op_id → 多个 fact_id 的关系到独立审计表（或塞 evidence 字段）
        # 这里简化方案：把 op_id 写进 evidence 末尾 `[forget_op:<op_id>]`
        await conn.execute(
            "UPDATE facts SET evidence = COALESCE(evidence, '') || ? "
            "WHERE id = ?",
            (f"\n[forget_op:{op_id}]", int(fact_id)),
        )
        await conn.commit()


async def restore_from_undo(
    self, op_id: str, *, max_age_seconds: float = 5.0,
) -> list[int]:
    """5 秒 undo 窗口内 restore；超时返回 []。"""
    await self._ensure_schema()
    now = time.time()
    cutoff = now - max_age_seconds
    async with aiosqlite.connect(self._db_path) as conn:
        cur = await conn.execute(
            "SELECT id FROM facts "
            "WHERE forgotten_at IS NOT NULL AND forgotten_at >= ? "
            "AND evidence LIKE ?",
            (cutoff, f"%[forget_op:{op_id}]%"),
        )
        ids = [int(r[0]) for r in await cur.fetchall()]
        await cur.close()
        if not ids:
            return []
        await conn.executemany(
            "UPDATE facts SET is_active = 1, forgotten_at = NULL WHERE id = ?",
            [(fid,) for fid in ids],
        )
        await conn.commit()
    return ids


async def is_forgotten_recently(
    self, *, subject: str, key: str, within_days: int = 7,
) -> bool:
    """FactExtractor 写入前查：subject/key 是否最近 N 天内被 forgotten。
    防 R-MISS-2 "忘记后又被插回覆盖"。
    """
    await self._ensure_schema()
    cutoff = time.time() - within_days * 86400
    async with aiosqlite.connect(self._db_path) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM facts "
            "WHERE subject = ? AND key = ? "
            "AND forgotten_at IS NOT NULL AND forgotten_at >= ? "
            "LIMIT 1",
            (subject, key, cutoff),
        )
        row = await cur.fetchone()
        await cur.close()
    return row is not None


async def vector_search_in_subject(
    self, query_embedding, *, subject: str, limit: int = 10,
) -> list[dict]:
    """限 same subject 的向量召回（D3 v2 混合视野的语义部分）。"""
    # 复用 vector_search 逻辑 + WHERE subject = ?
    ...
```

#### A1.2 FactExtractor 流程改造（v2 D3 混合视野 + R-MISS-2）

```python
async def _persist_extracted(self, extracted, message_id):
    for fact in extracted:
        # R-MISS-2: 最近 7 天内被 forgotten 的 fact 不重新插
        if await self._store.is_forgotten_recently(
            subject=fact.subject, key=fact.key, within_days=7,
        ):
            log.debug("Skip fact %s: forgotten within 7 days", fact.key)
            continue

        # 旧路径：same (subject, key) merge
        existing = await self._store.find_active(
            subject=fact.subject, key=fact.key,
        )
        if existing:
            decision = await self._decide_merge(fact, existing)
            ... # 旧逻辑
            continue

        # 新路径（A1 v2）：cross-key conflict scan 混合视野
        if self._cross_key_merge_enabled:
            # ① 最近 20 条
            recent = await self._store.list_active(
                subject=fact.subject, limit=20,
            )
            # ② 语义最近 10 条（限 same subject）
            semantic = []
            if self._embedder is not None:
                query_emb = await self._embedder.embed(fact.value)
                semantic = await self._store.vector_search_in_subject(
                    query_embedding=query_emb,
                    subject=fact.subject,
                    limit=10,
                )
            # ③ 合并去重，上限 25
            candidates = _merge_dedupe_facts(recent, semantic, limit=25)
            if candidates:
                conflict_decision = await self._decide_cross_key_conflict(
                    new_fact=fact, candidates=candidates,
                )
                # decision: { conflicts: [{old_id, reason}], should_insert }
                new_fid = None
                if conflict_decision.should_insert:
                    new_fid = await self._store.upsert(...)

                for old in conflict_decision.conflicts:
                    if not isinstance(old.get("old_id"), int):
                        log.warning("Invalid old_id in conflict: %s", old)
                        continue   # TS1-13 NULL/非法 ID 防护
                    if new_fid is not None:
                        await self._store.mark_superseded(
                            old_id=old["old_id"], superseded_by=new_fid,
                        )
                continue

        # fallback
        await self._store.upsert(...)
```

#### A1.3 memory_forget 工具（v2 重写 — 顶层 register + bind 模式）

`backend/deskpet/tools/memory_tools.py`（**模块顶层注册，不依赖装饰器**）：

```python
"""memory_forget tool — module-level registration with lazy binding."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from deskpet.tools.registry import registry

log = logging.getLogger(__name__)

# Module-level handles — populated by `bind()` in main.py lifespan.
# import 时为 None（pkgutil discovery 跑时无活实例）。
_facts_store = None
_embedder = None
_llm_call = None


def bind(*, facts_store, embedder, llm_call) -> None:
    """main.py lifespan 调一次注入依赖。"""
    global _facts_store, _embedder, _llm_call
    _facts_store = facts_store
    _embedder = embedder
    _llm_call = llm_call
    log.info("memory_tools.bind: dependencies injected")


# OpenAI function schema
_SCHEMA = {
    "name": "memory_forget",
    "description": "Forget a previously-remembered fact. "
                   "Use when the user explicitly asks to forget something.",
    "parameters": {
        "type": "object",
        "properties": {
            "fact_id": {
                "type": "integer",
                "description": "Exact fact ID to forget (preferred when known)",
            },
            "query": {
                "type": "string",
                "description": "Natural-language description (slower, requires confirmation)",
            },
        },
    },
}


async def _handle(args: dict, task_id: str) -> str:
    """ToolRegistry handler protocol: (args, task_id) -> json str."""
    if _facts_store is None:
        return json.dumps({
            "status": "error",
            "reason": "memory_forget tool not bound (main.py lifespan issue)",
        })

    fact_id = args.get("fact_id")
    query = args.get("query")

    if fact_id is not None:
        return await _forget_by_id(int(fact_id))
    if query:
        return await _forget_by_query(str(query))
    return json.dumps({
        "status": "error",
        "reason": "需 fact_id 或 query 之一",
    })


async def _forget_by_id(fact_id: int) -> str:
    """ID 模式 — 永远开启。"""
    op_id = uuid.uuid4().hex
    try:
        await _facts_store.mark_forgotten(
            fact_id, op_id=op_id, ts=time.time(),
        )
    except Exception as e:  # noqa: BLE001
        return json.dumps({"status": "error", "reason": str(e)})
    return json.dumps({
        "status": "ok",
        "op_id": op_id,
        "forgotten_ids": [fact_id],
    })


async def _forget_by_query(query: str) -> str:
    """自然语言模式 — D5 v2 工具自身规则拦截。"""
    # 工具自身规则 1：query 长度
    if len(query.strip()) < 6:
        return json.dumps({
            "status": "skipped",
            "reason": "query 过短（< 6 字），拒绝执行",
        })

    # 1. 向量召回 top 5
    try:
        candidates = await _facts_store.vector_search(
            await _embedder.embed(query), limit=5,
        )
    except Exception as e:  # noqa: BLE001
        return json.dumps({"status": "error", "reason": str(e)})

    if not candidates:
        return json.dumps({"status": "not_found"})

    # 工具自身规则 2：命中数过多
    if len(candidates) > 5:
        return json.dumps({
            "status": "skipped",
            "reason": "query 过宽（命中 > 5 fact），拒绝执行",
        })

    # 2. LLM 二次确认
    try:
        confirmed_ids = await _llm_confirm_forget(query, candidates)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"status": "error", "reason": str(e)})

    # 工具自身规则 3：单次最多 3 条
    confirmed_ids = confirmed_ids[:3]
    if not confirmed_ids:
        return json.dumps({
            "status": "skipped",
            "reason": "LLM 未确认任何 fact 要遗忘",
            "candidates": [c["id"] for c in candidates],
        })

    # 3. 标 forgotten
    op_id = uuid.uuid4().hex
    now = time.time()
    for fid in confirmed_ids:
        await _facts_store.mark_forgotten(fid, op_id=op_id, ts=now)

    return json.dumps({
        "status": "ok",
        "op_id": op_id,
        "forgotten_ids": confirmed_ids,
    })


async def _llm_confirm_forget(query: str, candidates: list[dict]) -> list[int]:
    """LLM 看 query + 候选 facts，返回应被遗忘的 ID 列表。"""
    prompt = _CONFIRM_PROMPT.format(
        query=query,
        candidates="\n".join(
            f"- id={c['id']} {c['key']}={c['value']}" for c in candidates
        ),
    )
    raw = await _llm_call(prompt)
    # 解析 JSON {ids: [...]}
    return _parse_confirm_response(raw)


# ─────────────────────────────────────────────────────
# 模块顶层 register — pkgutil discovery 触发时执行
# ─────────────────────────────────────────────────────
registry.register(
    name="memory_forget",
    toolset="memory",
    schema=_SCHEMA,
    handler=_handle,
    permission_category="write_file",   # D5 v2 触发 UI 确认
    dangerous=True,
)
```

**main.py lifespan 注入**（约 L1300 附近，feature flag 检查后）：

```python
if cfg.memory.v2.memory_forget:
    # 仅当 forgotten_at 列可用才启用
    if availability.get("forgotten_at"):
        from deskpet.tools import memory_tools
        memory_tools.bind(
            facts_store=_facts_store,
            embedder=_embedder,
            llm_call=llm_call_func,
        )
        log.info("p4_memory_forget_tool_bound")
    else:
        log.warning("memory_forget disabled: forgotten_at column unavailable")
```

#### A1.4 MemoryPanel facts view（v2 新增 — WI-S2.1b 详细设计）

**后端 ws 路由**（加进 `p4_ipc.py` 路由表，E3 v2）：

```python
# p4_ipc.py 路由表加（约 L20）：
"memory_facts_list": _handle_memory_facts_list,
"memory_forget": _handle_memory_forget_ws,
"memory_forget_undo": _handle_memory_forget_undo,


async def _handle_memory_facts_list(payload, ws_send, *, facts_store, **_):
    limit = payload.get("limit", 200)
    rows = await facts_store.list_active(limit=limit)
    await ws_send({
        "type": "memory_facts_list_response",
        "facts": rows,
    })


async def _handle_memory_forget_ws(payload, ws_send, *, facts_store, llm_call, embedder, **_):
    # 直接调工具实现而不走 registry —— 因为 UI 是确认过的来源
    from deskpet.tools.memory_tools import _forget_by_id, _forget_by_query
    fact_id = payload.get("fact_id")
    result_str = await _forget_by_id(int(fact_id)) if fact_id else await _forget_by_query(payload.get("query", ""))
    await ws_send({
        "type": "memory_forget_response",
        **json.loads(result_str),
    })


async def _handle_memory_forget_undo(payload, ws_send, *, facts_store, **_):
    op_id = payload["op_id"]
    restored = await facts_store.restore_from_undo(op_id, max_age_seconds=5.0)
    await ws_send({
        "type": "memory_forget_undo_response",
        "restored_ids": restored,
        "status": "ok" if restored else "expired",
    })
```

**前端 `tauri-app/src/components/MemoryPanel.tsx` 改动**：

```tsx
type View = "turns" | "l1" | "search" | "skills" | "facts";  // 加 facts


type FactItem = {
  id: number;
  category: string;
  subject: string;
  key: string;
  value: string;
  updated_at: number;
  is_active: number;
  superseded_by?: number;
};


// 在已有 useEffect 旁加：
const [facts, setFacts] = useState<FactItem[]>([]);
const [pendingForget, setPendingForget] = useState<
  { op_id: string; fact: FactItem; expires_at: number } | null
>(null);


// 进 facts view 时拉取
useEffect(() => {
  if (view === "facts") {
    sendWS({ type: "memory_facts_list", limit: 200 });
  }
}, [view]);


// 处理响应
useEffect(() => {
  const off = onWS((msg) => {
    if (msg.type === "memory_facts_list_response") setFacts(msg.facts);
    if (msg.type === "memory_forget_response" && msg.status === "ok") {
      const forgotten = facts.find((f) => f.id === msg.forgotten_ids[0]);
      if (forgotten) {
        setFacts((prev) => prev.filter((f) => f.id !== forgotten.id));
        setPendingForget({
          op_id: msg.op_id,
          fact: forgotten,
          expires_at: Date.now() + 5000,
        });
        setTimeout(() => setPendingForget(null), 5000);
      }
    }
    if (msg.type === "memory_forget_undo_response" && msg.status === "ok") {
      setFacts((prev) => [pendingForget!.fact, ...prev]);
      setPendingForget(null);
    }
  });
  return off;
}, [facts, pendingForget]);


// segTab 加 facts；renderView() 分支加 facts 列表（每条 + 🗑）
// undo 浮窗组件单独渲染（pendingForget 非空时显示）
```

### A2. entity 索引（WI-S2.2，v2 修订）

#### A2.1 FactsStore.find_by_entities — LIKE 只查 value 列（v2 ★）

```python
async def find_by_entities(
    self, entities: list[str], *, limit: int = 10,
) -> list[dict]:
    if not entities:
        return []
    await self._ensure_schema()
    seen: dict[int, dict] = {}
    async with aiosqlite.connect(self._db_path) as conn:
        conn.row_factory = aiosqlite.Row
        for e in entities[:5]:
            if len(e.strip()) < 2:
                continue
            pat = f"%{e.strip()}%"
            cur = await conn.execute(
                "SELECT * FROM facts "
                "WHERE is_active = 1 AND value LIKE ? "  # ← v2 只查 value
                "ORDER BY updated_at DESC LIMIT ?",
                (pat, int(limit)),
            )
            for r in await cur.fetchall():
                seen.setdefault(r["id"], dict(r))
            await cur.close()
    rows = list(seen.values())
    rows.sort(key=lambda r: -float(r.get("updated_at") or 0))
    return rows[:limit]
```

#### A2.2 EntityExtractor 三档 + 停用词（v2 ★）

`backend/deskpet/memory/entity_extractor.py`：

```python
"""Entity extraction for query-side NER.

Three-tier degradation:
  1. LLMEntityExtractor — primary
  2. RegexEntityExtractor — fallback, with stopword filter
  3. NoopEntityExtractor — final fallback (returns [])
"""
import re
from typing import Protocol


# v2 ★ stopword set — 防"了吗""这个"等高频字被当 entity
_STOPWORDS = frozenset({
    # 中文高频
    "我的", "我们", "今天", "明天", "昨天", "这个", "那个", "什么",
    "怎么", "了吗", "的人", "的话", "的事", "有什么", "可以", "知道",
    "时候", "地方", "事情", "东西", "问题", "意思", "为什么", "怎样",
    "需要", "应该", "可能", "已经", "因为", "所以", "如果",
    # 英文高频
    "What", "When", "Where", "Why", "How", "The", "This", "That",
    "There", "These", "Those", "Some", "Any",
})


class EntityExtractor(Protocol):
    async def extract(self, query: str) -> list[str]: ...


class RegexEntityExtractor:
    _CN = re.compile(r"[一-龥]{2,4}")  # 2-4 字中文
    _EN = re.compile(r"\b[A-Z][a-zA-Z]{1,}\b")  # 大写英文词

    async def extract(self, query: str) -> list[str]:
        cn_hits = self._CN.findall(query)
        en_hits = self._EN.findall(query)
        candidates = {*cn_hits, *en_hits}
        # v2 ★过滤停用词
        filtered = [c for c in candidates if c not in _STOPWORDS]
        return filtered[:5]


class LLMEntityExtractor:
    def __init__(self, llm_call):
        self._llm = llm_call

    async def extract(self, query: str) -> list[str]:
        try:
            raw = await self._llm(_ENTITY_PROMPT.format(query=query))
            entities = _parse_entities(raw)
            # 同样过停用词（防 LLM 误抽）
            entities = [e for e in entities if e not in _STOPWORDS]
            return entities[:5]
        except Exception:
            return []


class NoopEntityExtractor:
    async def extract(self, query: str) -> list[str]:
        return []


class CompositeEntityExtractor:
    """LLM → Regex 降级链。"""
    def __init__(self, llm_ex, regex_ex):
        self._llm = llm_ex
        self._regex = regex_ex

    async def extract(self, query: str) -> list[str]:
        result = await self._llm.extract(query)
        if not result:
            result = await self._regex.extract(query)
        return result


_ENTITY_PROMPT = """\
Extract named entities (person names, pet names, place names, project
names, specific objects) from the user query. Output only a JSON list
of strings, e.g. ["旺财", "Mike", "上海"]. If no entities, output [].

Common stopwords to AVOID: 我的、我们、今天、这个、什么、怎么、了吗 等

Query: {query}

Output JSON only.
"""
```

#### A2.3 EnhancedRetriever 改造（v2 ★ entity_weight=0.10）

```python
def __init__(self, base, *,
    facts_store=None, facts_weight=0.0,
    reranker=None, query_rewriter=None,
    embedder=None, chunk_store=None,
    # 新增（A2 v2）
    entity_extractor=None, entity_weight=0.10,  # ← v2 ★默认 0.10
):
    ...
    self._entity_extractor = entity_extractor
    self._entity_weight = float(entity_weight)


async def _collect_entity_hits(self, query: str) -> list[Hit]:
    if self._entity_extractor is None or self._facts_store is None:
        return []
    entities = await self._entity_extractor.extract(query)
    if not entities:
        return []
    rows = await self._facts_store.find_by_entities(entities, limit=10)
    log.debug("entity_path: query=%r entities=%s hits=%d",
              query, entities, len(rows))
    return [self._fact_row_to_hit(r) for r in rows]
```

`recall()` 调用链：
```
1. query_rewriter (optional)
2. base.recall() → base_hits
3. _collect_fact_hits → fact_hits（向量召回）
4. _collect_entity_hits → entity_hits（新增 A2，LIKE only value）
5. _collect_chunk_hits → chunk_hits
6. RRF fuse with weights (base=1.0, facts=facts_weight, chunks=1.0, entity=entity_weight)
7. reranker (optional)
8. top_k slice
```

### A3. eval 门控严格化（WI-S2.3，v2 修订 D10 CI 自动化）

#### A3.1 strict 模式 + CI 自动化触发（v2 ★）

`scripts/eval_gate.py` 改动同 v1，加 `--strict`。

**v2 ★ 新增 `backend/scripts/eval_gate_ci.sh`**：

```bash
#!/bin/bash
# CI 看 git diff 自动判断是否需要 strict 模式
set -euo pipefail

cd "$(dirname "$0")/.."

# 比较与默认分支（main/master）的差异
BASE_REF="${BASE_REF:-origin/master}"
CHANGED=$(git diff --name-only "$BASE_REF...HEAD" 2>/dev/null || git diff --name-only HEAD~1 HEAD)

STRICT_FLAG=""
RECALL_PATTERN='(enhanced_retriever|.*_retriever|.*_extractor|facts|retriever|reranker|query_rewriter|chunker|memory_v2_schema)\.py$'

if echo "$CHANGED" | grep -qE "$RECALL_PATTERN"; then
    echo "[eval_gate_ci] 召回相关改动检测 → --strict"
    echo "[eval_gate_ci] changed: "
    echo "$CHANGED" | grep -E "$RECALL_PATTERN"
    STRICT_FLAG="--strict"
fi

python -m scripts.eval_gate $STRICT_FLAG
```

**新增 `.github/workflows/eval-gate.yml`**：

```yaml
name: Eval Gate
on:
  pull_request:
    paths:
      - 'backend/**/*.py'
      - 'backend/scripts/eval_gate*'
jobs:
  eval-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e backend
      - run: bash backend/scripts/eval_gate_ci.sh
        env:
          BASE_REF: origin/${{ github.base_ref }}
```

#### A3.2 update sanity（同 v1）

```python
async def _do_update_baseline(current, *, force: bool) -> int:
    old = _load_baseline()
    if old and not force:
        if float(current["hit@5"]) < float(old["hit@5"]) - _HIT_TOLERANCE:
            print(f"[eval_gate] 拒绝钉低 baseline: ...", file=sys.stderr)
            return 3
        if float(current["token_per_query"]) > float(old["token_per_query"]) * _TOKEN_GROWTH_MAX:
            print(f"[eval_gate] 拒绝钉高 token: ...", file=sys.stderr)
            return 3
    ...
```

### A4. episodic→semantic（WI-S2.4，v2 修订 D12/D13）

#### A4.1 三处必须同时改（v2 ★）

`backend/deskpet/memory/facts.py`：

```python
# Line ~60 — VALID_CATEGORIES
VALID_CATEGORIES = {
    "profile", "preference", "project", "event", "reflection",
    "episodic_summary",   # ← v2 加
}

# Line ~80 — _CATEGORY_DECAY
_CATEGORY_DECAY = {
    "profile": 0.005,
    "preference": 0.02,
    "project": 0.05,
    "event": 0.10,
    "reflection": 0.01,
    "episodic_summary": 0.01,   # ← v2 加，slower decay 长期保留
}

# Line ~502 — process_message 签名 + 联合白名单
async def process_message(
    self, *, message_id: int, content: str, role: str,
    source: str = "user_message",   # ← v2 加
) -> list[dict]:
    # v2 ★ 联合白名单
    if not (
        role in ("user", "assistant")
        or (role == "system" and source == "summarizer")
    ):
        return []
    if not content or len(content.strip()) < self._min_chars:
        return []
    # ... rest unchanged ...
    # v2 ★ category override for episodic_summary
    if source == "summarizer":
        for fact in extracted:
            fact.category = "episodic_summary"
    ...
```

#### A4.2 summarizer 集成 — background_tasks set（v2 ★ D-RISK-4）

`main.py` lifespan 加（约 L1100 附近）：

```python
# v2 ★ background tasks set 防 GC 静默吞 task
app.state.background_tasks: set[asyncio.Task] = set()
```

`summarizer.py` 改造：

```python
async def summarize_old_sessions(
    db_path, llm_call, *,
    fact_extractor: Optional[FactExtractor] = None,    # 新参数
    episodic_to_semantic: bool = False,                # 新参数
    background_tasks: Optional[set] = None,            # ★v2 新参数
    ...
):
    ...
    # ↓ 事务提交后
    await conn.commit()

    if vector_worker is not None:
        await vector_worker.enqueue(summary_id, summary_text)

    # v2 ★ episodic → semantic
    if episodic_to_semantic and fact_extractor is not None:
        task = asyncio.create_task(
            fact_extractor.process_message(
                message_id=summary_id,
                content=summary_text,
                role="system",
                source="summarizer",
            )
        )
        # ★v2 收集 + 自动清理
        if background_tasks is not None:
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
```

`main.py` shutdown 加：

```python
@app.on_event("shutdown")
async def _shutdown():
    # v2 ★ 等所有 episodic fact 抽取完成
    if app.state.background_tasks:
        log.info("Waiting for %d background tasks", len(app.state.background_tasks))
        await asyncio.gather(
            *app.state.background_tasks, return_exceptions=True
        )
```

### A5. MR-4 GUI 联调脚本（WI-V.1）

同 v1，加：**主 checkout venv 准备应提前并行启动**（评审 P2-2）。

`tests/e2e_workspace_memory.py` 含 retry 逻辑（首跑 venv 装 torch 可能 5-30 分钟）。

---

## B. 自动化测试规格（v2 新增 TS0-6 + TS1-13 + TS2-8）

### TG-S0 · schema migrator（WI-S2.1a 前置）

`tests/test_memory_schema_v2_migrator.py`

| # | 用例 | 断言 |
|---|---|---|
| TS0-1 | fresh DB（无 facts 表）→ `ensure_memory_v2_tables` + `ensure_memory_v2_columns` | facts 表存在；含 superseded_by + forgotten_at；availability 返 True×2 |
| TS0-2 | Stage 1 老库副本（无新列）→ `ensure_memory_v2_columns` | 两列被 ALTER 加上；availability 返 True×2 |
| TS0-3 | 重复调用 | 第二次为 no-op；不报错；列不被重复加 |
| TS0-4 | SQLite ALTER 失败模拟（patch conn.execute 抛错） | log warning；不 raise；availability 返 False；`_ALTER_FAILURES` 记录 |
| TS0-5 | 并发 5 个 task 调 | 不冲突；最终列存在恰好一次 |
| **TS0-6 ★v2** | TS0-4 后启动 main.py | `cfg.memory.v2.cross_key_merge` 自动设 False；log warning"availability 缺 superseded_by"  |

### TG-S1 · cross-key 矛盾治理（WI-S2.1a 核心）

`tests/test_memory_cross_key_conflict_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| TS1-1 | `cross_key_merge=true`，建 fact A `(user, allergy_peanut)`；抽出 `(user, allergy_seafood)`；mock LLM 返 `conflicts=[{old_id: A.id}], should_insert: true` | A `is_active=0, superseded_by=B.id`；list_active 只返 B |
| TS1-2 | 同上但 mock LLM 返 `conflicts=[], should_insert: true` | 两条都 active；A 不变 |
| TS1-3 | mock LLM 抛错 | 走 Stage 1 原 insert 路径；新 fact 插入；A 仍 active |
| TS1-4 | mock LLM 返非法 JSON | 同 TS1-3 |
| TS1-5 | `cross_key_merge=false` | 跳过 cross-key 分支；与 Stage 1 行为字节级一致 |
| TS1-6 | same-subject 已有 21 条 active + embedder 召回 10 条 | 候选去重后 ≤ 25；prompt 不超长 |
| TS1-7 | candidates prompt 内容 | 只含 id/key/value/updated_at；不含 evidence |
| TS1-8 | LLM 返 `conflicts=[{old_id: 999}]`（不存在 ID） | log warning；不 mark_superseded；新 fact 仍插入 |
| TS1-9 | 并发抽取（2 个 task）同 subject 不同 key 矛盾 | `_persist_lock` 串行；最终 1 active + 1 inactive |
| TS1-10 | `mark_superseded` 对已 inactive 调用 | UPDATE 无效（`WHERE is_active=1` 守护）；不报错 |
| TS1-11 | `list_superseded_chain` 3 级链 | 返回 3 条 fact，新→旧 |
| TS1-12 | LLM 返 `should_insert: false, conflicts=[]` | log warning；noop |
| **TS1-13 ★v2** | LLM 返 `conflicts=[{old_id: null, reason: "x"}]` 或 `{"old_id": "abc"}` | log warning；跳过该 entry；其他正常处理 |
| **TS1-14 ★v2** | D3 混合视野：构造 30 条同 subject facts，"花生过敏"是第 21 条（不在最近 20）；新 fact "海鲜过敏" | embedder 召回应把"花生过敏"拉进候选；LLM 能正确判矛盾 |

### TG-S2 · memory_forget 工具（WI-S2.1a 配套，v2 加 TS2-8）

`tests/test_memory_forget_tool_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| TS2-1 | `memory_forget(fact_id=N)` 对存在 fact | `is_active=0, forgotten_at=now()`；返回 status=ok + op_id |
| TS2-2 | `memory_forget(fact_id=999999)` | mark_forgotten 不报错（WHERE id 不命中即 noop）；返回 op_id |
| TS2-3 | `memory_forget(query="忘记我对花生过敏")` | vector_search 命中 → LLM 确认 → 标 forgotten |
| TS2-4 | query 长度 < 6 | 不调向量召回；返 status=skipped |
| TS2-5 | query 命中 > 5 fact | 返 status=skipped |
| TS2-6 | LLM 返"不确认" | 不删；返 status=skipped + candidates |
| TS2-7 | 无 fact_id 无 query | 返 status=error |
| **TS2-8 ★v2 R-MISS-2** | 删 fact A 后，user 再说同样的话触发 FactExtractor | `is_forgotten_recently(within_days=7)` 命中 → 跳过；不重新插 A |
| TS2-9 | undo 5 秒内 | restore_from_undo 返回 ids；fact `is_active=1, forgotten_at=NULL` |
| TS2-10 | undo 6 秒后 | restore_from_undo 返回 []；fact 仍 inactive |
| TS2-11 | 工具 registry 自动发现（fresh import test） | `memory_forget` 在 `list_tools()` 输出中；模块顶层 register 触发 |
| TS2-12 | bind 未调时 `_handle` 调用 | 返 status=error "not bound" |
| TS2-13 | 连续 5 次 forget 不同 fact，各自 op_id | 5 个 op_id 全不同；undo 用错 op_id 不 restore |

### TG-S3 · entity 索引（WI-S2.2，v2 修订）

`tests/test_memory_entity_path_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| TS3-1 | `entity_path=true`；fact value 含"旺财"；query "旺财怎么样了" | entity 路命中该 fact，进 recall |
| TS3-2 | `entity_path=false` | entity 路不走 |
| TS3-3 | LLMEntityExtractor 抛错；Composite 降级 regex | regex 提"旺财"；命中同上 |
| TS3-4 | regex 提不到（纯英文 query 含小写词） | entity_hits=[]；不影响其他路 |
| TS3-5 | `find_by_entities(["旺财", "Mike"], limit=10)` | 同时 LIKE value 列两词；去重；按 updated_at 排序 |
| TS3-6 | entity 列表超过 5 | 截断到前 5 |
| TS3-7 | 单 entity 长度 < 2 字 | 跳过该 entity |
| TS3-8 | recall 结果 fact_hits 与 entity_hits 命中同 fact id | RRF 去重；不重复出现 |
| **TS3-9 ★v2** | regex 输入 "今天怎么了" | "今天""怎么"被停用词过滤；返 `[]` 或空 |
| **TS3-10 ★v2** | LIKE 只查 value：subject="user" 大量 fact，query "user" 词 | entity_path 不会被 subject="user" 匹配（只查 value）|

### TG-S4 · eval 门控严格化（WI-S2.3）

`tests/test_eval_gate_strict.py`

| # | 用例 | 断言 |
|---|---|---|
| TS4-1 | `--strict` baseline = 当前结果 | exit != 0（hit@5 not > baseline）|
| TS4-2 | `--strict` 当前 hit@5 > baseline + 0.01 | exit 0 |
| TS4-3 | `--strict` token 超 30% | exit != 0 |
| TS4-4 | 默认 gate baseline = 当前结果 | exit 0（不回归是 ≤，不是 <）|
| TS4-5 | `--update-baseline` 当前 hit@5 < old - 容差 | 拒绝写入；exit 3 |
| TS4-6 | `--update-baseline --force` 同 TS4-5 | 写入成功；exit 0 |
| TS4-7 | `--update-baseline` 当前 token > old × 1.30 | 拒绝；exit 3 |
| TS4-8 | 首次 update（无 old） | 直接写入；exit 0 |
| **TS4-9 ★v2** | `eval_gate_ci.sh` 模拟 git diff 含 `enhanced_retriever.py` | 检测出 → `--strict` 自动加入；记录 echo |
| **TS4-10 ★v2** | `eval_gate_ci.sh` git diff 仅 `README.md` | 不加 strict；走默认 gate |

### TG-S5 · episodic → semantic（WI-S2.4，v2 修订）

`tests/test_memory_episodic_to_semantic_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| TS5-1 | `episodic_to_semantic=true` + summarize_old_sessions 完成 1 session | summary message 落表；background_tasks set 增加一条 |
| TS5-2 | 等 task 完成（gather） | facts 表新增 ≥ 1 条 `category='episodic_summary'`；source_msg_id=summary_msg_id |
| TS5-3 | `episodic_to_semantic=false` | summarizer 正常；facts 表不新增 episodic_summary 行 |
| TS5-4 | `process_message(role="system", source="user_message")` | 返 []（白名单不允许）|
| TS5-5 | 同上但 `source="summarizer"` | 走抽取链路；category override 为 episodic_summary |
| TS5-6 | summary 抽出与 user 消息 fact 矛盾 | cross_key_merge=true 时 → 老 user fact 标 superseded |
| TS5-7 | LLM 对 summary 抽取 fail | summarizer 主流程不受影响；warn log |
| **TS5-8 ★v2** | task add_done_callback 触发 | 完成后 background_tasks set 自动 discard |
| **TS5-9 ★v2** | shutdown 时 5 个 pending tasks | gather 等待完成；无 "Task was destroyed" warning |
| **TS5-10 ★v2** | `VALID_CATEGORIES` 是否含 `episodic_summary` | 单测断言；防 E5 倒退 |

### TG-S6 · 全套回归 + flag 矩阵

`tests/test_memory_stage2_smoke.py`

| # | 用例 | 断言 |
|---|---|---|
| TS6-1 | Stage 2 全 flag off boot | main.py 正常起；行为与 Stage 1 字节级一致 |
| TS6-2 | 4 个 Stage 2 flag 全开 boot | 各组件就绪；boot 日志完整 |
| TS6-3 | flag 独立切换抽样 6 组 | 各组合 backend pytest 0 回归 |
| **TS6-4 ★v2** | `availability['superseded_by']=False` 时启动 | `cross_key_merge` flag 被自动关；warn log |

### TG-S7 · eval 门控（端到端，跑 strict）

| # | 用例 | 断言 |
|---|---|---|
| TS7-1 | Stage 2 全 flag 开 + `--strict` | hit@5 > baseline；exit 0 |
| TS7-2 | Stage 2 全 flag 开 + 默认 | hit@5 不回归；token < +30%；exit 0 |
| TS7-3 | entity 路 weight 设 0.3 跑 | 记录结果；不强制断言（探索性 ablation）|

### TG-S8 · 全套回归

| 套件 | 通过线 |
|---|---|
| backend pytest（Stage 2 flag 全关） | **0 回归**（含 Stage 1 全 1799+ 用例 + 新 TG-S0-S7）|
| backend pytest（Stage 2 flag 全开） | 全绿 |
| frontend vitest（MemoryPanel facts view + 🗑 + undo） | 0 回归 + 新增 ≥ 5 用例 |
| frontend tsc | 0 error |
| Rust cargo（无改动） | 0 回归 |
| eval_gate（默认 + strict） | PASS |
| eval_gate_ci.sh git diff 检测 | TS4-9/10 通过 |

### B-末 · 完成定义

每个 WI = 对应 TG 用例全绿 + TG-S8 回归 0 倒退 + flag 可独立开关 + PRD §7 达成。**集成测试（TG-S1~S5）绿才算完成。**

---

## C. 实施顺序（v2 拆 M1a/M1b）

```
M0  schema migrator + ALTER 失败兜底 (TS0-1~6) ──┐
                                                  │
M1a 后端                                          │
  ├ cross-key merge (TS1-1~14)              ─────┤
  ├ memory_forget 工具 (TS2-1~13)           ─────┤
  ├ facts_conflict_cleanup 脚本             ─────┤
  └ ws 路由 + bind                           ─────┤
                                                  │
M1b UI                                            │
  └ MemoryPanel facts view + 🗑 + undo ──────────┤  ◄── 依赖 M1a ws
                                                  │
M2  eval_gate strict + CI (TS4-1~10) ────────────┤  ◄── 独立并行
                                                  │
M3  episodic→semantic (TS5-1~10) ────────────────┤  ◄── 依赖 M1a cross-key
                                                  │
M4  entity 索引 (TS3-1~10) ──────────────────────┤  ◄── 独立并行
                                                  │
M5  eval --strict + MR-4 GUI (主 checkout) ──────┤  ◄── 全 merge 后
                                                  │
M6  全套回归 (TS6/8) + 文档化 ────────────────────┘
```

并行：M1a / M2 / M4 三路。

---

## D. 实测结果（动工后回填）

### 自动化测试

| 测试组 | 文件 | 结果 |
|---|---|---|
| TG-S0 schema migrator | test_memory_schema_v2_migrator.py | ⬜ 未开始 |
| TG-S1 cross-key 矛盾 | test_memory_cross_key_conflict_integration.py | ⬜ |
| TG-S2 memory_forget 工具 | test_memory_forget_tool_integration.py | ⬜ |
| TG-S3 entity 索引 | test_memory_entity_path_integration.py | ⬜ |
| TG-S4 eval gate strict + CI | test_eval_gate_strict.py | ⬜ |
| TG-S5 episodic→semantic | test_memory_episodic_to_semantic_integration.py | ⬜ |
| TG-S6 stage2 smoke | test_memory_stage2_smoke.py | ⬜ |
| TG-S7 eval strict 端到端 | （手工） | ⬜ |

### TG-S8 全套回归

- backend pytest（flag 全关）: ⬜
- backend pytest（flag 全开）: ⬜
- frontend vitest: ⬜
- frontend tsc: ⬜
- eval_gate（默认 + strict）: ⬜
- eval_gate_ci.sh: ⬜

### 接入确认（DoD：wire 进 main.py）

预期 boot 日志（flag 全开）：
- `v2_migrator: ALTER TABLE facts ADD superseded_by OK`
- `v2_migrator: ALTER TABLE facts ADD forgotten_at OK`
- `p4_cross_key_merge_enabled`
- `p4_entity_path_enabled`
- `p4_episodic_to_semantic_enabled`
- `p4_memory_forget_tool_bound`
