# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Stage 2 / WI-S2.1a — ``memory_forget`` tool + WI-T3.1 v3 memory_* 真实现.

设计要点（PRD/TDD D5 v2 / D6 v2 + v3 §A8 schema migration）：

* 顶层 ``registry.register(...)`` —— ``tools/__init__.py:_discover_and_load``
  在 import 时跑，没有 ``@register_tool`` 装饰器、没有 ``_ctx`` 注入。
* ``bind(...)`` 模块级 setter —— main.py lifespan 在 FactsStore / Embedder /
  LLM 构造完毕后调一次注入依赖；import 时 module-level handles 为 None，
  registry discovery 跑得动。
* 工具自身规则拦截：query 长度 < 6 字 → skipped；命中 fact > 5 → skipped；
  单次最多 forget 3 条。**不靠** LLM 二次确认抵御提示注入（D-RISK-5）。
* ``dangerous=True`` + ``permission_category="write_file"`` 让 registry 触发
  UI 确认 dialog。
* 删的不是真 DELETE，是 ``is_active=0 + forgotten_at=now()``，5 秒 undo
  可恢复（FactsStore.restore_from_undo）。

WI-T3.1 v3 schema migration（本文件 append）：
* ``memory_write`` — 旧 schema (text/tier/salience) → facts.upsert
  (subject="user", key="memory_<ts>", value=text, category=_TIER_TO_CATEGORY[tier])
* ``memory_read``  — (memory_id:int) → facts.get_by_id
* ``memory_search`` — (query, top_k) → facts.search（LIKE 兜底，未来 EnhancedRetriever）
* 字典序：'m' < 's' → memory_tools.py 先注册，stubs.py 守卫模式跳过同名
* 翻译表 PRD v3 D17：l1 短期/快衰减→event, l2 中期→project, l3 长期/慢衰减→preference
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from deskpet.tools.registry import registry

log = logging.getLogger(__name__)


# Module-level handles — bind() in main.py lifespan 注入。
_facts_store: Any = None
_embedder: Any = None
_llm_call: Optional[Callable[[str], Awaitable[str]]] = None
_enable_natural_language: bool = False


def bind(
    *,
    facts_store: Any,
    embedder: Any,
    llm_call: Optional[Callable[[str], Awaitable[str]]] = None,
    enable_natural_language: bool = False,
) -> None:
    """Inject runtime dependencies. Idempotent.

    ``enable_natural_language=False`` → 自然语言模式（query=...）禁用，
    走 _forget_by_query 直接返 skipped；强制只走 fact_id 模式。
    """
    global _facts_store, _embedder, _llm_call, _enable_natural_language
    _facts_store = facts_store
    _embedder = embedder
    _llm_call = llm_call
    _enable_natural_language = bool(enable_natural_language)
    log.info(
        "memory_tools.bind: facts_store=%s embedder=%s llm=%s NL=%s",
        type(facts_store).__name__,
        type(embedder).__name__ if embedder is not None else None,
        _llm_call is not None,
        _enable_natural_language,
    )


def is_bound() -> bool:
    return _facts_store is not None


# OpenAI function-calling schema.
_SCHEMA: dict[str, Any] = {
    "name": "memory_forget",
    "description": (
        "Forget a previously-remembered fact. Use ONLY when the user "
        "explicitly asks to forget something. Prefer fact_id when known."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fact_id": {
                "type": "integer",
                "description": (
                    "Exact fact ID to forget (preferred when known via "
                    "an earlier memory_facts_list call)"
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "Natural-language description of what to forget "
                    "(requires admin to enable)"
                ),
            },
        },
    },
}


_CONFIRM_PROMPT = """\
You are confirming which fact(s) to forget given a user query.

User wants to forget: {query!r}

Candidate facts (id, key, value):
{candidates}

Output ONLY a JSON object with a single field:
  {{"ids": [<int>, <int>, ...]}}

Include ONLY ids that **clearly match** the user's intent. Omit anything
ambiguous or unrelated. If nothing matches, output {{"ids": []}}.
"""


