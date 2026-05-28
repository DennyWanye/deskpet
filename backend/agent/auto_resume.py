# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 4: AutoResumeOrchestrator — closes the supervisor → main-agent loop.

When the agent loop fails recoverably (max_iterations / circuit_open /
permanent_tool_error / hallucination), the orchestrator:

1. Checks config gate (``auto_resume_enabled``); if off → escalate to
   user popup via the existing P5-S1 supervisor_alert path.
2. Checks the per-session attempt counter; if at ``max_attempts`` →
   emit ``auto_resume_exhausted`` ws event and stop.
3. Calls ``supervisor.diagnose(sid, snapshot)`` for an LLM-driven hint.
4. If supervisor returns ``action="nudge"`` with a hint, **spawn a
   fresh chat task** on the same sid with the hint injected as a system
   message — no user input required.
5. If supervisor returns ``action="ask_user"``, fall through to the
   existing popup path (don't increment counter — let user decide).

The orchestrator is **stateless across processes** — attempt counters
live on ``SessionActivityStore`` (in-memory, restart-clean). The
``ws_emitter`` and ``audit_writer`` callables are injected so the
orchestrator stays decoupled from main.py wiring.

ws events emitted (per spec):
  * ``auto_resume_started``: ``{session_id, attempt, hint_preview}``
  * ``auto_resume_exhausted``: ``{session_id, final_error, attempts}``

Note ``auto_resume_succeeded`` is emitted by **main.py** when the
spawned task's ``FinalEvent`` fires — orchestrator never knows the new
task succeeded (fire-and-forget by design).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover — import only for typing
    from agent.session_activity import SessionActivityStore
    from agent.supervisor import SupervisorAgent

logger = logging.getLogger("deskpet.agent.auto_resume")


# ─────────────── result dataclass ───────────────


@dataclass
class AutoResumeResult:
    """Outcome of one ``handle_failure`` call.

    * ``action="spawned"``    — a fresh chat task was kicked off with the
      supervisor's hint injected; ``attempt`` is the new counter value
      (1-based), ``hint`` is the hint text.
    * ``action="ask_user"``    — supervisor decided ask_user OR config
      disabled — caller should let the existing popup path handle it.
      ``reason`` carries the rationale string for telemetry.
    * ``action="exhausted"``   — already at max_attempts; orchestrator
      emitted ``auto_resume_exhausted`` and gave up. Caller should
      surface the original error to the user.
    """

    action: str  # "spawned" | "ask_user" | "exhausted"
    attempt: int = 0
    hint: str = ""
    reason: str = ""


# ─────────────── callable signatures ───────────────


# Dispatch a fresh chat task on this sid with the given message stack.
# The dispatcher is responsible for starting the AgentLoop and emitting
# all the usual chat ws events (delta / tool_call / final / error). The
# orchestrator awaits the dispatcher only long enough for it to start
# the task — actual completion is fire-and-forget.
ChatDispatcher = Callable[[str, list[dict]], Awaitable[None]]

# (event_type, payload) — same shape as supervisor's broadcast.
WsEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]

# Audit row writer — orchestrator writes one row per spawn with
# ``action='auto_resumed'``. The closure adapts to SessionDB's
# ``append_supervisor_hint`` keyword signature.
AuditWriter = Callable[[dict[str, Any]], Awaitable[None]]


# ─────────────── trigger reasons that auto-resume handles ───────────────


# Reasons the chat handler will forward to the orchestrator. Anything
# outside this set falls through to the existing chat_v2_error path
# untouched.
_AUTO_RESUME_TRIGGER_REASONS: frozenset[str] = frozenset({
    "max_iterations",
    "permanent_tool_error",
    "circuit_open",
    "hallucination",
})


def is_auto_resume_trigger(reason: str) -> bool:
    """True iff a given ErrorEvent.reason should be routed to orchestrator."""
    return (reason or "").strip() in _AUTO_RESUME_TRIGGER_REASONS


