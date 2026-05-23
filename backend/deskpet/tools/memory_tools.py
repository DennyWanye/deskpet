"""Stage 2 / WI-S2.1a — ``memory_forget`` tool.

设计要点（PRD/TDD D5 v2 / D6 v2）：

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
    """Defensive parse of ``{"ids": [...]}``."""
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
