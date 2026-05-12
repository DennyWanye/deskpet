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
# Plus: a separate hard cap on TOOL CALLS per session (counted across
# iterations, regardless of iteration index). When >= _TOOL_BUDGET,
# next iteration injects a "you've used your tool budget" forced stop.

_SELFCHECK_EVERY = 10  # base interval — tier 1 fires here
_SELFCHECK_TIER2_AT = 20  # iter >= this → escalate
_SELFCHECK_TIER3_AT = 30  # iter >= this → forced stop
_TOOL_BUDGET_SOFT = 25  # gentle warning when used
_TOOL_BUDGET_HARD = 40  # forced stop when used

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

_TOOL_BUDGET_HARD_MSG = (
    "[TOOL BUDGET EXHAUSTED — 已用 {tools_used} 次工具调用]\n"
    "你已经超过工具调用硬上限 ({hard_cap} 次)。立即停止任何新的 tool_call。\n"
    "下一轮回复必须是 stop_reason=end_turn，用文字总结当前所有成果给用户。"
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
        # P5-S2 B1: shared global tool-result-ref store. Long tool_results
        # are replaced inline with a "[truncated, ref_id=X]" marker and
        # the full body is kept in the module singleton — the same one
        # the ``fetch_tool_result`` tool reads from when the LLM wants
        # to retrieve the original. Cross-AgentLoop sharing is safe
        # because ref_ids are 8-char random tokens; LRU caps memory.
        from agent.tool_result_truncator import get_global_ref_store
        self._tool_result_refs = get_global_ref_store()

        # P6 Phase 3 — TerminationGate plumbing (feature-flagged).
        #
        # Three cases:
        #   1. Caller injects an explicit gate → use it.
        #   2. P6_ENABLE_GATE truthy → build a default gate with
        #      max_turns = self.max_iterations.
        #   3. Otherwise → self._gate = None (legacy path, unchanged
        #      behaviour for the existing 1196-test baseline).
        #
        # The legacy soft selfcheck / soft tool-budget messages
        # (_TOOL_BUDGET_HARD / _SELFCHECK_*) are kept in place — they are
        # complementary to the gate, not replaced by it. When the flag
        # is on, the gate provides HARD termination semantics on top of
        # those nudges.
        if termination_gate is not None:
            self._gate: Optional[TerminationGate] = termination_gate
        else:
            try:
                from config import is_p6_gate_enabled as _flag_check
                _flag_on = _flag_check()
            except Exception:  # noqa: BLE001 — defensive: never break import
                _flag_on = False
            if _flag_on:
                self._gate = TerminationGate(
                    GateConfig(max_turns=self.max_iterations)
                )
            else:
                self._gate = None

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
        # P5-S2 Phase 7: track total tool calls for hard-cap enforcement.
        # Each ToolCallEvent yield bumps this. When >= _TOOL_BUDGET_HARD
        # the loop injects a forced-stop system message.
        tools_used_count = 0

        # P5-S2 B3: warn-once latch so we don't spam the WARN log every
        # iteration once we're in the 80-95% band.
        _budget_warn_emitted = False

        for iteration in range(1, self.max_iterations + 1):
            # P6 Phase 3 — TerminationGate.allows_call() (feature-flagged).
            # Checks hard limits (turns, wall-clock, cost) BEFORE we burn
            # another LLM call. The gate is the single source of truth
            # for "should this loop keep going?" when enabled.
            if self._gate is not None:
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
            try:
                from agent.token_budget import (
                    check_budget as _check_budget,
                    BudgetCheck as _BudgetCheck,
                )
                # Use the FIRST provider's model as the context-window
                # reference (chain mode). In legacy single-provider mode
                # fall back to default_model.
                _budget_model = use_model
                if chain_mode and provider_chain:
                    _first = provider_chain[0]
                    _budget_model = getattr(_first, "model", None) or use_model
                _budget = _check_budget(working_messages, model=_budget_model)
                if _budget.verdict is _BudgetCheck.BLOCK:
                    logger.error(
                        "p5s2_token_budget_block sid=%s tid=%s iter=%d "
                        "tokens=%d window=%d ratio=%.2f",
                        session_id, tid, iteration,
                        _budget.estimated_tokens, _budget.context_window,
                        _budget.ratio,
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

            # P5-S2 Phase 7: escalating in-loop self-check + tool budget.
            # Two independent triggers, each can inject a system message:
            #   (a) iteration-based self-check (every _SELFCHECK_EVERY rounds)
            #       → tier escalates based on iteration count
            #   (b) tool budget hard cap (when total tool calls used so far
            #       crosses _TOOL_BUDGET_HARD)
            # Both inject as system messages BEFORE the next LLM call.
            # The HARD tool-budget message takes priority — it's the
            # strongest stop signal.

            # Count tools used so far in this loop run (tracked above per
            # iteration). `tools_used` is bumped at each ToolCallEvent.
            tools_used = locals().get("tools_used_count", 0)

            if tools_used >= _TOOL_BUDGET_HARD:
                working_messages.append({
                    "role": "system",
                    "content": _TOOL_BUDGET_HARD_MSG.format(
                        tools_used=tools_used,
                        hard_cap=_TOOL_BUDGET_HARD,
                    ),
                })
                logger.warning(
                    "p5s2_tool_budget_exhausted sid=%s tid=%s iter=%d tools_used=%d",
                    session_id, tid, iteration, tools_used,
                )
            elif iteration > 0 and iteration % _SELFCHECK_EVERY == 0:
                budget_left = self.max_iterations - iteration
                msg = _build_selfcheck_message(iteration, self.max_iterations, tools_used)
                working_messages.append({"role": "system", "content": msg})
                tier = (
                    3 if iteration >= _SELFCHECK_TIER3_AT
                    else 2 if iteration >= _SELFCHECK_TIER2_AT
                    else 1
                )
                logger.info(
                    "p5s2_selfcheck_injected sid=%s tid=%s iter=%d budget=%d "
                    "tier=%d tools_used=%d",
                    session_id, tid, iteration, budget_left, tier, tools_used,
                )

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
                                max_tokens=int(llm_kwargs.get("max_tokens", 2048)),
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
                        # P6 Phase 3 — record terminal error reason on the
                        # gate so callers reading summary() see the real
                        # cause (otherwise the gate would stay "running").
                        if self._gate is not None:
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
                        # 3-in-a-row from chinzy). Fall back to the non-
                        # streaming path instead of bubbling the error
                        # to the user — non-stream is more reliable on
                        # chinzy in our observed traffic, AND it has its
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
                            # Empty-stream case (chinzy didn't actually stream).
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
                )
                return

            totals["input"] += response.usage.input_tokens
            totals["output"] += response.usage.output_tokens
            totals["cache_read"] += response.usage.cache_read_tokens
            totals["cache_write"] += response.usage.cache_write_tokens

            # P6 Phase 3 — record the turn (advances turns_used and
            # optionally adds to cost_usd if the response carries a
            # cost_usd extra; ChatUsage has no such field today so we
            # pass 0.0). The gate's allows_call check at top of next
            # iteration uses turns_used to decide whether to keep going.
            if self._gate is not None:
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

                # P6 Phase 3 — gate records the natural terminal state
                # before we emit the FinalEvent so consumers reading
                # gate.summary() after a run see SUCCESS / matching reason.
                if self._gate is not None:
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
                # P6 Phase 3 — gate gates each tool dispatch on hard
                # budget + per-tool consecutive cap (hallucination
                # detection). Returning early on the FIRST blocked tool
                # is the bug-fix: old soft-message path kept iterating
                # to max_iterations even after the budget was busted
                # (the "not convergent" bug). Tools that were already
                # appended to tool_coros above keep running concurrently;
                # we just stop scheduling new ones and exit.
                if self._gate is not None:
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
                    self._gate.record_tool_call(tc.name)
                # P5-S2 Phase 7: bump tool budget counter — checked at top
                # of next iteration to enforce hard cap.
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
                # P5-S2 B1: truncate long tool_result bodies before they
                # land in working_messages — same content is re-sent on
                # every iteration, so a single 60 KB read_file × 30
                # iterations = 1.8 MB context bloat. The truncated form
                # keeps head + tail visible + a ref_id the LLM can
                # fetch later. Original result_str stays in the
                # ToolResultEvent above so the UI sees the full body.
                from agent.tool_result_truncator import (
                    maybe_truncate_tool_result as _maybe_truncate,
                )
                _content_for_history, _trunc_ref = _maybe_truncate(
                    result_str, store=self._tool_result_refs
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
                if permanent_break is None:
                    err_class = _classify_tool_result(result_str)
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
            return _json.dumps(
                {
                    "ok": False,
                    "error": "tool_call_args_malformed_json",
                    "hint": (
                        f"你刚发的 tool_call.arguments 不是合法 JSON: "
                        f"{tc.args_parse_error}. 你写了 {len(args_raw)} 字符的 args, "
                        "但解析失败。最常见原因：长字符串里 \\n / \\\" / \\\\ "
                        "没正确转义。请重新生成同一个 tool_call，"
                        "确保 JSON 严格合法（特别是 multi-line content 字段）。"
                    ),
                    "tool": tc.name,
                    "args_raw_preview": preview,
                    "parse_error": tc.args_parse_error,
                    "args_len": len(args_raw),
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