async def _handle(args: dict, task_id: str) -> str:  # noqa: ARG001
    """Registry handler protocol: (args, task_id) -> JSON str."""
    if _facts_store is None:
        return json.dumps({
            "status": "error",
            "reason": "memory_forget tool not bound (main.py lifespan issue)",
        })

    fact_id = args.get("fact_id")
    query = args.get("query")

    if fact_id is not None:
        try:
            fact_id_int = int(fact_id)
        except (TypeError, ValueError):
            return json.dumps({
                "status": "error",
                "reason": f"fact_id must be int, got {fact_id!r}",
            })
        return await _forget_by_id(fact_id_int)

    if query:
        return await _forget_by_query(str(query))

    return json.dumps({
        "status": "error",
        "reason": "需 fact_id 或 query 之一",
    })


async def _forget_by_id(fact_id: int) -> str:
    """fact_id 模式 — 永远开放（最直接、最少歧义的方式）。"""
    op_id = uuid.uuid4().hex
    try:
        await _facts_store.mark_forgotten(
            fact_id, op_id=op_id, ts=time.time(),
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error", "reason": str(exc)})
    return json.dumps({
        "status": "ok",
        "op_id": op_id,
        "forgotten_ids": [fact_id],
    })


async def _forget_by_query(query: str) -> str:
    """自然语言模式 — D5 v2 工具自身规则拦截 + 默认禁用。"""
    if not _enable_natural_language:
        return json.dumps({
            "status": "skipped",
            "reason": (
                "natural-language forget disabled "
                "(set [memory.v2.forget] enable_natural_language=true)"
            ),
        })

    # 规则 1：query 长度
    if len(query.strip()) < 6:
        return json.dumps({
            "status": "skipped",
            "reason": "query 过短（< 6 字），拒绝执行",
        })

    if _embedder is None:
        return json.dumps({
            "status": "error",
            "reason": "embedder not bound; cannot resolve query → fact",
        })

    # 1. 向量召回 top 5
    try:
        qvec = await _embedder.encode([query])
        if qvec is None or len(qvec) == 0:
            return json.dumps({"status": "not_found"})
        candidates = await _facts_store.vector_search(qvec[0], limit=5)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error", "reason": str(exc)})

    if not candidates:
        return json.dumps({"status": "not_found"})

    # 规则 2：命中数过多 → 拒（防"忘记所有我说过的话"）
    if len(candidates) > 5:
        return json.dumps({
            "status": "skipped",
            "reason": "query 过宽（命中 > 5 fact），拒绝执行",
        })

    # 2. LLM 二次确认
    if _llm_call is None:
        return json.dumps({
            "status": "error",
            "reason": "llm_call not bound; cannot confirm",
        })
    try:
        confirmed_ids = await _llm_confirm_forget(query, candidates)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error", "reason": str(exc)})

    # 规则 3：单次最多 3 条
    confirmed_ids = confirmed_ids[:3]
    if not confirmed_ids:
        return json.dumps({
            "status": "skipped",
            "reason": "LLM 未确认任何 fact 要遗忘",
            "candidates": [int(c["id"]) for c in candidates],
        })

    # 3. 标 forgotten + 共享 op_id
    op_id = uuid.uuid4().hex
    now = time.time()
    for fid in confirmed_ids:
        try:
            await _facts_store.mark_forgotten(int(fid), op_id=op_id, ts=now)
        except Exception as exc:  # noqa: BLE001
            log.warning("mark_forgotten(%s) failed: %s", fid, exc)

    return json.dumps({
        "status": "ok",
        "op_id": op_id,
        "forgotten_ids": [int(i) for i in confirmed_ids],
    })


async def _llm_confirm_forget(
    query: str, candidates: list[dict[str, Any]],
) -> list[int]:
    """LLM 看 query + 候选 facts，返回应被遗忘的 ID 列表。"""
    cand_str = "\n".join(
        f"  - id={int(c['id'])} key={c.get('key')!r} "
        f"value={str(c.get('value',''))[:80]!r}"
        for c in candidates
    )
    prompt = _CONFIRM_PROMPT.format(query=query, candidates=cand_str)
    raw = await _llm_call(prompt)  # type: ignore[misc]
    return _parse_confirm_response(raw)


def _parse_confirm_response(raw: str) -> list[int]:
    """Defensive parse of ``{"ids": [...]}``.

    Stage 2 真测试 round 2 bug fix：剥 ``<think>...</think>`` reasoning
    块（deepseek-v4 / claude thinking 等模型常带）。
    """
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    import re
    for pat in (r"<think>.*?</think>", r"<thinking>.*?</thinking>",
                r"<reasoning>.*?</reasoning>"):
        text = re.sub(pat, "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    lb, rb = text.find("{"), text.rfind("}")
    if not (0 <= lb < rb):
        return []
    try:
        obj = json.loads(text[lb:rb + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    raw_ids = obj.get("ids")
    if not isinstance(raw_ids, list):
        return []
    out: list[int] = []
    for x in raw_ids:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------
# 模块顶层 register — pkgutil discovery 触发时执行
# ---------------------------------------------------------------------
registry.register(
    name="memory_forget",
    toolset="memory",
    schema=_SCHEMA,
    handler=_handle,
    permission_category="write_file",
    dangerous=True,
    source="builtin",
)


# =====================================================================
# WI-T3.1 v3 — memory_write / memory_read / memory_search schema
# migration 真实现（接 facts.py，stubs.py 守卫模式跳过同名 stub）
# =====================================================================

# PRD v3 D17 翻译表：tier (l1/l2/l3/auto) → facts.category
# l1 短期/最快衰减 ↔ event；l2 中期 ↔ project；l3 长期/最慢衰减 ↔ preference
# auto 走 preference（最保守 — 长期保留，让用户主动 forget）
_TIER_TO_CATEGORY: dict[str, str] = {
    "l1": "event",
    "l2": "project",
    "l3": "preference",
    "auto": "preference",
}


_MEMORY_WRITE_SCHEMA: dict[str, Any] = {
    "name": "memory_write",
    "description": (
        "Persist a fact / observation to DeskPet long-term memory. "
        "Use when the user shares a preference, name, project detail, "
        "or any fact worth recalling later."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Content to remember (free-form).",
            },
            "tier": {
                "type": "string",
                "enum": ["l1", "l2", "l3", "auto"],
                "default": "auto",
                "description": (
                    "l1=short-term/event, l2=mid-term/project, "
                    "l3=long-term/preference. Default auto = preference."
                ),
            },
            "salience": {
                "type": "number",
                "description": "0.0-1.0 importance. Default 0.5.",
                "default": 0.5,
            },
        },
        "required": ["text"],
    },
}

_MEMORY_READ_SCHEMA: dict[str, Any] = {
    "name": "memory_read",
    "description": "Read a specific memory record by integer id.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "Exact fact ID (returned by memory_write or memory_search).",
            },
        },
        "required": ["memory_id"],
    },
}

