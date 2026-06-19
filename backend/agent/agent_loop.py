# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeskPet agent loop (P4-S6 §11 skeleton).

One pass = one LLM turn + optional tool dispatch round. Loop until
`stop_reason != "tool_use"` or `max_iterations` hit, whichever comes
first.

Yielded events (AgentEvent) — caller decides what to do with each:

    assistant_message   assistant text chunk (content from the model)
    tool_call           a tool the model asked to invoke
    tool_result         output of a dispatched tool (JSON string)
    final               last turn; final response + aggregated stats
    error               terminal failure (budget exceeded, max_iter, LLM error)

The loop does NOT talk to the wire itself. Callers wire:
    llm_registry: must expose `async chat_with_fallback(...)` → ChatResponse
    tool_registry: must expose
        - `schemas(enabled_toolsets=None)` → list[dict]   (OpenAI format)
        - `async dispatch(name, args, task_id)` → str     (JSON string; see §5 contract)

Tool dispatch is concurrent (asyncio.gather) when the model requests
multiple tool_calls in one turn (spec §11.9). Each tool result is fed
back as a `role=tool` message and the loop iterates.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol, Union

from agent import errors as agent_errors
from agent.task_id import new_task_id
from agent.context_manager import ContextManager
from agent.termination import GateConfig, TerminationGate, TerminationReason
from llm.budget import DailyBudget
from llm.errors import LLMBudgetExceededError, LLMProviderError
from llm.types import ChatResponse, ToolCall

logger = logging.getLogger("deskpet.agent.loop")


# ───────────────────── P5-S2 Phase 3 constants ─────────────────────


# Repeat-detection threshold: if the LLM emits the SAME (tool_name,
# args_hash) signature ``>= _REPEAT_THRESHOLD`` times in a row, the
# loop short-circuits the dispatch and injects a system message asking
# the LLM to look at the prior tool_result hint or change tactics.
# Keep at 3 to match the OpenSpec proposal default.
_REPEAT_THRESHOLD = 3

# ───────────────────── WI-1.3 goal-anchor constants ─────────────────────
#
# Every ``_GOAL_ANCHOR_EVERY`` iterations, when there is an active goal,
# inject a brief ``[目标锚定]`` system message into working_messages so the
# model is reminded of the original objective and doesn't drift into
# intermediate side-tasks.  This is ORTHOGONAL to the goal_checker nudge:
#   anchor = "don't drift away" (preventive, periodic)
#   nudge  = "you haven't finished yet" (reactive, on end_turn)
_GOAL_ANCHOR_EVERY = 5

# Module-level template so tests can pin it. Filled with ``.format(
# name=..., count=...)`` at injection time.
_REPEAT_NUDGE_MSG = (
    "你刚连续 {count} 次用同一个工具 {name} 调用同样的参数，"
    "结果不会变。看一下 tool_result 的 hint 字段，"
    "或者换个思路 / 换个工具。"
)

# ───────────────────── P5-S2 Phase 7: in-loop self-check ─────────────
#
# Every ``_SELFCHECK_EVERY`` iterations, BEFORE the LLM call, we inject
# a system message. The message ESCALATES based on iteration count:
#
#   tier 1 (iter=10): gentle reflection — "consider committing to a finish"
#   tier 2 (iter=20): firm — "you MUST justify each remaining tool call"
#   tier 3 (iter=30+): forced — "your NEXT response MUST be stop_reason=end_turn"
#
# Empirical: tier 1 alone wasn't enough — the LLM sees "considered" and
# keeps going. Forced tier at 30 reliably breaks "infinite content
# generation" patterns where each iteration writes another section,
# fix, refactor, etc.
#
# P6 Phase 6: the legacy soft tool-budget injection (``_TOOL_BUDGET_HARD_MSG``
# + the >= _TOOL_BUDGET_HARD branch) was removed — the TerminationGate
# now enforces tool_budget_hard with a HARD break that emits
# ErrorEvent(error_tool_budget). Only the iteration-based selfcheck
# tiers remain as soft nudges.

_SELFCHECK_EVERY = 10  # base interval — tier 1 fires here
_SELFCHECK_TIER2_AT = 20  # iter >= this → escalate
_SELFCHECK_TIER3_AT = 30  # iter >= this → forced stop

_SELFCHECK_TIER1 = (
    "[self-check 第 1 级 / 第 {iter}/{max_iter} 轮 — 已用 {tools_used} 次工具调用]\n"
    "你已经跑了 {iter} 轮 ReAct 循环。在下一轮回复中先反思 3 行：\n"
    "1. 用户原问题的核心需求是什么？\n"
    "2. 你已经完成了哪些核心成果（具体改了什么文件、跑了什么命令、产出了什么结果）？\n"
    "3. 剩余工作是否真的必须？若不必须立即 stop_reason=end_turn 把可改进点写进 todo_write。\n"
    "记住：用户期望答完原问题，不是追求完美。剩 {budget} 轮预算。"
)

_SELFCHECK_TIER2 = (
    "[self-check 第 2 级 — 你已经跑了 {iter} 轮，使用了 {tools_used} 次工具调用]\n"
    "**警告**：你已经超过了正常任务的迭代量。不再允许『探索式』的工具调用。\n"
    "下一轮你**必须**做以下其中之一：\n"
    "  (A) 立即 stop_reason=end_turn，给用户一段总结：你做完了什么、剩余什么留给下次。\n"
    "  (B) 如果**真的**必须继续，先用一句话写清楚『为什么再来一轮就能收尾』，"
    "然后这一轮必须是**最后一轮**——只调一个工具，下下轮一定 end_turn。\n"
    "如果你又想『再写一节/再修一处/再扫一遍』——选 (A)，把它写进 todo 留给下次。"
)

_SELFCHECK_TIER3 = (
    "[STRICT STOP — 第 {iter}/{max_iter} 轮，已用 {tools_used} 次工具调用]\n"
    "你已经达到迭代预算的临界点。**禁止任何新的 tool_call**。\n"
    "你的下一轮回复**必须**：\n"
    "  1. 不包含任何 tool_calls 字段（即 stop_reason=end_turn）\n"
    "  2. 用 markdown 段落直接给用户写：\n"
    "     - 已完成的工作清单（带文件路径、改动概要）\n"
    "     - 已知未完成 / 待改进事项（用户可以下次让我做）\n"
    "  3. 用 todo_write 之前已经做过的，不要重复写\n"
    "如果你违反这条规则继续 tool_call，下一轮 supervisor 会强制中断你。"
)

def _build_selfcheck_message(iteration: int, max_iter: int, tools_used: int) -> str:
    """Choose the right self-check tier based on iteration count.

    Tier escalation handles the "LLM ignores gentle reminder" problem:
    we get progressively harsher until the LLM has no choice but to
    stop_reason=end_turn.
    """
    budget = max(0, max_iter - iteration)
    fmt = {
        "iter": iteration,
        "max_iter": max_iter,
        "budget": budget,
        "tools_used": tools_used,
    }
    if iteration >= _SELFCHECK_TIER3_AT:
        return _SELFCHECK_TIER3.format(**fmt)
    if iteration >= _SELFCHECK_TIER2_AT:
        return _SELFCHECK_TIER2.format(**fmt)
    return _SELFCHECK_TIER1.format(**fmt)


# ───────────────────── P5-S2 Phase 2 helpers ─────────────────────


def _classify_tool_result(result_str: str) -> type[Exception]:
    """Classify a tool result JSON string into an error class.

    Handles two layouts emitted by the dispatch path:
      1. v2 envelope: ``{"ok": false, "result": null, "error": "..."}``
      2. legacy / hand-shaped: ``{"error": "...", "retriable": ...}``
      3. nested — handler put a structured envelope into ``result`` as
         a JSON string: ``{"ok": true, "result": "{\"ok\":false,\"error\":...}"}``

    Always defaults to :class:`agent_errors.TransientToolError` on parse
    failure / unknown shape (conservative — never break a turn over
    something we can't read).
    """
    import json as _json

    try:
        payload = _json.loads(result_str) if isinstance(result_str, str) else result_str
    except (ValueError, TypeError):
        return agent_errors.TransientToolError

    if not isinstance(payload, dict):
        return agent_errors.TransientToolError

    # v2 envelope success path: ok=True with nested result. Inspect the
    # nested structure (handlers can return {"ok": false, ...} inside
    # result even when execute_tool considered the call "successful"
    # at the dispatch layer).
    if payload.get("ok") is True and isinstance(payload.get("result"), str):
        nested_str = payload["result"]
        try:
            nested = _json.loads(nested_str)
        except (ValueError, TypeError):
            nested = None
        if isinstance(nested, dict) and nested.get("ok") is False:
            return agent_errors.classify(nested)
        # Successful, no nested error → no classification needed.
        return agent_errors.TransientToolError

    # Failure envelopes (top-level error string).
    return agent_errors.classify(payload)


