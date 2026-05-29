# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B1 — SessionGoalStore（PRD Stage B § B1）.

In-memory store mapping ``session_id -> SessionGoal``. Used by
``/goal <text>`` slash command and consumed by ``AgentLoop`` end-turn
``goal_checker`` rebound (see ``goal_checker.py``).

Persistence is deliberately NOT implemented in v1 — goals are
session-lifetime only (TODO: SessionDB persistence留 v2). Re-setting
the same ``session_id`` overwrites the prior goal (last-write-wins).

Thread-safety: protected by a single ``asyncio.Lock``? No — the store
is consumed from a single asyncio task per session (AgentLoop runs
sequentially per WS connection), so a plain ``dict`` is sufficient.
If callers spawn parallel coros per session, wrap the store in a lock
at the call site.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionGoal:
    """One active goal for one session.

    Attributes
    ----------
    session_id:
        The session this goal belongs to.
    text:
        Free-text goal description (what the LLM should achieve).
    set_at:
        Unix timestamp (``time.time()``) when the goal was set.
    max_iterations:
        Cap on goal-checker rebounds — after this many ``check()``
        returns ``done=False``, AgentLoop stops nudging and lets the
        turn finalize. Default 10.
    iterations_used:
        Counter incremented each time GoalChecker returns ``done=False``
        and AgentLoop rebounds the loop.
    done:
        Set ``True`` when GoalChecker returns ``done=True``. Once done,
        AgentLoop short-circuits the goal-check block on subsequent
        turns.
    """

    session_id: str
    text: str
    set_at: float
    max_iterations: int = 10
    iterations_used: int = 0
    done: bool = False


class SessionGoalStore:
    """In-memory ``session_id -> SessionGoal`` map.

    Methods are all sync (no I/O). Persistence留 v2 (TODO: SessionDB).
    """

    def __init__(self) -> None:
        self._goals: dict[str, SessionGoal] = {}

    def set(
        self,
        session_id: str,
        text: str,
        max_iterations: int = 10,
    ) -> SessionGoal:
        """Set (or overwrite) the goal for ``session_id``.

        Returns the newly-stored ``SessionGoal``. ``iterations_used``
        and ``done`` are reset to 0/False even when overwriting an
        existing entry (new goal = fresh counter).
        """
        goal = SessionGoal(
            session_id=session_id,
            text=text,
            set_at=time.time(),
            max_iterations=max_iterations,
            iterations_used=0,
            done=False,
        )
        self._goals[session_id] = goal
        return goal

    def get(self, session_id: str) -> Optional[SessionGoal]:
        """Return the active goal for ``session_id`` or ``None``."""
        return self._goals.get(session_id)

    def clear(self, session_id: str) -> bool:
        """Drop the goal for ``session_id``. Returns ``True`` if a goal
        existed and was removed, ``False`` if no goal was active.
        """
        if session_id in self._goals:
            del self._goals[session_id]
            return True
        return False

    def mark_done(self, session_id: str) -> bool:
        """Mark the goal as achieved. Returns ``True`` if a goal existed
        and was marked, ``False`` otherwise. Idempotent.
        """
        goal = self._goals.get(session_id)
        if goal is None:
            return False
        goal.done = True
        return True

    def increment_iteration(self, session_id: str) -> int:
        """Increment ``iterations_used`` and return the new value.
        Returns ``0`` if no goal was active (caller should have checked
        via ``get()`` first — this is a defensive no-op).
        """
        goal = self._goals.get(session_id)
        if goal is None:
            return 0
        goal.iterations_used += 1
        return goal.iterations_used


__all__ = ["SessionGoal", "SessionGoalStore"]