_MEMORY_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "memory_search",
    "description": (
        "Search DeskPet memory by free-text query. Returns up to top_k "
        "matching facts (LIKE-based; future EnhancedRetriever upgrade)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}


async def _memory_write_handle(args: dict, task_id: str) -> str:  # noqa: ARG001
    """memory_write handler — schema migration 翻译到 facts.upsert."""
    if _facts_store is None:
        return json.dumps({
            "ok": False,
            "error": "memory_write not bound (main.py lifespan issue)",
        }, ensure_ascii=False)
    text = args.get("text")
    if not text or not isinstance(text, str):
        return json.dumps({
            "ok": False,
            "error": "missing required field 'text' (non-empty string)",
        }, ensure_ascii=False)
    tier = str(args.get("tier") or "auto").lower()
    category = _TIER_TO_CATEGORY.get(tier, "preference")
    try:
        salience = float(args.get("salience", 0.5))
    except (TypeError, ValueError):
        salience = 0.5
    # confidence ≈ salience（旧 schema 没有 confidence 字段，复用 salience 语义）
    confidence = max(0.0, min(1.0, salience))
    # key 用时间戳保证唯一（旧 schema 没传 key 字段 → 自动生成）
    key = f"memory_{int(time.time() * 1000)}"
    try:
        new_id = await _facts_store.upsert(
            category=category,
            subject="user",
            key=key,
            value=text.strip(),
            confidence=confidence,
            source_msg_id=None,
            evidence="memory_write tool call",
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "ok": False,
            "error": f"facts.upsert failed: {exc}",
        }, ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "memory_id": int(new_id),
        "category": category,
        "tier": tier,
    }, ensure_ascii=False)


