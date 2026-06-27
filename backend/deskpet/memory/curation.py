# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-OH-4 — memory self-curation nudge (agent 主动判断该不该记).

Background
----------
DeskPet 已有两条被动记忆通路：

* :mod:`deskpet.memory.reflection` —— ``ReflectionWorker`` 每 6h batch
  总结昨天，写一条 ``category='reflection'`` 的元认知笔记。
* :mod:`deskpet.memory.facts` —— ``FactExtractor`` 在写入端按规则 / LLM
  抽取结构化事实。

两者**都是被动批处理**，没有「agent 回看刚发生的对话、主动判断有什么值得
长期记住」这一环。本模块补的就是这个缺口（对标 hermes「周期性自省」+
openhuman「self-curation」）：周期性给 agent 一个内部 prompt
「回看最近 N 轮，有什么值得长期记住？」，由 LLM 自决写不写、写什么。

* 输入：最近若干轮对话（``recent_turns``）。
* 输出：``list[CurationDecision]`` —— 每条带 ``should_remember`` 布尔；
  ``True`` 的由调用方走 ``FactsStore.upsert()`` 落库。
* JSON 解析复用 :mod:`deskpet.agent.reflection` / ``goal_checker`` 同源的
  3 级 fallback（直 parse → fenced block → 首个 ``{...}``），容忍真实
  LLM 输出的格式抖动（裸 JSON / 代码块 / 夹带散文）。

⚠️ 注意区分：``deskpet.agent.reflection`` 的 ``StructuredReflection`` 是
**纠错反思**（错了重规划），与本模块的「记忆 self-curation」是两件事。
OH-4 落在 ``memory/`` 而非 ``agent/``。

Failure isolation
-----------------
``nudge`` 永不抛——LLM 调用失败 / JSON 畸形 / 字段缺失一律降级成「这次不记」
（返回空 list），绝不影响主回合。调用方（agent_loop）以 fire-and-forget 的
方式异步触发，不挡用户回合（照 ``vector_worker`` 异步模式）。

Backward-compat
---------------
本模块纯加法。``memory.v2.curation_nudge`` 默认 **False** →
``MemoryCurator`` 不构造、agent_loop 不调用 ``nudge``、不写 facts →
字节级一致。所有新行为只在 flag ON 路径执行。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

log = logging.getLogger(__name__)


_LLMCall = Callable[[str], Awaitable[str]]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────────────────

_CURATION_PROMPT = """\
You are the pet's memory curator. Read the recent conversation turns below
(one block per turn, most recent last), then decide — on your own — whether
anything is worth remembering for the long term.

Be selective. Only surface durable, reusable facts about the user or the
relationship: stable preferences, recurring projects, important personal
context, decisions, constraints. Skip small talk, one-off chatter, and things
already obviously transient. It is completely fine to remember NOTHING.

Reply with ONLY a JSON object of this exact shape:

{{
  "decisions": [
    {{
      "should_remember": true,
      "category": "preference" | "fact" | "goal" | "constraint" | "context",
      "key": "short_snake_case_key",
      "value": "the thing to remember, one sentence, same language as the user",
      "reason": "why this is worth keeping long-term"
    }}
  ]
}}

If nothing is worth remembering, reply: {{"decisions": []}}
Do not invent facts that were not stated. Do not include any prose outside
the JSON object.

RECENT TURNS:
{turns}

JSON:"""


# WI-CC-5: prompt variant that additionally permits a ``learning`` category
# (procedural / reusable how-to knowledge — e.g. "user wanted a dark theme for
# the last PPT", "the steps to generate the weekly report"). Only used when the
# ``auto_learnings`` flag is ON. BYTE-IDENTICAL to _CURATION_PROMPT except the
# category enum gains "learning" plus one guidance sentence.
_CURATION_PROMPT_WITH_LEARNINGS = """\
You are the pet's memory curator. Read the recent conversation turns below
(one block per turn, most recent last), then decide — on your own — whether
anything is worth remembering for the long term.

Be selective. Only surface durable, reusable facts about the user or the
relationship: stable preferences, recurring projects, important personal
context, decisions, constraints. You may ALSO record "learning" items —
reusable procedural knowledge or how-the-user-likes-things, e.g. the steps to
do a recurring task, or a styling preference observed while helping ("user
preferred a dark theme for the last deck"). Skip small talk, one-off chatter,
and things already obviously transient. It is completely fine to remember
NOTHING.

Reply with ONLY a JSON object of this exact shape:

{{
  "decisions": [
    {{
      "should_remember": true,
      "category": "preference" | "fact" | "goal" | "constraint" | "context" | "learning",
      "key": "short_snake_case_key",
      "value": "the thing to remember, one sentence, same language as the user",
      "reason": "why this is worth keeping long-term"
    }}
  ]
}}

If nothing is worth remembering, reply: {{"decisions": []}}
Do not invent facts that were not stated. Do not include any prose outside
the JSON object.

RECENT TURNS:
{turns}

JSON:"""