# ─────────────── orchestrator ───────────────


class AutoResumeOrchestrator:
    """Closes the supervisor → main-agent retry loop.

    Constructor args are all keyword-only so wiring at main.py stays
    obvious and doesn't accidentally pass things in the wrong order.
    """

    def __init__(
        self,
        *,
        supervisor: "SupervisorAgent",
        chat_dispatcher: ChatDispatcher,
        activity_store: "SessionActivityStore",
        max_attempts: int = 2,
        enabled: bool = True,
        ws_emitter: Optional[WsEmitter] = None,
        audit_writer: Optional[AuditWriter] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._supervisor = supervisor
        self._dispatch = chat_dispatcher
        self._activity = activity_store
        self._max_attempts = max(1, int(max_attempts))
        self._enabled = bool(enabled)
        self._emit = ws_emitter
        self._audit = audit_writer
        self._clock = clock

    # Useful for runtime config hot-swap (Phase 6 will wire the toggle).
    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    async def handle_failure(
        self,
        sid: str,
        reason: str,
        snapshot: dict[str, Any],
        original_msgs: list[dict],
    ) -> AutoResumeResult:
        """Decide what to do about a recoverable failure on ``sid``.

        Never raises — all internal failures are logged + degrade to
        ``ask_user`` so the user always gets *some* feedback.
        """
        if not self._enabled:
            logger.info("auto_resume_skipped sid=%s reason=disabled", sid)
            return AutoResumeResult(action="ask_user", reason="disabled")

        # 1) Check current attempt counter BEFORE doing anything expensive.
        sa = await self._safe_get_activity(sid)
        attempts_so_far = sa.auto_resume_attempts if sa is not None else 0
        if attempts_so_far >= self._max_attempts:
            await self._emit_exhausted(sid, snapshot, attempts_so_far)
            return AutoResumeResult(
                action="exhausted",
                attempt=attempts_so_far,
                reason=f"max_attempts={self._max_attempts}_reached",
            )

        # 2) Ask supervisor. Supervisor.diagnose never raises — but defend.
        try:
            sup_action = await self._supervisor.diagnose(sid, snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_resume_supervisor_call_failed sid=%s err=%s", sid, exc)
            return AutoResumeResult(
                action="ask_user",
                attempt=attempts_so_far,
                reason=f"supervisor_failed:{type(exc).__name__}",
            )

        # 3) Supervisor said ask_user → don't auto-spawn, don't bump.
        if sup_action.action == "ask_user":
            # 2026-05-15: 此前是静默 return，事后看 log 无法解释
            # "为啥 max_iter 触发了 auto_resume 但没 spawn" —— 因为
            # supervisor 明确判了 ask_user，需要人介入。落 INFO 日志。
            logger.info(
                "auto_resume_skipped sid=%s reason=supervisor_decided_ask_user "
                "attempts=%d snapshot_reason=%s",
                sid, attempts_so_far, snapshot.get("reason", ""),
            )
            return AutoResumeResult(
                action="ask_user",
                attempt=attempts_so_far,
                reason="supervisor_decided_ask_user",
            )

        # 4) Supervisor said wait/cancel/etc — degrade to popup path too.
        if sup_action.action != "nudge" or not sup_action.hint_for_main_agent:
            # 同样落日志：supervisor 给了非 nudge 决策或 nudge 但没 hint，
            # auto_resume 无法 spawn，最终用户会看到"自愈失败"。
            logger.info(
                "auto_resume_skipped sid=%s reason=supervisor_no_actionable_nudge "
                "action=%s has_hint=%s attempts=%d",
                sid, sup_action.action,
                bool(sup_action.hint_for_main_agent),
                attempts_so_far,
            )
            return AutoResumeResult(
                action="ask_user",
                attempt=attempts_so_far,
                reason=f"supervisor_action_{sup_action.action}_no_hint",
            )

        # 5) nudge with a real hint → spawn fresh task with hint injected.
        hint_text = sup_action.hint_for_main_agent
        new_msgs = list(original_msgs) + [{
            "role": "system",
            "content": f"[Supervisor Hint] {hint_text}",
            "_is_supervisor_hint": True,
        }]
        new_attempt = await self._activity.increment_auto_resume_attempts(sid)

        # Audit BEFORE dispatch — so even a dispatcher crash leaves a row.
        if self._audit is not None:
            try:
                await self._audit({
                    "session_id": sid,
                    "alert_id": sup_action.alert_id or "",
                    "hint_text": hint_text,
                    "action": "auto_resumed",
                    "severity": sup_action.severity or "yellow",
                    "diagnosis": sup_action.diagnosis or "",
                })
            except Exception as exc:  # noqa: BLE001
                logger.debug("auto_resume_audit_failed sid=%s err=%s", sid, exc)

        # ws event BEFORE dispatch — banner shows up immediately.
        if self._emit is not None:
            try:
                await self._emit("auto_resume_started", {
                    "session_id": sid,
                    "attempt": new_attempt,
                    "hint_preview": hint_text[:200],
                })
            except Exception as exc:  # noqa: BLE001
                logger.debug("auto_resume_emit_started_failed sid=%s err=%s", sid, exc)

        # Fire dispatcher. We await it so the caller knows spawn started,
        # but the dispatcher itself should be fire-and-forget internally
        # (e.g. wrap _run_chat in asyncio.create_task).
        try:
            await self._dispatch(sid, new_msgs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_resume_dispatch_failed sid=%s err=%s", sid, exc)
            # Don't roll the counter back — it's already been bumped and
            # an attempt was made. User can still manually retry.
            return AutoResumeResult(
                action="ask_user",
                attempt=new_attempt,
                hint=hint_text,
                reason=f"dispatch_failed:{type(exc).__name__}",
            )

        logger.info(
            "auto_resume_spawned sid=%s attempt=%d hint=%s",
            sid, new_attempt, hint_text[:100],
        )
        return AutoResumeResult(
            action="spawned",
            attempt=new_attempt,
            hint=hint_text,
        )

    # ─────────────── helpers ───────────────

    async def _safe_get_activity(self, sid: str):
        try:
            return await self._activity.get(sid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("auto_resume_activity_get_failed sid=%s err=%s", sid, exc)
            return None

    async def _emit_exhausted(
        self, sid: str, snapshot: dict[str, Any], attempts: int,
    ) -> None:
        # final_error: best-effort summary from snapshot
        final_error = (
            snapshot.get("detail")
            or snapshot.get("error")
            or snapshot.get("reason")
            or ""
        )
        # 2026-05-15: 此前这里只 emit WS 不写日志 → 用户在 UI 上看到红字
        # "自愈失败（N 次尝试）: ..." 但 backend.log 里找不到对应记录，事后
        # 完全没法溯源到底卡在哪个 supervisor 决策上。WARNING 级别落盘 +
        # snapshot 的 reason/iteration 一并打下来，方便后续翻 transcript。
        logger.warning(
            "auto_resume_exhausted sid=%s attempts=%d reason=%s "
            "snapshot_iter=%s final_error=%s",
            sid,
            attempts,
            snapshot.get("reason", ""),
            snapshot.get("iteration", ""),
            str(final_error)[:300],
        )
        if self._emit is None:
            return
        try:
            await self._emit("auto_resume_exhausted", {
                "session_id": sid,
                "final_error": str(final_error)[:200],
                "attempts": attempts,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("auto_resume_emit_exhausted_failed sid=%s err=%s", sid, exc)


__all__ = [
    "AutoResumeOrchestrator",
    "AutoResumeResult",
    "ChatDispatcher",
    "WsEmitter",
    "AuditWriter",
    "is_auto_resume_trigger",
]