def _extract_break_detail(result_str: str, tool_name: str) -> str:
    """Pull a short human-readable detail string out of a tool result
    JSON for the ``ErrorEvent.detail`` field. Prefers a ``hint``
    (Phase 0 sensor feedback) over the raw ``error`` so the message we
    surface is actionable.
    """
    import json as _json

    try:
        payload = _json.loads(result_str) if isinstance(result_str, str) else result_str
    except (ValueError, TypeError):
        return f"tool {tool_name} returned unparseable error"

    if not isinstance(payload, dict):
        return f"tool {tool_name}: {str(payload)[:200]}"

    # Try nested first (envelope.result is a JSON string with the
    # actual handler error including hint).
    nested_str = payload.get("result")
    if isinstance(nested_str, str):
        try:
            nested = _json.loads(nested_str)
        except (ValueError, TypeError):
            nested = None
        if isinstance(nested, dict):
            for key in ("hint", "error", "message"):
                value = nested.get(key)
                if isinstance(value, str) and value:
                    return f"tool {tool_name}: {value}"[:300]

    # Fall back to top-level fields.
    for key in ("hint", "error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return f"tool {tool_name}: {value}"[:300]

    return f"tool {tool_name} permanent error (no detail)"


# ───────────────────── event dataclasses ─────────────────────


@dataclass
class AgentEvent:
    """Base event emitted by the agent loop.

    All fields default so subclasses can freely add non-default fields
    without hitting python's "non-default follows default" check. Each
    subclass overrides `type` in __post_init__ via a class-level default.
    """

    type: str = ""
    task_id: str = ""
    iteration: int = 0


@dataclass
class AssistantMessageEvent(AgentEvent):
    content: str = ""
    # P4-S24: chain-of-thought from thinking-mode models. Persisted to
    # SessionDB so cross-session history rebuilds satisfy the
    # "reasoning_content must be passed back" API constraint. Empty
    # for non-thinking models — safe to ignore.
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "assistant_message"


@dataclass
class AssistantDeltaEvent(AgentEvent):
    """P4-S25 A1: incremental token chunk during streaming.

    Emitted only when the loop runs in streaming mode (caller uses a
    shim with ``chat_with_fallback_stream``). Aggregating these gives
    the same string as the AssistantMessageEvent emitted at the end of
    each iteration, but the user sees text trickle in rather than wait
    for the whole turn.

    ``kind`` distinguishes visible content vs hidden thinking-mode
    reasoning; the frontend can render reasoning in a faded panel and
    content in the main bubble.
    """
    content: str = ""
    kind: str = "content"  # "content" | "reasoning"

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "assistant_delta"


@dataclass
class ToolCallEvent(AgentEvent):
    tool_call: Optional[ToolCall] = None

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_call"


@dataclass
class ToolResultEvent(AgentEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    result: str = ""  # JSON string

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_result"


@dataclass
class FinalEvent(AgentEvent):
    content: str = ""
    # P4-S24: passed through so `_run_chat` can persist it on the final
    # assistant turn (DB row's reasoning_content column).
    reasoning_content: str = ""
    stop_reason: str = "end_turn"
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "final"


@dataclass
class ErrorEvent(AgentEvent):
    reason: str = ""
    detail: str = ""
    # WI-R5: structured relay error code propagated from
    # LLMProviderError.error_class — "insufficient_balance" /
    # "relay_key_invalid". "" for non-relay / unclassified failures.
    # The frontend renders a friendly message + 充值 hint for it.
    error_class: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "error"


@dataclass
class ProviderChainFallbackEvent(AgentEvent):
    """P5-S2 Phase 3.6 — emitted when a provider in the chain fails
    transiently and the loop moves to the next provider.

    main.py forwards this as
    ``{type: "provider_chain_fallback", session_id, from, to, reason}``
    over the ws so the frontend can pop a diagnostic banner ("auto
    switched to backup B because A timed out").

    ``from_`` uses the trailing underscore because ``from`` is a Python
    keyword. Frontend serializer renames it to ``from`` over the wire
    (see main.py forwarder).
    """

    session_id: str = ""
    from_: str = ""
    to: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "provider_chain_fallback"


# ───────────────────── protocols for caller dependencies ─────────────────────


class _LLMRegistryProto(Protocol):
    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse: ...


class _ToolRegistryProto(Protocol):
    def schemas(self, enabled_toolsets: Optional[list[str]] = None) -> list[dict[str, Any]]: ...
    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any: ...


# ───────────────────── agent loop ─────────────────────


class AgentLoop:
    """ReAct-style driver around LLMRegistry + ToolRegistry."""

    def __init__(
        self,
        llm_registry: _LLMRegistryProto,
        tool_registry: _ToolRegistryProto,
        *,
        max_iterations: int = 20,
        budget_checker: Optional[DailyBudget] = None,
        default_model: Optional[str] = None,
        completion_probe: Optional[
            Callable[[str], Awaitable[list[dict[str, Any]]]]
        ] = None,
        max_completion_nudges: int = 2,
        activity_store: Optional[Any] = None,
        signature_repeat_threshold: Optional[int] = None,
        termination_gate: Optional[TerminationGate] = None,
        context_manager: Optional[ContextManager] = None,
        # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门 + ReceiptStore 拿 ledger
        verify_gate: Optional[Any] = None,        # deskpet.agent.verify_gate.VerifyGate
        receipt_store: Optional[Any] = None,      # deskpet.tools.receipt_store.ReceiptStore
        max_verify_nudges: int = 2,               # PRD D6: 3 次失败强退
        # WI-B3 Companion+Code v1: /goal command 末轮 LLM-judged rebound.
        # 两个都 None (默认) → BC，跳过整段 goal-check 块。
        session_goal_store: Optional[Any] = None,  # deskpet.agent.goal_store.SessionGoalStore
        goal_checker: Optional[Any] = None,        # deskpet.agent.goal_checker.GoalChecker
        # WI-2.1 structured reflection: when True, _REFLECTION_INSTRUCTION is
        # appended to verify-gate rebound + selfcheck tier2/tier3 system msgs.
        # Default False = BC (flag off → byte-identical behaviour to pre-WI-2.1).
        structured_reflection: bool = False,
        # WI-2.4 external evaluator: cross-persona quality judge for high-consequence
        # goals. None (default) = BC (0 extra LLM calls, skip entirely).
        external_evaluator: Optional[Any] = None,  # deskpet.agent.external_evaluator.ExternalEvaluator
        # WI-4.0 compaction: ContextCompressor to call when prompt tokens near cap.
        # None (default) = BC (compressor not injected → zero new behaviour).
        # When non-None, should_compress() and compress() are called in the loop.
        compressor: Optional[Any] = None,  # deskpet.agent.context_compressor.ContextCompressor
        # WI-4.2 skill remount: inject SkillLoader + SkillMatcher for post-compaction
        # skill body re-inline.  Both default None → BC (no remount, zero overhead).
        # When non-None and compaction fires, _remount_skills() is called to re-insert
        # the bodies of skills used this run as a single role=system block.
        skill_loader: Optional[Any] = None,   # deskpet.skills.loader.SkillLoader
        skill_matcher: Optional[Any] = None,  # deskpet.skills.skill_matcher.SkillMatcher
        # WI-1.6 工具路径录制（喂 FP-5 技能自创）。None (默认) → 不录（BC，零开销）。
        # 非 None 时每个 tool_result 喂 record_tool(name, ok)，complete() 由 codify hook 调。
        tool_path_recorder: Optional[Any] = None,  # deskpet.agent.tool_path.ToolPathRecorder
        # WI-4b pre-flush: 压缩真正摘掉中段前,把"当前任务态"写进 L1 文件记忆,
        # 跨 session 记住任务(frozen-snapshot → 只对下个 session 生效)。None (默认)
        # → 不 flush(BC)。每个 run 最多 flush 一次(限频,防刷爆 MEMORY.md 50KB cap)。
        file_memory: Optional[Any] = None,  # deskpet.memory.file_memory.FileMemory
    ) -> None:
        self.llm = llm_registry
        self.tools = tool_registry
        self.max_iterations = max_iterations
        self.budget_checker = budget_checker
        self.default_model = default_model
        # P4-S20: detect v2 ToolRegistry (has async execute_tool with
        # built-in permission gating). When present, dispatch routes
        # through it so user-permission popups work end-to-end. Legacy
        # registries (only `dispatch`) keep working unchanged.
        self._supports_execute_tool = callable(getattr(tool_registry, "execute_tool", None))
        # P5-S2 Hook A: completion guard. If supplied, ``completion_probe``
        # is called when the LLM tries to finalize (stop_reason ≠ tool_use)
        # to check whether session-level work (todos) is actually done.
        # The probe receives ``session_id`` and returns the list of
        # incomplete todo dicts ({content, activeForm, status, ...}).
        # If non-empty AND we still have nudges in budget, the loop
        # injects a system message reminding the LLM and re-runs the
        # iteration instead of finalizing. ``max_completion_nudges``
        # caps the rebound count to prevent infinite loops when the LLM
        # genuinely refuses to continue. Set to 0 to disable the hook.
        self.completion_probe = completion_probe
        self.max_completion_nudges = max_completion_nudges
        # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门
        self.verify_gate = verify_gate
        self.receipt_store = receipt_store
        self.max_verify_nudges = max_verify_nudges
        # WI-B3 Companion+Code v1: /goal store + checker (BC: 两者皆 None → skip)
        self.session_goal_store = session_goal_store
        self.goal_checker = goal_checker
        # WI-2.1 structured reflection flag (BC: False → no injection)
        self.structured_reflection = structured_reflection
        # WI-2.4 external evaluator: cross-persona quality judge for high-consequence
        # goals (BC: None → skip entirely, 0 extra LLM calls).
        self.external_evaluator = external_evaluator
        # WI-4.0 compaction: ContextCompressor (BC: None → skip entirely).
        # When non-None, loop calls should_compress() + compress() after budget check.
        self.compressor = compressor
        # WI-4.2 skill remount (BC: both None → skip entirely).
        # When non-None and compaction fires, _remount_skills() re-inlines skill bodies.
        self.skill_loader = skill_loader
        self.skill_matcher = skill_matcher
        # WI-1.6 工具路径录制器（BC: None → 不录）。喂 FP-5 4.3 技能自创触发器。
        self.tool_path_recorder = tool_path_recorder
        # WI-4b pre-flush L1 句柄（BC: None → 不 flush）。
        self.file_memory = file_memory
        # P5-S2 Phase 3.3: same-(name, args) repeat detection. When set,
        # the loop checks the activity store's per-session
        # ``tool_signature_window`` BEFORE dispatching each tool_call —
        # if the same signature already shows ``>= _REPEAT_THRESHOLD - 1``
        # consecutive prior calls, we suppress the dispatch and inject a
        # system nudge so the LLM gets a chance to change tactics.
        # Pre-Phase-3 callers leave this as None and the branch is a
        # no-op (verified by ``test_no_activity_store_means_no_repeat_detection``).
        self.activity_store = activity_store
        # P5-S2 Phase 6: per-instance override for the consecutive
        # same-(name, args) repeat threshold. Defaults to module
        # constant ``_REPEAT_THRESHOLD`` (3) so legacy callers keep
        # the existing behaviour. main.py wires
        # ``[supervisor].tool_signature_repeat_threshold`` here.
        self._signature_repeat_threshold = (
            int(signature_repeat_threshold)
            if signature_repeat_threshold is not None
            else _REPEAT_THRESHOLD
        )
        # P6 Phase 6 — TerminationGate is always wired in. Callers may
        # inject an explicit gate; otherwise a default gate is created
        # with max_turns = self.max_iterations. The legacy ``_gate is
        # None`` branch is gone — every code path goes through the gate.
        #
        # The soft selfcheck tier messages (_SELFCHECK_*) are kept in
        # place — they are complementary to the gate, not replaced by it.
        # The gate provides HARD termination semantics on top of those
        # nudges. The legacy _TOOL_BUDGET_HARD_MSG soft cap was removed
        # in Phase 6 (gate handles tool budget hard cap directly).
        self._gate: TerminationGate = (
            termination_gate
            if termination_gate is not None
            else TerminationGate(GateConfig(max_turns=self.max_iterations))
        )

        # P6 Phase 6 — ContextManager is always wired in. Same shape as
        # _gate above: caller may inject one, otherwise a default is
        # created. The tool-result write site delegates truncation to
        # ctx.record_tool_result (which honours skip_truncation_for_tools
        # — the G1 fix). The legacy ``_ctx is None`` branch is gone.
        self._ctx: ContextManager = (
            context_manager
            if context_manager is not None
            else ContextManager()
        )

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        task_id: Optional[str] = None,
        tools_filter: Optional[list[str]] = None,
        model: Optional[str] = None,
        session_id: str = "default",
        stream: bool = False,
        provider_chain: Optional[list[Any]] = None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Drive the ReAct loop. See module docstring for event contract.

        ``stream`` (P4-S25 A1): when True and the registry exposes
        ``chat_with_fallback_stream``, each LLM iteration emits
        :class:`AssistantDeltaEvent` per token chunk before the
        :class:`AssistantMessageEvent` lands at the end of that
        iteration. False (default) preserves the original non-streaming
        behaviour for callers that don't need partial output.
        """
        tid = task_id or new_task_id()
        working_messages: list[dict[str, Any]] = list(messages)
        tool_schemas = self.tools.schemas(enabled_toolsets=tools_filter)

        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        use_model = model or self.default_model

        # ──────────────── P5-S2 Phase 3: provider chain mode ────────────────
        #
        # When ``provider_chain`` is supplied, AgentLoop walks the chain
        # itself instead of delegating to ``self.llm.chat_with_fallback``.
        # Each provider is tried in order; LLMProviderError (transient)
        # falls through to the next; permanent errors (args_parse_error
        # etc. — surfaced via ChatResponse, NOT exceptions) short-circuit
        # the chain because the next provider would just see the same
        # broken request and fail identically.
        #
        # Empty chain is an actionable user-facing error — emit it once
        # at the top of run() so the loop never tries to call anything.
        chain_mode = provider_chain is not None
        if chain_mode and not provider_chain:
            yield ErrorEvent(
                type="error",
                task_id=tid,
                iteration=0,
                reason="no_provider_configured",
                detail=(
                    "未配置任何 LLM provider。"
                    "请打开设置 → LLM Providers → 添加"
                ),
            )
            return
        # Detect streaming capability lazily — agent loop tests use a
        # mock registry that may only define chat_with_fallback.
        stream_capable = stream and callable(
            getattr(self.llm, "chat_with_fallback_stream", None)
        )

        # P5-S2 Hook A: per-run nudge counter for the completion guard.
        # Reset every fresh ``run`` invocation so each chat turn gets a
        # fresh nudge budget — we don't want stale "already nudged 2x"
        # state leaking between turns.
        completion_nudges_used = 0
        # WI-T2.6: 同 completion_nudges_used 模式 — 本轮起算
        verify_nudges_used = 0
        # WI-2.2: track previous task_replanning for stagnation detection
        _prev_task_replanning: str = ""
        # P6 Phase 6 — local mirror of gate.state.tools_used kept so the
        # selfcheck tier messages can format "已用 N 次工具调用". The gate
        # is the source of truth for hard-cap enforcement; this var is
        # purely informational.
        tools_used_count = 0

        # P5-S2 B3: warn-once latch so we don't spam the WARN log every
        # iteration once we're in the 80-95% band.
        _budget_warn_emitted = False

        # WI-4a 目标 always-on 单点注入(对标 Claude Code 钉死 CLAUDE.md)。
        # 唯一注入点 = 这里(循环前注一次),role=system → context_compressor._partition
        # 永久排除、永不被压;compress() 见到它就不再自注(去重),整轮恒 ≤1 条 [目标锚定]。
        # 取代了原"周期性 anchor(每 _GOAL_ANCHOR_EVERY 轮重注)"+"压缩后 _build_goal_anchor
        # 注入"两处冗余。目标 + 子目标都从 session_goal_store 取。
        if self.session_goal_store is not None:
            _ga_fn = getattr(self.session_goal_store, "get_goal_text", None)
            _always_goal = _ga_fn(session_id) if callable(_ga_fn) else None
            if _always_goal:
                _anchor_lines = [f"[目标锚定] 当前目标：{_always_goal}"]
                _pg_fn = getattr(self.session_goal_store, "get_pending_tasks", None)
                _pending = _pg_fn(session_id) if callable(_pg_fn) else None
                if _pending:
                    _anchor_lines.append(f"[当前子目标] {_pending[0]}")
                _anchor_lines.append(
                    "请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏。"
                )
                working_messages.append(
                    {"role": "system", "content": "\n".join(_anchor_lines)}
                )
                logger.info(
                    "wi4a_goal_anchor_always_on sid=%s tid=%s", session_id, tid
                )

        # WI-4.0 compaction: warn-once latch so we don't spam the log every
        # iteration when the compressor fires (long multi-tool tasks may cross
        # the threshold repeatedly; we log the first fire and stay quiet after).
        _compaction_warn_logged: bool = False

        # WI-4b pre-flush 限频 latch: 每个 run 最多把任务态 flush 进 L1 一次
        # (防长 agentic 任务反复触发压缩时刷爆 MEMORY.md 50KB cap → 驱逐真实记忆)。
        _preflush_done: bool = False

        # FP-2 TC-2.1 第 3 刀: relay 真实 prompt_tokens 反馈回路。char-based
        # 估算对中文/markdown 系统性低估(真机 real 32.9k 时 estimate <24k),
        # 纯系数追不上内容分布 → 用上一轮 response.usage.input_tokens 兜底,
        # compaction 判定取 max(estimate, real)。0 = 本 run 还没有真实值。
        _last_real_prompt_tokens: int = 0

        # WI-4.2 skill remount: track skill_invoke calls this run so that
        # _remount_skills() knows which skill bodies to re-inline after compaction.
        # Reset per run() invocation (fresh list each chat turn).
        # List preserves insertion order for LRU-drop logic; dedup via "already
        # appended" check below.
        self._skills_used_order: list[str] = []
        self._skills_used_this_run: set[str] = set()

        for iteration in range(1, self.max_iterations + 1):
            # P6 Phase 6 — TerminationGate.allows_call() is always run.
            # Checks hard limits (turns, wall-clock, cost) BEFORE we burn
            # another LLM call. The gate is the single source of truth
            # for "should this loop keep going?".
            _ok, _reason = self._gate.allows_call()
            if not _ok:
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason=(_reason.value if _reason is not None else "unknown"),
                    detail="Termination gate blocked LLM call",
                )
                return

            # P5-S2 B3: token budget guard. Runs BEFORE the LLM call so
            # we can surface an actionable error (BLOCK) or pre-emptive
            # warning (WARN) instead of waiting for the model to choke
            # on context_length_exceeded.
            #
            # P6 Phase 6 — always delegate to self._ctx.check_budget so
            # chat handler + AgentLoop share one budget evaluator.
            try:
                from agent.token_budget import (
                    BudgetCheck as _BudgetCheck,
                )
                # Use the FIRST provider's model as the context-window
                # reference (chain mode). In single-provider mode fall
                # back to default_model.
                _budget_model = use_model
                if chain_mode and provider_chain:
                    _first = provider_chain[0]
                    _budget_model = getattr(_first, "model", None) or use_model
                _resolved_model = _budget_model or "unknown"
                _budget = self._ctx.check_budget(
                    working_messages, model=_resolved_model,
                )
                if _budget.verdict is _BudgetCheck.BLOCK:
                    logger.error(
                        "p5s2_token_budget_block sid=%s tid=%s iter=%d "
                        "tokens=%d window=%d ratio=%.2f",
                        session_id, tid, iteration,
                        _budget.estimated_tokens, _budget.context_window,
                        _budget.ratio,
                    )
                    # P6 Phase 6 — record the same reason on the gate so
                    # summary() reflects the real cause (otherwise the
                    # gate would stay "running").
                    self._gate.record_error(
                        TerminationReason.CONTEXT_BUDGET_BLOCK
                    )
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason="context_budget_block",
                        detail=_budget.advice,
                    )
                    return
                elif _budget.verdict is _BudgetCheck.WARN and not _budget_warn_emitted:
                    logger.warning(
                        "p5s2_token_budget_warn sid=%s tid=%s iter=%d "
                        "tokens=%d window=%d ratio=%.2f",
                        session_id, tid, iteration,
                        _budget.estimated_tokens, _budget.context_window,
                        _budget.ratio,
                    )
                    _budget_warn_emitted = True
            except Exception as _b_exc:  # noqa: BLE001
                # Budget check is advisory — never fail the loop on it.
                logger.debug("token_budget_check_failed err=%s", str(_b_exc)[:100])

            # Budget gate BEFORE the call — can't take back tokens after the fact.
            if self.budget_checker is not None and not self.budget_checker.check_allowed():
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="budget_exceeded",
                    detail=f"daily budget cap reached (${self.budget_checker.cap_usd:.2f})",
                )
                return

            # WI-4.0 compaction: after budget guard, before LLM call.
            # Reuses _budget.estimated_tokens (just computed above).
            # compressor=None (flag off) → short-circuit, zero overhead.
            # Operates in-place on working_messages (not assemble — BC).
            # System messages (skill_prelude/persona/frozen) are kept verbatim
            # by _partition inside compress() — "不绕 assemble" constraint met.
            if self.compressor is not None:
                try:
                    _ctoken_est = getattr(_budget, "estimated_tokens", 0)
                except Exception:  # noqa: BLE001
                    _ctoken_est = 0
                # 第 3 刀: real usage 兜底(见 _last_real_prompt_tokens 注释)。
                if _last_real_prompt_tokens > _ctoken_est:
                    _ctoken_est = _last_real_prompt_tokens
                # ★ 第 4 刀(真机测压缩发现的核心 bug 修复): 直接数【即将发送的
                # working_messages】的 token。原来只用 _budget.estimated_tokens(被
                # BudgetAllocator 压到 window×0.6,低于压缩阈值 window×0.8)+
                # _last_real_prompt_tokens(每条消息开头重置 0、且单轮聊天不迭代第二次
                # 拿不到真值) → 两路都够不到阈值 → 压缩**永不触发**(实测 12 消息/
                # 1746 真 token 仍不压)。直接数 working_messages 调用前可得、不受
                # allocator 截断、不延迟,是最可靠的触发信号(复用统一计数,优化 #1+#3)。
                try:
                    from deskpet.agent.tokens import count_messages_tokens as _cmt
                    _wm_tokens = _cmt(working_messages)
                    if _wm_tokens > _ctoken_est:
                        _ctoken_est = _wm_tokens
                except Exception:  # noqa: BLE001
                    pass
                _ctx_should_compress = False
                try:
                    _ctx_cfg = getattr(self._ctx, "config", None)
                    _ctx_compact_at = getattr(_ctx_cfg, "compact_at_tokens", None)
                    if _ctx_compact_at is not None:
                        _ctx_should_compress = (
                            _ctoken_est >= int(_ctx_compact_at) > 0
                        )
                except Exception:  # noqa: BLE001
                    _ctx_should_compress = False
                if (
                    self.compressor.should_compress(_ctoken_est)
                    or _ctx_should_compress
                ):
                    try:
                        _gt = None
                        if self.session_goal_store is not None:
                            _gt_fn = getattr(self.session_goal_store, "get_goal_text", None)
                            if callable(_gt_fn):
                                _gt = _gt_fn(session_id)
                        # WI-4b pre-flush: 摘掉中段前把任务态写进 L1(跨 session 记任务)。
                        # best-effort + 每 run 限一次(latch),失败绝不阻断压缩。
                        if self.file_memory is not None and not _preflush_done:
                            _preflush_done = True
                            try:
                                _last_user = ""
                                for _m in reversed(working_messages):
                                    if _m.get("role") == "user":
                                        _last_user = str(_m.get("content") or "")[:500]
                                        break
                                _flush_parts = []
                                if _gt:
                                    _flush_parts.append(f"目标: {_gt}")
                                if _last_user:
                                    _flush_parts.append(f"最近请求: {_last_user}")
                                if _flush_parts:
                                    _flush_body = (
                                        "[任务态快照/task-state] " + "; ".join(_flush_parts)
                                    )
                                    await self.file_memory.append(
                                        "memory", _flush_body, salience=0.6
                                    )
                                    logger.info(
                                        "wi4b_preflush_l1 sid=%s tid=%s chars=%d",
                                        session_id, tid, len(_flush_body),
                                    )
                            except Exception as _pf_exc:  # noqa: BLE001
                                logger.debug(
                                    "wi4b_preflush_failed sid=%s err=%s",
                                    session_id, str(_pf_exc)[:120],
                                )
                        _cresult = await self.compressor.compress(
                            working_messages,
                            goal_text=_gt,
                        )
                        if getattr(_cresult, "compressed", False):
                            working_messages = _cresult.messages
                            # WI-4.2: re-inline skill bodies after compaction so
                            # the LLM doesn't lose skill step details that were
                            # in the compressed "middle" messages.
                            working_messages = self._remount_skills(
                                working_messages, session_id
                            )
                            if not _compaction_warn_logged:
                                logger.info(
                                    "p1_4_compaction_fired sid=%s tid=%s iter=%d "
                                    "reduction=%s",
                                    session_id, tid, iteration,
                                    getattr(_cresult, "reduction_ratio", "?"),
                                )
                                _compaction_warn_logged = True
                    except Exception as _cmp_exc:  # noqa: BLE001
                        # Compaction is advisory — never abort the loop on it.
                        logger.debug(
                            "p1_4_compaction_failed sid=%s iter=%d err=%s",
                            session_id, iteration, str(_cmp_exc)[:200],
                        )

            # P6 Phase 6: escalating in-loop self-check (soft nudge).
            # The legacy _TOOL_BUDGET_HARD_MSG soft cap is GONE — the
            # TerminationGate now enforces tool_budget_hard with a HARD
            # break and emits ErrorEvent(error_tool_budget). The selfcheck
            # tier injection below is complementary (gentle reflection
            # nudge to coax stop_reason=end_turn earlier).

            # Count tools used so far in this loop run (tracked at each
            # tool dispatch). `tools_used` mirrors gate.state.tools_used.
            tools_used = locals().get("tools_used_count", 0)

            if iteration > 0 and iteration % _SELFCHECK_EVERY == 0:
                budget_left = self.max_iterations - iteration
                msg = _build_selfcheck_message(iteration, self.max_iterations, tools_used)
                # WI-2.1: append reflection instruction at tier2/tier3 only
                # (zero overhead on normal tier1 path; flag off = BC).
                if self.structured_reflection and iteration >= _SELFCHECK_TIER2_AT:
                    from deskpet.agent.reflection import _REFLECTION_INSTRUCTION
                    msg = msg + _REFLECTION_INSTRUCTION
                working_messages.append({"role": "system", "content": msg})
                tier = (
                    3 if iteration >= _SELFCHECK_TIER3_AT
                    else 2 if iteration >= _SELFCHECK_TIER2_AT
                    else 1
                )
                logger.info(
                    "p5s2_selfcheck_injected sid=%s tid=%s iter=%d budget=%d "
                    "tier=%d tools_used=%d structured_reflection=%s",
                    session_id, tid, iteration, budget_left, tier, tools_used,
                    self.structured_reflection,
                )

            # WI-4a: 周期性 [目标锚定] 注入已删除 —— 改由循环前的 always-on 单点注入
            # (见上方 wi4a_goal_anchor_always_on)。always-on 那条 role=system 常驻、
            # 不被压、整轮恒 ≤1 条,周期重注是冗余且会堆多条同文 system。防 drift 由
            # always-on 常驻 + WI-3 结构化摘要保任务共同覆盖,能力不丢。

            try:
                if chain_mode:
                    # ─── Phase 3: walk the chain ───
                    # On transient LLMProviderError, yield a
                    # ProviderChainFallbackEvent and try the next
                    # provider. On success, accept the response and
                    # break. If every provider fails, emit
                    # ErrorEvent(reason="all_providers_failed").
                    from agent.tool_use_shim import _raw_to_response

                    response = None  # type: ignore[assignment]
                    last_exc: Optional[Exception] = None
                    for idx, prov in enumerate(provider_chain):  # type: ignore[arg-type]
                        try:
                            raw = await prov.chat_with_tools(
                                working_messages,
                                tools=tool_schemas or None,
                                # P6 bugfix 2026-05-14 (live-test):
                                # bumped default 2048 → 8192. 2048 was
                                # too tight for code-mode write_file:
                                # 6KB React file ≈ 2000+ tokens →
                                # output truncated mid-string →
                                # "Unterminated string" JSON parse fail
                                # → permanent_tool_error → circuit
                                # breaker → user stuck. 8192 leaves
                                # comfortable headroom (~24KB output)
                                # without 显著 cost spike on the relay.
                                max_tokens=int(llm_kwargs.get("max_tokens", 8192)),
                                temperature=llm_kwargs.get("temperature"),
                                response_format=llm_kwargs.get("response_format"),
                            )
                        except LLMProviderError as exc:
                            last_exc = exc
                            prov_id = getattr(prov, "id", f"provider_{idx}")
                            next_idx = idx + 1
                            if next_idx < len(provider_chain):  # type: ignore[arg-type]
                                next_prov = provider_chain[next_idx]  # type: ignore[index]
                                next_id = getattr(
                                    next_prov, "id", f"provider_{next_idx}"
                                )
                                reason = str(exc) or type(exc).__name__
                                logger.warning(
                                    "provider_chain_fallback from=%s to=%s "
                                    "reason=%s sid=%s tid=%s",
                                    prov_id, next_id, reason[:200],
                                    session_id, tid,
                                )
                                yield ProviderChainFallbackEvent(
                                    type="provider_chain_fallback",
                                    task_id=tid,
                                    iteration=iteration,
                                    session_id=session_id,
                                    from_=prov_id,
                                    to=next_id,
                                    reason=reason,
                                )
                            else:
                                logger.warning(
                                    "provider_chain_last_provider_failed "
                                    "id=%s reason=%s sid=%s",
                                    prov_id, str(exc)[:200], session_id,
                                )
                            continue
                        else:
                            response = _raw_to_response(raw)
                            break

                    if response is None:
                        # All providers in the chain raised.
                        tried = len(provider_chain)  # type: ignore[arg-type]
                        last_text = str(last_exc) if last_exc else "unknown"
                        # P6 Phase 6 — record terminal error reason on the
                        # gate so callers reading summary() see the real
                        # cause (otherwise the gate would stay "running").
                        self._gate.record_error(
                            TerminationReason.ALL_PROVIDERS_FAILED
                        )
                        yield ErrorEvent(
                            type="error",
                            task_id=tid,
                            iteration=iteration,
                            reason="all_providers_failed",
                            detail=(
                                f"tried {tried}, last_error: {last_text}"
                            ),
                        )
                        return
                elif stream_capable:
                    # Streaming path: forward delta events, accumulate
                    # the final dict, then build the same ChatResponse
                    # the non-streaming path would have produced.
                    final_dict: dict | None = None
                    delta_count = 0
                    stream_failed_with: Exception | None = None
                    try:
                        async for ev in self.llm.chat_with_fallback_stream(  # type: ignore[attr-defined]
                            working_messages,
                            tools=tool_schemas or None,
                            model=use_model,
                            **llm_kwargs,
                        ):
                            ev_type = ev.get("type")
                            if ev_type == "delta":
                                delta_count += 1
                                yield AssistantDeltaEvent(
                                    type="assistant_delta",
                                    task_id=tid,
                                    iteration=iteration,
                                    content=ev.get("content", ""),
                                    kind="content",
                                )
                            elif ev_type == "delta_reasoning":
                                delta_count += 1
                                yield AssistantDeltaEvent(
                                    type="assistant_delta",
                                    task_id=tid,
                                    iteration=iteration,
                                    content=ev.get("content", ""),
                                    kind="reasoning",
                                )
                            elif ev_type == "final":
                                final_dict = ev
                    except LLMProviderError as stream_exc:
                        # P4-S25 fix: streaming raised after exhausting its
                        # own retry budget (typically RemoteProtocolError
                        # 3-in-a-row from the relay). Fall back to the non-
                        # streaming path instead of bubbling the error
                        # to the user — non-stream is more reliable on
                        # the relay in our observed traffic, AND it has its
                        # own independent retry budget so we effectively
                        # double the resilience without hard-coding 6
                        # retries.
                        stream_failed_with = stream_exc
                        logger.warning(
                            "agent_loop_stream_failed_falling_back "
                            "error=%s", str(stream_exc)[:200],
                        )

                    needs_nonstream_fallback = (
                        stream_failed_with is not None
                        or final_dict is None
                        or (
                            delta_count == 0
                            and not final_dict.get("content")
                            and not final_dict.get("tool_calls")
                        )
                    )
                    if needs_nonstream_fallback:
                        if stream_failed_with is None:
                            # Empty-stream case (the relay didn't actually stream).
                            logger.warning(
                                "agent_loop_stream_fallback_to_nonstream "
                                "delta_count=%d", delta_count,
                            )
                        response = await self.llm.chat_with_fallback(
                            working_messages,
                            tools=tool_schemas or None,
                            model=use_model,
                            **llm_kwargs,
                        )
                    else:
                        # Convert to ChatResponse to share the rest of the
                        # iteration code with the non-streaming path.
                        from agent.tool_use_shim import _raw_to_response
                        response = _raw_to_response(final_dict)
                else:
                    response = await self.llm.chat_with_fallback(
                        working_messages,
                        tools=tool_schemas or None,
                        model=use_model,
                        **llm_kwargs,
                    )
            except LLMBudgetExceededError as exc:
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="budget_exceeded",
                    detail=str(exc),
                )
                return
            except LLMProviderError as exc:
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="llm_error",
                    detail=str(exc),
                    # WI-R5: carry the relay error code so the frontend
                    # can show 余额不足 / key 失效 friendly messages.
                    error_class=getattr(exc, "error_class", "") or "",
                )
                return

            totals["input"] += response.usage.input_tokens
            totals["output"] += response.usage.output_tokens
            totals["cache_read"] += response.usage.cache_read_tokens
            totals["cache_write"] += response.usage.cache_write_tokens
            # 第 3 刀: 记录 relay 真实 prompt 大小,喂下一轮 compaction 判定。
            if response.usage.input_tokens > _last_real_prompt_tokens:
                _last_real_prompt_tokens = response.usage.input_tokens

            # P6 Phase 6 — record the turn (advances turns_used and
            # optionally adds to cost_usd if the response carries a
            # cost_usd extra; ChatUsage has no such field today so we
            # pass 0.0). The gate's allows_call check at top of next
            # iteration uses turns_used to decide whether to keep going.
            _cost_delta = 0.0
            _usage = getattr(response, "usage", None)
            if _usage is not None:
                _maybe_cost = getattr(_usage, "cost_usd", None)
                if isinstance(_maybe_cost, (int, float)):
                    _cost_delta = float(_maybe_cost)
            self._gate.record_turn(cost_delta_usd=_cost_delta)

            yield AssistantMessageEvent(
                type="assistant_message",
                task_id=tid,
                iteration=iteration,
                content=response.content,
                reasoning_content=response.reasoning_content,
                tool_calls=list(response.tool_calls),
                stop_reason=response.stop_reason,
                model=response.model,
            )

            # End of conversation — emit final and stop.
            if response.stop_reason != "tool_use" or not response.tool_calls:
                # P5-S2 Hook A: completion guard. Before truly finalizing,
                # ask the caller (via ``completion_probe``) whether session-
                # level work (todos) is actually finished. If the LLM said
                # "I'm done" but the SessionDB still has incomplete todos,
                # rebound with a system message reminding it to either
                # finish them or explicitly mark them cancelled. Capped at
                # ``max_completion_nudges`` so we can't loop forever when
                # the LLM digs in.
                if (
                    self.completion_probe is not None
                    and self.max_completion_nudges > 0
                    and completion_nudges_used < self.max_completion_nudges
                ):
                    try:
                        incomplete = await self.completion_probe(session_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "p5s2_completion_probe_failed sid=%s err=%s",
                            session_id, str(exc)[:200],
                        )
                        incomplete = []
                    if incomplete:
                        completion_nudges_used += 1
                        # Build the rebound system message. Keep it brief
                        # — long prompts crowd the context window.
                        bullet_list = "\n".join(
                            f"  - {(t.get('content') or '').strip()[:120]}"
                            for t in incomplete[:8]
                        )
                        rebound = (
                            f"你声明已完成（stop_reason={response.stop_reason or 'end_turn'}），"
                            f"但 todos 里还有 {len(incomplete)} 项未完成：\n"
                            f"{bullet_list}\n\n"
                            "请继续执行剩余 todos。如果某项确实做不了 / 不该做，"
                            "请用 todo_write 把它标成 completed 并简述原因（或者改文案明确说明放弃理由）。"
                            "不要再次空口说『我做完了』。"
                        )
                        # Append the assistant message that triggered this
                        # so the LLM sees its own prior end_turn output —
                        # otherwise the rebound system message has no
                        # context for "what did I just stop on".
                        if response.content:
                            working_messages.append({
                                "role": "assistant",
                                "content": response.content,
                            })
                        working_messages.append({
                            "role": "system",
                            "content": rebound,
                        })
                        logger.info(
                            "p5s2_completion_nudge_injected "
                            "sid=%s nudge=%d/%d incomplete=%d",
                            session_id,
                            completion_nudges_used,
                            self.max_completion_nudges,
                            len(incomplete),
                        )
                        # Skip the final emission and re-iterate. The
                        # next LLM call sees the nudge.
                        continue

                # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门（PRD §3 D6）。
                # 同 completion_probe 模式 — 守门返回 outcome.passed=False 时
                # 回灌 D8 schema system message + continue；max_verify_nudges
                # 控制重试上限（PRD: failure_count==3 时调度 ephemeral → 仍
                # fail 才强退）。flag-off 时 verify_gate=None 跳过整段（BC）。
                if (
                    self.verify_gate is not None
                    and getattr(self.verify_gate, "mode", "off") != "off"
                    and verify_nudges_used < self.max_verify_nudges
                    and response.content
                ):
                    try:
                        # 拉取本 session 的 sig-filtered ledger（N1 信任面已在
                        # ReceiptStore.load_session 内强制 hmac_verify）
                        ledger = (
                            self.receipt_store.load_session(session_id)
                            if self.receipt_store is not None else []
                        )
                        # WI-2.3: thread goal_text into verify_gate.check so
                        # GoalAlignment is populated when an active goal exists.
                        # BC: goal_text=None (no store / no active goal) → byte-identical
                        # to pre-WI-2.3 (check ignores None goal_text).
                        _vg_goal_text: Optional[str] = None
                        if self.session_goal_store is not None:
                            _gt_fn = getattr(
                                self.session_goal_store, "get_goal_text", None
                            )
                            if callable(_gt_fn):
                                _vg_goal_text = _gt_fn(session_id)
                        v_outcome = self.verify_gate.check(
                            assistant_text=response.content,
                            ledger=ledger,
                            goal_text=_vg_goal_text,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "verify_gate.check failed (sid=%s): %s — passing through",
                            session_id, exc,
                        )
                        v_outcome = None

                    if (v_outcome is not None and not v_outcome.passed
                            and v_outcome.unmatched_claims):
                        verify_nudges_used += 1

                        # WI-2.2: stagnation detection — if 2nd+ rebound and
                        # task_replanning text is nearly identical to previous
                        # round (difflib ratio > 0.85), the LLM is stuck in
                        # copy-paste instead of genuinely replanning. Skip to
                        # ephemeral immediately (don't waste the 2nd nudge).
                        _stagnant = False
                        if verify_nudges_used >= 2 and self.structured_reflection:
                            try:
                                from deskpet.agent.reflection import parse_reflection
                                import difflib as _difflib
                                _refl = parse_reflection(response.content or "")
                                _cur_replan = str(_refl.task_replanning) if _refl else ""
                                if (
                                    _prev_task_replanning
                                    and _cur_replan
                                    and _difflib.SequenceMatcher(
                                        None, _prev_task_replanning, _cur_replan
                                    ).ratio() > 0.85
                                ):
                                    _stagnant = True
                                    logger.info(
                                        "verify_replan_stagnant sid=%s nudge=%d "
                                        "ratio=%.2f — escalating to ephemeral",
                                        session_id, verify_nudges_used,
                                        _difflib.SequenceMatcher(
                                            None, _prev_task_replanning, _cur_replan
                                        ).ratio(),
                                    )
                                    try:
                                        from observability.metrics_sink import (
                                            record as _vg_metric,
                                        )
                                        _vg_metric("verify_replan_stagnant", {
                                            "nudge_count": int(verify_nudges_used),
                                        })
                                    except Exception:  # noqa: BLE001
                                        pass
                                if _refl:
                                    _prev_task_replanning = _cur_replan
                            except Exception:  # noqa: BLE001 — safe-fail
                                pass

                        # 失败计数达 max 或 stagnation → 调 ephemeral 救援
                        ephemeral_pass = False
                        if _stagnant or verify_nudges_used >= self.max_verify_nudges:
                            try:
                                ephemeral_pass = await (
                                    self.verify_gate.consult_ephemeral_subagent(
                                        ledger=ledger,
                                        failed_claims=v_outcome.unmatched_claims,
                                        assistant_text=response.content,
                                    )
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("ephemeral consult failed: %s", exc)

                        if not ephemeral_pass:
                            # WI-2.2: verify_exhausted — all layers (nudges +
                            # ephemeral) exhausted → emit terminal error + return.
                            # Only when we've either stagnated or used all nudges
                            # AND ephemeral failed.
                            if _stagnant or verify_nudges_used >= self.max_verify_nudges:
                                logger.warning(
                                    "verify_exhausted sid=%s nudge=%d/%d "
                                    "stagnant=%s",
                                    session_id, verify_nudges_used,
                                    self.max_verify_nudges, _stagnant,
                                )
                                try:
                                    from observability.metrics_sink import (
                                        record as _vg_metric,
                                    )
                                    _vg_metric("verify_exhausted", {
                                        "nudge_count": int(verify_nudges_used),
                                        "stagnant": bool(_stagnant),
                                    })
                                except Exception:  # noqa: BLE001
                                    pass
                                yield ErrorEvent(
                                    type="error",
                                    task_id=tid,
                                    iteration=iteration,
                                    reason="verify_exhausted",
                                    detail=(
                                        f"verify-gate: all retries exhausted after "
                                        f"{verify_nudges_used} nudge(s). "
                                        f"stagnant={_stagnant}"
                                    ),
                                )
                                return

                            # Still have nudges: inject rebound + 回灌 D8 schema
                            unmatched_lines = "\n".join(
                                f"  {i+1}. [unmatched_claim] {c.raw_text!r} — "
                                f"no receipt matched ({c.reason})"
                                for i, c in enumerate(v_outcome.unmatched_claims[:5])
                            )
                            rebound = (
                                f"[verify-gate] iteration={iteration} blocked end_turn.\n"
                                f"Failures:\n{unmatched_lines}\n"
                                f"Classification: unmatched_claim\n"
                                f"Next: please call the missing tool to actually "
                                f"perform the action you claimed, then end_turn again."
                            )
                            # WI-2.3: append goal alignment context to rebound
                            # when goal_text was provided and GoalAlignment is
                            # populated. goal_text=None → rebound unchanged (BC).
                            if (
                                v_outcome.goal_alignment is not None
                                and not v_outcome.goal_alignment.aligned
                            ):
                                ga = v_outcome.goal_alignment
                                _evidence_str = "\n".join(
                                    f"  - {e}"
                                    for e in ga.objective_evidence[:5]
                                ) or "  (无客观证据)"
                                rebound = (
                                    rebound
                                    + f"\n原始目标: {ga.goal_text}\n"
                                    + f"客观证据: {_evidence_str}\n"
                                    + f"缺口: {ga.gap}\n"
                                    + "→ 请重规划（结构化反思）后补齐，"
                                    + "使产物真满足原目标，而非换个说法过关。"
                                )
                            # WI-2.1: append reflection instruction when flag on.
                            # Flag off (default) → rebound string unchanged (BC).
                            if self.structured_reflection:
                                from deskpet.agent.reflection import _REFLECTION_INSTRUCTION
                                rebound = rebound + _REFLECTION_INSTRUCTION
                            if response.content:
                                working_messages.append({
                                    "role": "assistant",
                                    "content": response.content,
                                })
                            working_messages.append({
                                "role": "system",
                                "content": rebound,
                            })
                            logger.info(
                                "verify_gate_nudge_injected sid=%s nudge=%d/%d "
                                "unmatched=%d ephemeral_pass=%s",
                                session_id, verify_nudges_used,
                                self.max_verify_nudges,
                                len(v_outcome.unmatched_claims),
                                ephemeral_pass,
                            )
                            # WI-T2.1 v3：真 emit 到 metrics.jsonl（MR-T-8 拦截
                            # 硬证据 — 用户 goal "verify_* event in metrics.jsonl"
                            # 把 fake-completion 拦截事件计数化，供监控面板看健康率）
                            try:
                                from observability.metrics_sink import (
                                    record as _verify_metric,
                                )
                                _verify_metric("verify_gate_nudge_injected", {
                                    "nudge_count": int(verify_nudges_used),
                                    "count": int(len(v_outcome.unmatched_claims)),
                                    "ok": bool(ephemeral_pass),
                                })
                            except Exception:  # noqa: BLE001 — metric 失败不阻 dispatch
                                pass
                            continue
                        else:
                            logger.info(
                                "verify_gate.ephemeral_rescued sid=%s", session_id,
                            )

                # WI-B3 Companion+Code v1 — /goal end_turn rebound. Same
                # safe-fail pattern as completion_probe / verify_gate:
                # checker exception or done=False → inject a system
                # message and ``continue`` the loop so the next LLM call
                # sees the hint. Cap at ``goal.max_iterations`` so the
                # checker can't loop forever. flag-off (store / checker
                # = None, or no active goal, or goal.done already) →
                # skip the block entirely (BC).
                if (
                    self.session_goal_store is not None
                    and self.goal_checker is not None
                ):
                    _goal = self.session_goal_store.get(session_id)
                    if (
                        _goal is not None
                        and not _goal.done
                        and _goal.iterations_used < _goal.max_iterations
                    ):
                        try:
                            _done, _hint = await self.goal_checker.check(
                                _goal.text, working_messages,
                            )
                        except Exception as exc:  # noqa: BLE001 — safe-fail
                            logger.warning(
                                "goal_checker raised (sid=%s): %s — skipping check",
                                session_id, exc,
                            )
                            # R-T3 §15.4: 兜底也用 skipped 语义（check() 内已 safe-fail，
                            # 这里只作双重防护）。不默认 done=True。
                            _done, _hint = False, "goal_check=skipped"
                        # metric emit (best-effort, not blocking)
                        try:
                            from observability.metrics_sink import (  # noqa: PLC0415
                                record as _goal_metric,
                            )
                            _goal_metric("goal_checker_invoked", {
                                "ok": bool(_done),
                                "count": int(_goal.iterations_used),
                            })
                        except Exception:  # noqa: BLE001 — metric 失败不阻
                            pass
                        # R-T3 §15.4: goal_check=skipped → checker 降级，
                        # 无法正向确认 → 不 mark_done，但也不注入"未达成"nudge
                        # （我们不知道目标是否真达成，不能给 LLM 假信号）。
                        # 直接 fall-through 到 FinalEvent / evaluator gate。
                        if _hint == "goal_check=skipped":
                            logger.info(
                                "goal_checker.skipped sid=%s — "
                                "checker degraded, proceeding without goal confirmation",
                                session_id,
                            )
                            # 不 continue，让正常 FinalEvent 流程继续
                        elif not _done:
                            self.session_goal_store.increment_iteration(session_id)
                            # T1：落库 iterations_used，重启不归零。safe-fail
                            # 内置于 persist_iteration；getattr 兜底旧 store。
                            _pit = getattr(
                                self.session_goal_store,
                                "persist_iteration", None,
                            )
                            if _pit is not None:
                                await _pit(session_id)
                            # Append the assistant message that triggered
                            # this so the LLM sees its own prior end_turn
                            # output — symmetric with completion_probe /
                            # verify_gate rebound shape.
                            if response.content:
                                working_messages.append({
                                    "role": "assistant",
                                    "content": response.content,
                                })
                            working_messages.append({
                                "role": "system",
                                "content": (
                                    f"[goal] 未达成 "
                                    f"({_goal.iterations_used}/{_goal.max_iterations}): "
                                    f"{_hint}\n继续工作直到目标完成。"
                                ),
                            })
                            logger.info(
                                "goal_checker_nudge_injected sid=%s "
                                "iter=%d/%d",
                                session_id,
                                _goal.iterations_used,
                                _goal.max_iterations,
                            )
                            continue
                        else:
                            self.session_goal_store.mark_done(session_id)
                            # 落库 done 终态（与 increment 落库对称）：否则
                            # load_persisted 只召回 status='active'，已完成目标
                            # 重启会复活成 active。getattr 兜底旧 store。
                            _pd = getattr(
                                self.session_goal_store,
                                "persist_done", None,
                            )
                            if _pd is not None:
                                await _pd(session_id)
                            logger.info(
                                "goal_checker.marked_done sid=%s",
                                session_id,
                            )

                # WI-2.4: external evaluator gate — BEFORE FinalEvent.
                # Only fires when:
                #   a) external_evaluator is wired (flag on), AND
                #   b) is_high_consequence_goal() returns True.
                # If evaluator returns verdict=revise → emit ErrorEvent
                # ("evaluator_revise") instead of FinalEvent so auto_resume
                # can spawn a replan. Runs at most ONCE per goal (here,
                # before FinalEvent — revise → replan → new run, no loop).
                # flag off (external_evaluator=None) → skip entirely (BC, 0 calls).
                if self.external_evaluator is not None:
                    try:
                        from deskpet.agent.external_evaluator import (  # noqa: PLC0415
                            is_high_consequence_goal as _is_hcg,
                        )
                        _eval_ledger = (
                            self.receipt_store.load_session(session_id)
                            if self.receipt_store is not None else []
                        )
                        # Extract goal_text from session_goal_store if wired,
                        # else fall back to first user message text.
                        _eval_goal_text = ""
                        if self.session_goal_store is not None:
                            _gt_fn = getattr(
                                self.session_goal_store, "get_goal_text", None
                            )
                            if callable(_gt_fn):
                                _eval_goal_text = _gt_fn(session_id) or ""
                        if not _eval_goal_text:
                            # BC fallback: use first user message content
                            for _m in working_messages:
                                if _m.get("role") == "user":
                                    _eval_goal_text = str(_m.get("content") or "")
                                    break
                        if _is_hcg(_eval_goal_text, _eval_ledger, []):
                            _ev_result = await self.external_evaluator.evaluate(
                                original_goal=_eval_goal_text,
                                produced_artifacts=[
                                    getattr(r, "tool_name", "unknown")
                                    for r in _eval_ledger
                                ],
                                objective_evidence=[
                                    f"receipt ok: tool={getattr(r, 'tool_name', '?')}"
                                    for r in _eval_ledger if getattr(r, "ok", True)
                                ],
                                conversation_summary=str(response.content or "")[:512],
                            )
                            if (
                                _ev_result.get("verdict") == "revise"
                                and _ev_result.get("quality_score", 10) < 6
                            ):
                                logger.info(
                                    "external_evaluator verdict=revise sid=%s "
                                    "quality_score=%d issues=%d → emit evaluator_revise",
                                    session_id,
                                    _ev_result["quality_score"],
                                    len(_ev_result.get("issues", [])),
                                )
                                try:
                                    from observability.metrics_sink import (  # noqa: PLC0415
                                        record as _eval_metric,
                                    )
                                    _eval_metric("evaluator_revise_triggered", {
                                        "quality_score": _ev_result["quality_score"],
                                        "issues_count": len(_ev_result.get("issues", [])),
                                    })
                                except Exception:  # noqa: BLE001 — metric 失败不阻
                                    pass
                                yield ErrorEvent(
                                    type="error",
                                    task_id=tid,
                                    iteration=iteration,
                                    reason="evaluator_revise",
                                    detail=(
                                        f"[external_evaluator] 质量不足 "
                                        f"(score={_ev_result['quality_score']}/10): "
                                        + "; ".join(_ev_result.get("issues", []))
                                    ),
                                )
                                return
                    except Exception as exc:  # noqa: BLE001 — safe-fail
                        logger.warning(
                            "external_evaluator gate failed (sid=%s): %s — passing through",
                            session_id, exc,
                        )

                # P6 Phase 6 — gate records the natural terminal state
                # before we emit the FinalEvent so consumers reading
                # gate.summary() after a run see SUCCESS / matching reason.
                self._gate.record_final_answer()

                yield FinalEvent(
                    type="final",
                    task_id=tid,
                    iteration=iteration,
                    content=response.content,
                    reasoning_content=response.reasoning_content,
                    stop_reason=response.stop_reason or "end_turn",
                    total_input_tokens=totals["input"],
                    total_output_tokens=totals["output"],
                    total_cache_read_tokens=totals["cache_read"],
                    total_cache_write_tokens=totals["cache_write"],
                )
                return

            # P5-S2 Phase 3.3: same-(name, args) repeat detection. If
            # the activity store reports the same signature already ran
            # ``_REPEAT_THRESHOLD - 1`` times in a row, dispatching the
            # same call a Nth time can't change the result. Inject a
            # system nudge and skip dispatch for THIS turn — the LLM
            # gets the nudge on the next iteration and can change tactics.
            #
            # Check happens BEFORE we append the assistant turn so the
            # rejected tool_call doesn't pollute the conversation either.
            if self.activity_store is not None:
                from agent.session_activity import args_hash as _args_hash  # noqa: PLC0415

                repeat_hit: tuple[str, int] | None = None  # (tool_name, count)
                sa = await self.activity_store.get(session_id)
                if sa is not None:
                    sig_window = dict(sa.tool_signature_window)
                    for tc in response.tool_calls:
                        sig = f"{tc.name}:{_args_hash(tc.arguments)}"
                        prior = int(sig_window.get(sig, 0))
                        if prior >= (self._signature_repeat_threshold - 1):
                            repeat_hit = (tc.name, prior + 1)
                            break

                if repeat_hit is not None:
                    name, count = repeat_hit
                    nudge = _REPEAT_NUDGE_MSG.format(name=name, count=count)
                    # Append the assistant content (so the LLM can see
                    # what it just said) plus the system nudge. Do NOT
                    # append the broken tool_calls — we're suppressing
                    # the dispatch entirely.
                    if response.content:
                        working_messages.append({
                            "role": "assistant",
                            "content": response.content,
                        })
                    working_messages.append({
                        "role": "system",
                        "content": nudge,
                    })
                    logger.info(
                        "p5s2_signature_repeat_nudge sid=%s tid=%s iter=%d "
                        "name=%s count=%d",
                        session_id, tid, iteration, name, count,
                    )
                    # Skip dispatch and re-iterate — next LLM call sees
                    # the nudge and can change tactics. NOTE: we don't
                    # bump completion_nudges_used (this is a different
                    # nudge mechanism) and we don't yield a ToolCallEvent
                    # for the suppressed call (it never happened from
                    # the dispatch layer's perspective).
                    continue

            # Append assistant turn with tool_calls so next LLM turn sees it.
            # OpenAI requires arguments to be a JSON STRING, not a dict.
            # Some adapters (anthropic_adapter) want the dict form. We
            # encode here as a string since the OpenAI-compat path is
            # the most common downstream.
            import json as _json_at
            asst_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _json_at.dumps(
                                tc.arguments, ensure_ascii=False
                            ),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            # P4-S24: round-trip reasoning_content for thinking-mode
            # models. DeepSeek V4 Pro / Qwen3 thinking / GLM-4.5 etc.
            # reject the next request with HTTP 400 if a prior
            # assistant turn that HAD a reasoning_content arrives
            # without it. Skip the field entirely for non-thinking
            # models (empty string) so we don't pollute payloads to
            # plain Ollama / GPT-4o.
            if response.reasoning_content:
                asst_msg["reasoning_content"] = response.reasoning_content
            working_messages.append(asst_msg)

            # Dispatch all tools concurrently (spec §11.9).
            tool_coros = []
            call_order: list[ToolCall] = []
            for tc in response.tool_calls:
                # P6 Phase 6 — gate gates each tool dispatch on hard
                # budget + per-tool consecutive cap (hallucination
                # detection). Returning early on the FIRST blocked tool
                # is the bug-fix: old soft-message path kept iterating
                # to max_iterations even after the budget was busted
                # (the "not convergent" bug). Tools that were already
                # appended to tool_coros above keep running concurrently;
                # we just stop scheduling new ones and exit.
                _tok, _treason = self._gate.allows_tool(tc.name)
                if not _tok:
                    # Flush any tools already scheduled so we still
                    # honour the per-iteration "yield tool_result
                    # for each concurrent call" invariant.
                    if tool_coros:
                        _flushed = await asyncio.gather(
                            *tool_coros, return_exceptions=True
                        )
                        for _ftc, _fres in zip(call_order, _flushed):
                            if isinstance(_fres, BaseException):
                                import json as _json_flush
                                _fres_str = _json_flush.dumps(
                                    {
                                        "error": f"{type(_fres).__name__}: {_fres}",
                                        "retriable": False,
                                    },
                                    ensure_ascii=False,
                                )
                            else:
                                _fres_str = _fres
                            yield ToolResultEvent(
                                type="tool_result",
                                task_id=tid,
                                iteration=iteration,
                                tool_call_id=_ftc.id,
                                tool_name=_ftc.name,
                                result=_fres_str,
                            )
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason=(
                            _treason.value
                            if _treason is not None else "unknown"
                        ),
                        detail=f"Termination gate blocked tool {tc.name}",
                    )
                    return
                # P6 bugfix 2026-05-13: pass args so the per-tool consecutive
                # counter is args-aware (5 reads of FIVE DIFFERENT files no
                # longer trigger HALLUCINATION_DETECTED — only repeating the
                # same path triggers).
                self._gate.record_tool_call(tc.name, args=tc.arguments)
                # WI-4.2 skill remount: record skill_invoke calls so that
                # _remount_skills() can re-inline their bodies after compaction.
                if tc.name == "skill_invoke" and self.skill_loader is not None:
                    _sname = None
                    try:
                        _sargs = tc.arguments
                        if isinstance(_sargs, dict):
                            _sname = _sargs.get("skill_name")
                        elif isinstance(_sargs, str):
                            import json as _json_sk
                            _sargs_d = _json_sk.loads(_sargs)
                            _sname = _sargs_d.get("skill_name")
                    except Exception:  # noqa: BLE001 — never block dispatch
                        pass
                    if _sname and _sname not in self._skills_used_this_run:
                        self._skills_used_this_run.add(_sname)
                        self._skills_used_order.append(_sname)
                # P6 Phase 6 — tools_used_count was the legacy soft-cap
                # counter; the gate now tracks tools_used in its state.
                # Kept as a local var for the (still-active) soft selfcheck
                # messages below.
                tools_used_count += 1
                yield ToolCallEvent(
                    type="tool_call",
                    task_id=tid,
                    iteration=iteration,
                    tool_call=tc,
                )
                tool_coros.append(self._dispatch_tool(tc, tid, session_id))
                call_order.append(tc)

            results = await asyncio.gather(*tool_coros, return_exceptions=True)

            # P5-S2 Phase 2: classify each tool result. If ANY is a
            # PermanentToolError or HallucinationError, break out after
            # yielding every tool_result event (downstream persistence
            # + supervisor diagnosis still need them) — but DO NOT
            # iterate again. Saves up to ``max_iterations - 1`` wasted
            # LLM round-trips when the LLM keeps invoking the same
            # broken tool_call (vpn-tunnel bug 2026-05-10).
            permanent_break: tuple[str, str] | None = None  # (reason, detail)

            for tc, result in zip(call_order, results):
                if isinstance(result, BaseException):
                    # _dispatch_tool already normalizes most exceptions; this
                    # is a defense-in-depth catch for anything that slipped past.
                    import json as _json  # local import: rarely used path

                    result_str = _json.dumps(
                        {"error": f"{type(result).__name__}: {result}", "retriable": False},
                        ensure_ascii=False,
                    )
                else:
                    result_str = result
                yield ToolResultEvent(
                    type="tool_result",
                    task_id=tid,
                    iteration=iteration,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    result=result_str,
                )
                # P6 Phase 6 — delegate truncation to
                # self._ctx.record_tool_result. The skip_truncation_for_tools
                # set (G1 fix) is honoured here, so a fetch_tool_result
                # round-trip returns the full body. Long tool_result bodies
                # are replaced inline with a "[truncated, ref_id=X]" marker
                # and the full body is kept in the global ref store —
                # same content is re-sent on every iteration, so a single
                # 60 KB read_file × 30 iterations = 1.8 MB context bloat.
                # Original result_str stays in the ToolResultEvent above
                # so the UI sees the full body.
                _content_for_history, _trunc_ref = self._ctx.record_tool_result(
                    tool_name=tc.name, result=result_str,
                )
                if _trunc_ref is not None:
                    logger.info(
                        "p5s2_tool_result_truncated sid=%s tool=%s "
                        "orig_len=%d kept_len=%d ref_id=%s",
                        session_id, tc.name, len(result_str),
                        len(_content_for_history), _trunc_ref,
                    )
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": _content_for_history,
                    }
                )

                # P5-S2 Phase 2: classify *this* tool_result.
                _tp_err_class = None
                if permanent_break is None:
                    err_class = _classify_tool_result(result_str)
                    _tp_err_class = err_class
                    if err_class is agent_errors.PermanentToolError:
                        permanent_break = (
                            "permanent_tool_error",
                            _extract_break_detail(result_str, tc.name),
                        )
                    elif err_class is agent_errors.HallucinationError:
                        permanent_break = (
                            "hallucination",
                            _extract_break_detail(result_str, tc.name),
                        )

                # WI-1.6 录工具路径喂 FP-5 技能自创（recorder=None → no-op，BC）。
                # ok = 非异常 且 未被分类为永久/幻觉错误。safe-fail，永不阻断派发。
                if self.tool_path_recorder is not None:
                    try:
                        _tp_ok = not isinstance(result, BaseException) and _tp_err_class not in (
                            agent_errors.PermanentToolError,
                            agent_errors.HallucinationError,
                        )
                        self.tool_path_recorder.record_tool(
                            session_id, name=tc.name, ok=_tp_ok,
                        )
                    except Exception:  # noqa: BLE001 — never block dispatch
                        pass

            if permanent_break is not None:
                reason, detail = permanent_break
                logger.info(
                    "p5s2_tool_error_classified sid=%s tid=%s iter=%d "
                    "reason=%s detail=%s",
                    session_id, tid, iteration, reason, detail[:200],
                )
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason=reason,
                    detail=detail,
                )
                return

        # Hit max_iterations — spec §11.4 says emit warning + final.
        logger.warning("agent loop task %s hit max_iterations=%d", tid, self.max_iterations)
        yield ErrorEvent(
            type="error",
            task_id=tid,
            iteration=self.max_iterations,
            reason="max_iterations",
            detail=f"exceeded {self.max_iterations} iterations without terminal stop_reason",
        )

    # ------------------------------------------------------------------
    # WI-4.2 — post-compaction skill body remount
    # ------------------------------------------------------------------

    _REMOUNT_MARKER = "[已重挂技能 / remounted skills]"
    _REMOUNT_TOKEN_BUDGET = 25_000  # chars (proxy for tokens; 1 token ≈ 4 chars)

    def _remount_skills(
        self,
        messages: list[dict],
        session_id: str,  # noqa: ARG002 — reserved for future per-session skill store
    ) -> list[dict]:
        """Re-inline skill bodies into a single role=system block after compaction.

        Algorithm
        ---------
        1. Gather skills to remount:
           - skills tracked via ``_skills_used_this_run`` (from ``skill_invoke`` calls).
           - (future: SkillMatcher strong-match set, when matcher available)
        2. Order by recency of use (``_skills_used_order`` insertion order, most-recent
           last → iterate reversed for MRU-first priority).
        3. Re-read bodies via ``skill_loader.read_body(name)`` up to 25K char budget.
           Skills that exceed remaining budget are dropped (LRU-drop = drop oldest).
           Skills whose loader raises KeyError / IOError are skipped gracefully.
        4. Build one role=system block with ``_REMOUNT_MARKER`` as header.
           BEFORE inserting, remove any existing ``[已重挂技能]`` block (prevents
           pile-up across repeated compactions).
        5. No skills used / no bodies loaded → return messages unchanged (no-op).
        6. skill_loader is None → no-op (BC).
        """
        # BC: no loader injected → nothing to do
        if self.skill_loader is None:
            return messages

        # Gather names to remount.  Primary source: in-run skill_invoke tracking.
        # Prefer the ordered list (preserves recency); fall back to the set for
        # cases where a test (or external code) only sets _skills_used_this_run.
        _order_attr = getattr(self, "_skills_used_order", [])
        _set_attr: set[str] = getattr(self, "_skills_used_this_run", set())
        if _order_attr:
            names_to_remount: list[str] = list(_order_attr)
        elif _set_attr:
            # No ordering info — use the set in arbitrary order.
            names_to_remount = list(_set_attr)
        else:
            names_to_remount = []
        # Deduplicate while preserving order (shouldn't be needed given how we
        # build the list, but be defensive).
        seen: set[str] = set()
        ordered: list[str] = []
        for n in names_to_remount:
            if n not in seen:
                seen.add(n)
                ordered.append(n)

        # Future: merge SkillMatcher strong-match names here (WI-4.2 §3.1 note).
        # When self.skill_matcher is available, call matcher.match(last_user_query)
        # and union with ``ordered`` (placing matcher hits at the front / MRU).

        if not ordered:
            # No skills used this run → no-op
            return messages

        # Load bodies, most-recently-used first, up to budget.
        # ``ordered`` is insertion-order (oldest first); reverse for MRU priority.
        budget_remaining = self._REMOUNT_TOKEN_BUDGET
        sections: list[str] = []
        # We iterate MRU-first so that the most-recent skills get budget priority.
        for name in reversed(ordered):
            try:
                body = self.skill_loader.read_body(name)
            except (KeyError, OSError, IOError):
                # Skill not found or file unreadable → skip gracefully
                logger.debug(
                    "skill_remount.skip_unreadable sid=%s name=%s",
                    session_id, name,
                )
                continue
            except Exception:  # noqa: BLE001
                continue

            section = f"### {name}\n{body}"
            if len(section) > budget_remaining:
                # Would exceed budget — skip this skill (LRU-drop: since we're
                # iterating MRU-first, any overflow here means we drop the older
                # skill in subsequent loop iterations — but we still try to fit
                # shorter ones after this one).  A simpler "first-fit" approach:
                # just skip and keep trying (smaller bodies may still fit).
                logger.debug(
                    "skill_remount.budget_skip sid=%s name=%s body_len=%d remaining=%d",
                    session_id, name, len(section), budget_remaining,
                )
                continue
            sections.append(section)
            budget_remaining -= len(section)

        if not sections:
            # All skills either skipped or over-budget → no-op
            return messages

        # Build the remount block.  Sections were appended MRU-first; reverse
        # so the block reads oldest→newest (more natural reading order).
        sections.reverse()
        remount_content = (
            f"{self._REMOUNT_MARKER}\n\n"
            + "\n\n".join(sections)
        )
        remount_block: dict = {"role": "system", "content": remount_content}

        # Remove any existing remount block (single-block invariant — prevents pile-up
        # across repeated compactions).
        cleaned = [
            m for m in messages
            if not (
                m.get("role") == "system"
                and self._REMOUNT_MARKER in (m.get("content") or "")
            )
        ]

        # Insert the new remount block.  Place it after the last existing system
        # message (usually the skill_prelude / persona block) so context order is:
        #   [system: skill_prelude] … [system: remount] … [user/assistant messages]
        last_system_idx = -1
        for idx, m in enumerate(cleaned):
            if m.get("role") == "system":
                last_system_idx = idx
        insert_at = last_system_idx + 1

        result = cleaned[:insert_at] + [remount_block] + cleaned[insert_at:]

        logger.info(
            "skill_remounted sid=%s names=%s budget_used=%d",
            session_id,
            [s.split("\n")[0].replace("### ", "") for s in sections],
            self._REMOUNT_TOKEN_BUDGET - budget_remaining,
        )
        return result

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool(
        self, tc: ToolCall, task_id: str, session_id: str = "default"
    ) -> str:
        """Call tool_registry to run a tool with graceful error shaping.

        P4-S20 routing:
          - v2 registry (has ``execute_tool``) → permission-gated async
            path. Returns ``{"ok", "result", "error"}`` envelope as JSON
            string so the LLM tool_result turn sees structured outcome
            (and can apologize on permission denial instead of
            hallucinating success).
          - legacy registry → ``dispatch()`` per spec §5 error contract.
        """
        import json as _json
        # P5-S2 (2026-05-11) malformed-args short-circuit. The provider
        # populates ``args_parse_error`` + ``args_raw`` when the model's
        # JSON args couldn't be parsed (deepseek-v4-pro mis-escaping
        # \n / " / \\ in long markdown content is the canonical case).
        # Don't dispatch — the tool would just see empty args and
        # complain about missing fields, the LLM would retry with the
        # same broken JSON, and the circuit breaker would eventually
        # fire. Instead, hand back a structured error tool_result that
        # tells the model exactly what was malformed so it can
        # regenerate with valid JSON.
        if tc.args_parse_error:
            args_raw = tc.args_raw or ""
            preview = args_raw[:300]
            if len(args_raw) > 300:
                preview = preview + "…"
            logger.warning(
                "p5s2_dispatch_short_circuit_malformed_args "
                "tool=%s args_len=%d parse_error=%s",
                tc.name, len(args_raw), tc.args_parse_error[:200],
            )
            # P6 bugfix 2026-05-14 (live-test): when args are LONG
            # (>3KB) and the parse error is "Unterminated string", the
            # actual cause is almost always **output truncation by the
            # LLM proxy's max_tokens**, NOT escape mishaps. The model
            # didn't finish generating the JSON. Telling it to "fix
            # escapes" is misleading — it'll regenerate the same too-
            # long output and fail again. Instead tell it to shorten:
            # write the file in smaller chunks (use edit_file's
            # incremental mode, or write a stub then iterate).
            is_truncation = (
                len(args_raw) > 3000
                and "Unterminated string" in (tc.args_parse_error or "")
            )
            if is_truncation:
                hint_text = (
                    f"你刚发的 tool_call.arguments 太长 ({len(args_raw)} 字符) 被 LLM "
                    "输出 token 上限截断了，JSON 不完整无法解析。**不要重试同样的 "
                    "tool_call** —— 同样会再被截断。请改用以下任一策略：\n"
                    "1) write_file 一次只写不超过 3000 字符（约 80 行代码），"
                    "如果文件大就分多次：先 write_file 写主结构+ TODO 注释，再 "
                    "用 edit_file/write_file 多次追加补完。\n"
                    "2) 把大文件拆成多个小文件（按职责分组件 / hook / util），"
                    "每个文件 < 80 行。\n"
                    "3) 如果只是修改局部，用 edit_file 而非 write_file 整覆盖。"
                )
            else:
                hint_text = (
                    f"你刚发的 tool_call.arguments 不是合法 JSON: "
                    f"{tc.args_parse_error}. 你写了 {len(args_raw)} 字符的 args, "
                    "但解析失败。最常见原因：长字符串里 \\n / \\\" / \\\\ "
                    "没正确转义。请重新生成同一个 tool_call，"
                    "确保 JSON 严格合法（特别是 multi-line content 字段）。"
                )
            return _json.dumps(
                {
                    "ok": False,
                    "error": (
                        "tool_call_args_truncated_by_max_tokens"
                        if is_truncation
                        else "tool_call_args_malformed_json"
                    ),
                    "hint": hint_text,
                    "tool": tc.name,
                    "args_raw_preview": preview,
                    "parse_error": tc.args_parse_error,
                    "args_len": len(args_raw),
                    "likely_cause": (
                        "max_tokens_truncation"
                        if is_truncation
                        else "escape_error"
                    ),
                },
                ensure_ascii=False,
            )
        try:
            if self._supports_execute_tool:
                envelope = await self.tools.execute_tool(  # type: ignore[attr-defined]
                    tc.name, tc.arguments, session_id, task_id
                )
                # Pass through envelope to LLM as JSON. Tools that
                # succeeded already encoded their domain result inside
                # `result` (typically a JSON string); we don't
                # double-encode.
                return _json.dumps(envelope, ensure_ascii=False)
            result = self.tools.dispatch(tc.name, tc.arguments, task_id)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            return _json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "retriable": False,
                },
                ensure_ascii=False,
            )
        if isinstance(result, (dict, list)):
            return _json.dumps(result, ensure_ascii=False)
        return str(result)


# Runtime AgentEvent union type for callers that want isinstance checks.
AgentEventUnion = Union[
    AssistantMessageEvent,
    ToolCallEvent,
    ToolResultEvent,
    FinalEvent,
    ErrorEvent,
]