# ──────────────────────────────────────────────────────────────────────────────
# Decision dataclass
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class CurationDecision:
    """One self-curation verdict from the LLM.

    ``should_remember=False`` decisions are kept (for observability / tests)
    but the caller only persists the ``True`` ones via ``FactsStore.upsert``.
    """

    should_remember: bool
    category: str
    key: str
    value: str
    reason: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# JSON extraction — 3-level fallback (same as goal_checker / agent.reflection)
# ──────────────────────────────────────────────────────────────────────────────

_FENCED_JSON_RX = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
# Greedy outer braces so a top-level object containing nested arrays/objects
# (our {"decisions": [ {...}, {...} ]}) is captured whole, not truncated at the
# first inner "}".
_BARE_JSON_RX = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> Optional[dict]:  # type: ignore[type-arg]
    """3 级 fallback 提 JSON 对象（与 goal_checker / agent.reflection 同源）。

    1. 直 ``json.loads(raw.strip())``
    2. fenced ```json``` 代码块
    3. 首个 ``{ ... }`` 子串（贪婪外括号，容纳嵌套数组）

    返 ``None`` 表示无法 parse。
    """
    s = (raw or "").strip()

    # 1. 直 parse
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. fenced block
    m = _FENCED_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. 首个 {...}
    m = _BARE_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _coerce_decisions(obj: dict) -> list[CurationDecision]:
    """Map a parsed JSON dict into ``CurationDecision`` rows.

    Tolerant: missing / wrong-typed fields get safe defaults; rows without a
    usable ``key`` or ``value`` (when ``should_remember`` is True) are dropped
    so the caller never writes an empty fact.
    """
    raw_list = obj.get("decisions")
    if not isinstance(raw_list, list):
        return []
    out: list[CurationDecision] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        should = bool(item.get("should_remember", False))
        category = str(item.get("category") or "context").strip() or "context"
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        reason = str(item.get("reason") or "").strip()
        # A "remember" verdict with no key/value is unusable — skip it rather
        # than write an empty fact row.
        if should and (not key or not value):
            continue
        out.append(
            CurationDecision(
                should_remember=should,
                category=category,
                key=key,
                value=value,
                reason=reason,
            )
        )
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Curator
# ──────────────────────────────────────────────────────────────────────────────