async def _memory_read_handle(args: dict, task_id: str) -> str:  # noqa: ARG001
    """memory_read handler — by id 真实现（R-MISS-9 facts.get_by_id 新加）."""
    if _facts_store is None:
        return json.dumps({
            "ok": False,
            "error": "memory_read not bound",
        }, ensure_ascii=False)
    raw_id = args.get("memory_id")
    try:
        fact_id = int(raw_id)
    except (TypeError, ValueError):
        return json.dumps({
            "ok": False,
            "error": f"memory_id must be integer, got {raw_id!r}",
        }, ensure_ascii=False)
    try:
        row = await _facts_store.get_by_id(fact_id)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "ok": False,
            "error": f"facts.get_by_id failed: {exc}",
        }, ensure_ascii=False)
    if row is None:
        return json.dumps({
            "ok": False,
            "error": f"memory_id={fact_id} not found",
        }, ensure_ascii=False)
    # 隐藏内部字段（embedding 是 bytes blob，序列化噪声）
    safe = {k: v for k, v in row.items() if k not in ("embedding",)}
    return json.dumps({"ok": True, "fact": safe}, ensure_ascii=False, default=str)


async def _memory_search_handle(args: dict, task_id: str) -> str:  # noqa: ARG001
    """memory_search handler — facts.search (LIKE) 兜底真实现."""
    if _facts_store is None:
        return json.dumps({
            "ok": False,
            "error": "memory_search not bound",
        }, ensure_ascii=False)
    query = str(args.get("query") or "").strip()
    if not query:
        return json.dumps({
            "ok": False,
            "error": "query must be non-empty",
        }, ensure_ascii=False)
    try:
        top_k = int(args.get("top_k", 5))
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(20, top_k))
    try:
        rows = await _facts_store.search(query, limit=top_k)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "ok": False,
            "error": f"facts.search failed: {exc}",
        }, ensure_ascii=False)
    safe_rows = [
        {k: v for k, v in r.items() if k not in ("embedding",)}
        for r in (rows or [])
    ]
    return json.dumps({
        "ok": True,
        "results": safe_rows,
        "count": len(safe_rows),
    }, ensure_ascii=False, default=str)


# 注册（memory_tools.py 字典序先于 stubs.py，故 stubs.py 守卫模式跳过同名 stub）
registry.register(
    name="memory_write",
    toolset="memory",
    schema=_MEMORY_WRITE_SCHEMA,
    handler=_memory_write_handle,
    permission_category="write_file",
    source="builtin",
)
registry.register(
    name="memory_read",
    toolset="memory",
    schema=_MEMORY_READ_SCHEMA,
    handler=_memory_read_handle,
    permission_category="read_file",
    source="builtin",
)
registry.register(
    name="memory_search",
    toolset="memory",
    schema=_MEMORY_SEARCH_SCHEMA,
    handler=_memory_search_handle,
    permission_category="read_file",
    source="builtin",
)