class MemoryCurator:
    """Periodic memory self-curation nudge.

    The agent loop calls :meth:`nudge` (fire-and-forget) at a frequency gate;
    each call asks the LLM what — if anything — is worth remembering, then the
    ``True`` decisions are persisted via ``FactsStore.upsert``.

    Failure isolation: ``nudge`` swallows all errors and returns ``[]`` so the
    chat path is never affected.
    """

    def __init__(
        self,
        facts_store: Any,
        llm_call: _LLMCall,
        *,
        max_turns: int = 12,
        subject: str = "user",
        default_confidence: float = 0.55,
        allow_learnings: bool = False,
    ) -> None:
        self._facts = facts_store
        self._llm = llm_call
        self._max_turns = int(max_turns)
        self._subject = subject
        self._default_confidence = float(default_confidence)
        # WI-CC-5: when True, use the learnings-aware prompt AND allow the
        # ``learning`` category through. Default False = byte-level BC: the
        # base prompt never mentions ``learning`` and any stray ``learning``
        # decision (e.g. hallucinated) is filtered out before persist/return.
        # Set by main.py from cfg.memory.v2.auto_learnings.
        self._allow_learnings = bool(allow_learnings)
        # WI-OH-4 fix (2026-06-23, real-E2E caught): the per-session turn
        # counter MUST live here, on the curator singleton — NOT on the
        # _AgentLoop. main.py rebuilds a fresh _AgentLoop per chat turn
        # (build_agent inside _run_chat), so an in-loop counter reset every
        # turn and ``count % every_n`` never reached 0 in production. The unit
        # test passed only because it reused ONE loop instance across turns.
        # The curator is registered once in service_context → this dict
        # persists across turns, so the cadence gate actually advances.
        self._turn_counts: dict[str, int] = {}

    def bump_turn(self, session_id: str) -> int:
        """Increment + return the persistent per-session terminal-turn count.

        Lives on the curator (a service_context singleton) so it survives the
        per-turn ``_AgentLoop`` rebuild. The frequency *threshold* stays on the
        caller (``agent_loop``, from ``cfg.memory.v2.curation_nudge_every_n_turns``);
        this only owns the monotonic count so the gate can advance across turns.
        """
        n = self._turn_counts.get(session_id, 0) + 1
        self._turn_counts[session_id] = n
        return n

    async def nudge(
        self,
        recent_turns: Sequence[dict[str, Any]],
        *,
        llm: Optional[_LLMCall] = None,
        persist: bool = True,
    ) -> list[CurationDecision]:
        """Ask the LLM what's worth remembering from ``recent_turns``.

        Parameters
        ----------
        recent_turns:
            List of ``{"role": ..., "content": ...}`` dicts (any extra keys are
            ignored). Most-recent-last ordering is preferred but not required.
        llm:
            Optional override for the call-time LLM (else the ctor's). Lets
            callers thread a live provider without rebuilding the curator.
        persist:
            When True (default), ``should_remember`` decisions are written via
            ``FactsStore.upsert``. Set False to dry-run (tests / shadow).

        Returns
        -------
        list[CurationDecision]
            All parsed decisions (both remember / skip). Empty on any failure.
        """
        call = llm or self._llm
        if call is None:
            return []

        turns = [t for t in (recent_turns or []) if isinstance(t, dict)]
        # Keep only chat turns, cap to the most recent N.
        turns = [
            t for t in turns
            if str(t.get("role")) in ("user", "assistant")
        ]
        if not turns:
            return []
        turns = turns[-self._max_turns:]

        rendered = "\n\n".join(
            f"[{t.get('role')}] {str(t.get('content') or '')[:240]}"
            for t in turns
        )
        # WI-CC-5: pick the learnings-aware prompt only when the flag is ON.
        # flag OFF → base prompt (byte-identical to OH-4) → never mentions
        # the ``learning`` category.
        prompt_tmpl = (
            _CURATION_PROMPT_WITH_LEARNINGS
            if self._allow_learnings
            else _CURATION_PROMPT
        )
        prompt = prompt_tmpl.format(turns=rendered)

        try:
            raw = await call(prompt)
        except Exception as exc:  # noqa: BLE001 — safe-fail, never block turn
            log.debug("curation: LLM call failed: %s", exc)
            return []

        obj = _extract_json(raw or "")
        if obj is None:
            log.debug(
                "curation: could not extract JSON from text (len=%d)",
                len(raw or ""),
            )
            return []

        decisions = _coerce_decisions(obj)
        # WI-CC-5 BC guard: when auto_learnings is OFF, drop any ``learning``
        # decision the LLM may have emitted (the base prompt never asks for it,
        # but defense-in-depth keeps OFF byte-level identical — no learning row
        # is ever persisted or returned).
        if not self._allow_learnings:
            decisions = [d for d in decisions if d.category != "learning"]
        if not decisions:
            return []

        if persist:
            await self._persist(decisions)
        return decisions

    async def _persist(self, decisions: list[CurationDecision]) -> None:
        """Write the ``should_remember`` decisions as facts. Best-effort.

        Each write is isolated: one bad row never blocks the rest, and a
        store-level error never escapes (the chat path must not see it).
        """
        for d in decisions:
            if not d.should_remember:
                continue
            try:
                await self._facts.upsert(
                    category=d.category,
                    subject=self._subject,
                    key=d.key,
                    value=d.value,
                    confidence=self._default_confidence,
                    source_msg_id=None,
                    evidence=f"curation_nudge: {d.reason}"[:240],
                )
            except Exception as exc:  # noqa: BLE001 — isolate per-row failure
                log.debug(
                    "curation: persist failed (key=%s): %s", d.key, exc
                )


__all__ = [
    "MemoryCurator",
    "CurationDecision",
]
