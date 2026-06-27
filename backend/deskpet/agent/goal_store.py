# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B1 — SessionGoalStore（PRD Stage B § B1）.

In-memory store mapping ``session_id -> SessionGoal``. Used by
``/goal <text>`` slash command and consumed by ``AgentLoop`` end-turn
``goal_checker`` rebound (see ``goal_checker.py``).

Persistence is wired via :meth:`SessionGoalStore.bind_persistence`
(goal-completion FP-1): ``persist`` / ``load_persisted`` round-trip
active goals through the ``session_goals`` table in SessionDB so they
survive a restart. When ``bind_persistence`` is never called the store
degrades to pure in-memory (BC + test isolation). Re-setting the same
``session_id`` overwrites the prior goal (last-write-wins).

Thread-safety: protected by a single ``asyncio.Lock``? No — the store
is consumed from a single asyncio task per session (AgentLoop runs
sequentially per WS connection), so a plain ``dict`` is sufficient.
If callers spawn parallel coros per session, wrap the store in a lock
at the call site.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


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
    # —— 新增（全默认值；goal_id="" 即未落库内存态，BC）——
    goal_id: str = ""
    status: str = "active"          # active | done | abandoned（落库权威）
    progress: float = 0.0
    criteria: Optional[str] = None
    updated_at: float = 0.0
    subgoals: list[str] = field(default_factory=list)


class SessionGoalStore:
    """In-memory ``session_id -> SessionGoal`` map (authoritative read path).

    Mutators (``set`` / ``mark_done`` / ``increment_iteration``) are sync
    and touch only the in-memory dict. Durable state is written through the
    async ``persist*`` methods and restored by ``load_persisted`` — see
    :meth:`bind_persistence`. Without a bound SessionDB the store is pure
    in-memory.
    """

    def __init__(self) -> None:
        self._goals: dict[str, SessionGoal] = {}
        self._session_db = None          # set via bind_persistence
        # /goal clear 落 abandoned 用：记下被清条目的 goal_id
        self._last_cleared_goal_id: dict[str, str] = {}
        self._logger = logging.getLogger("deskpet.agent.goal_store")
        # B-10 双写钩：可选的 (session_id, text) → Awaitable[None] callback。
        # goal_store 不 import facts（防 agent←memory import 环）；
        # callback 由 main.py lifespan 通过 bind_on_goal_set 注入。
        self._on_goal_set_cb: Optional[Callable[[str, str], Awaitable[None]]] = None
        # 保留 fire-and-forget fanout task 的强引用，防 asyncio GC 在 await
        # 挂起期间回收未完成 task（经典 footgun → category=goal 表静默为空）。
        self._fanout_tasks: set[asyncio.Future] = set()

    # ───────────────────── B-10 双写钩 ──────────────────────────────
    def bind_on_goal_set(
        self,
        callback: Callable[[str, str], Awaitable[None]],
    ) -> None:
        """注册 goal set 后的单向 fanout callback（B-10 双写契约）。

        callback(session_id: str, text: str) → Awaitable[None]

        - 在 set() 成功后异步 fire（asyncio.create_task，safe-fail）。
        - goal_store 不 import facts —— callback 由 main.py 注入闭包；
          这样防止 agent←memory import 环（§1.7 冻结约束）。
        - 仅允许注册一个 callback（last-write-wins）。
        """
        self._on_goal_set_cb = callback

    def _fire_on_goal_set(self, session_id: str, text: str) -> None:
        """Internal: fire the callback in a fire-and-forget task (safe-fail)."""
        cb = self._on_goal_set_cb
        if cb is None:
            return

        async def _run() -> None:
            try:
                await cb(session_id, text)
            except Exception as exc:  # noqa: BLE001 — safe-fail, no block
                self._logger.warning(
                    "on_goal_set_callback_failed sid=%s: %s", session_id, exc
                )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 强引用保留：add → done_callback discard，防 task 在首个 await
                # 挂起后被 GC（fire-and-forget footgun，真机 facts 表静默为空根因）。
                task = asyncio.ensure_future(_run())
                self._fanout_tasks.add(task)
                task.add_done_callback(self._fanout_tasks.discard)
            else:
                loop.run_until_complete(_run())
        except RuntimeError:
            # No running loop (e.g. sync tests) — run synchronously via new loop
            asyncio.run(_run())

    # ───────────────────── goal-completion FP-1 持久化 ────────────────
    def bind_persistence(self, session_db: object) -> None:
        """注入 SessionDB 句柄。未调 → 退化纯内存（BC + 测试隔离）。"""
        self._session_db = session_db

    async def persist(self, goal: SessionGoal) -> None:
        """异步落库一条 goal。safe-fail：未 bind 或落库失败都不抛
        （否则 _handle_goal 改 async 后异常冒泡到 slash 处理）。
        """
        if self._session_db is None:
            return
        try:
            await self._session_db.upsert_session_goal(
                goal_id=goal.goal_id,
                session_id=goal.session_id,
                text=goal.text,
                status=goal.status,
                progress=goal.progress,
                criteria=goal.criteria,
                max_iterations=goal.max_iterations,
                iterations_used=goal.iterations_used,
                set_at=goal.set_at,
                updated_at=goal.updated_at or goal.set_at,
            )
        except Exception as exc:  # noqa: BLE001 — safe-fail
            self._logger.warning(
                "goal_store.persist failed sid=%s: %s", goal.session_id, exc,
            )

    async def load_persisted(self) -> int:
        """启动恢复：把所有 active 目标灌回内存 dict。返回恢复条数。
        未 bind → 0（BC）。每 session 取最新 active（list 已按 updated_at 倒序）。
        """
        if self._session_db is None:
            return 0
        try:
            rows = await self._session_db.list_active_goals()
        except Exception as exc:  # noqa: BLE001 — safe-fail
            self._logger.warning("goal_store.load_persisted failed: %s", exc)
            return 0
        n = 0
        for r in rows:
            sid = r["session_id"]
            if sid in self._goals:       # 已有更新的（倒序首条）→ 跳过旧的
                continue
            self._goals[sid] = SessionGoal(
                session_id=sid,
                text=r["text"],
                set_at=r["set_at"],
                max_iterations=r["max_iterations"],
                iterations_used=r["iterations_used"],
                done=(r["status"] == "done"),
                goal_id=r["goal_id"],
                status=r["status"],
                progress=r["progress"],
                criteria=r["criteria"],
                updated_at=r["updated_at"],
            )
            n += 1
        self._logger.info("goal_store.load_persisted restored=%d", n)
        return n

    def get_goal_text(self, session_id: str) -> Optional[str]:
        """冻结 §1.4：sync, None-safe, 永读内存（最新权威）。"""
        g = self._goals.get(session_id)
        return g.text if g is not None else None

    def get_active_goal_context(
        self, session_id: Optional[str] = None
    ) -> Optional[tuple[str, str]]:
        """Resolve ``(goal_id, session_id)`` for an active goal.

        WI-TG-1 方案A: the main-agent global ``goal_task_*`` tools have no
        session/goal in their ``(args, corr_id)`` signature, so they call
        this to fill the two required args of ``TaskGraphStore.create``.

        - If ``session_id`` is given and has an active goal with a non-empty
          ``goal_id`` → return that goal's ``(goal_id, session_id)``.
        - Else fall back to the single most-recently-updated active goal
          across all sessions (deskpet is single-user; usually ≤1 active
          goal). This keeps the tool usable even when the caller can't
          supply a session id.
        - No active goal with a persisted ``goal_id`` → ``None`` (caller
          surfaces a "set a goal first" error).
        """
        if session_id is not None:
            g = self._goals.get(session_id)
            if g is not None and g.goal_id and g.status == "active":
                return (g.goal_id, g.session_id)
        candidates = [
            g for g in self._goals.values()
            if g.goal_id and g.status == "active"
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda g: g.updated_at or g.set_at)
        return (best.goal_id, best.session_id)

    def get_pending_tasks(self, session_id: str) -> list[str]:
        """子目标列表（供 WI-4a always-on [当前子目标] 注入）。

        sync, None-safe：无目标 / 无子目标 → 空 list。读 ``SessionGoal.subgoals``
        内存权威。当前 v1 不区分 done/pending（subgoals 是纯文本清单），全部返回。
        """
        g = self._goals.get(session_id)
        return list(g.subgoals) if g is not None and g.subgoals else []

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
        import uuid
        now = time.time()
        goal = SessionGoal(
            session_id=session_id,
            text=text,
            set_at=now,
            max_iterations=max_iterations,
            iterations_used=0,
            done=False,
            goal_id=uuid.uuid4().hex,
            status="active",
            updated_at=now,
        )
        self._goals[session_id] = goal
        # B-10：触发 fanout（facts upsert 等），safe-fail，不阻 set()。
        self._fire_on_goal_set(session_id, text)
        return goal

    def get(self, session_id: str) -> Optional[SessionGoal]:
        """Return the active goal for ``session_id`` or ``None``."""
        return self._goals.get(session_id)

    def clear(self, session_id: str) -> bool:
        """Drop the goal for ``session_id``. Returns ``True`` if a goal
        existed and was removed, ``False`` if no goal was active.

        记下被清条目的 ``goal_id`` 供 ``persist_abandon`` 落 abandoned
        （不物理删库行，保留历史给 P0-3 沉淀）。
        """
        g = self._goals.get(session_id)
        if g is not None:
            self._last_cleared_goal_id[session_id] = g.goal_id
            del self._goals[session_id]
            return True
        return False

    async def persist_abandon(self, session_id: str) -> None:
        """/goal clear：落 abandoned（不物理删库行）。内存已 clear，
        从落库行改 status。无 session_db / 无 goal_id → no-op。safe-fail。
        """
        if self._session_db is None:
            return
        gid = self._last_cleared_goal_id.pop(session_id, "")
        if not gid:
            return
        try:
            rows = await self._session_db.get_active_goals(session_id)
            for r in rows:
                if r["goal_id"] == gid:
                    await self._session_db.upsert_session_goal(
                        goal_id=gid, session_id=session_id, text=r["text"],
                        status="abandoned", progress=r["progress"],
                        criteria=r["criteria"],
                        max_iterations=r["max_iterations"],
                        iterations_used=r["iterations_used"],
                        set_at=r["set_at"], updated_at=time.time(),
                    )
                    break
        except Exception as exc:  # noqa: BLE001 — safe-fail
            self._logger.warning(
                "persist_abandon failed sid=%s: %s", session_id, exc,
            )

    def mark_done(self, session_id: str) -> bool:
        """Mark the goal as achieved. Returns ``True`` if a goal existed
        and was marked, ``False`` otherwise. Idempotent.
        """
        goal = self._goals.get(session_id)
        if goal is None:
            return False
        goal.done = True
        goal.status = "done"
        goal.updated_at = time.time()
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

    async def persist_iteration(self, session_id: str) -> None:
        """T1：把当前内存的 iterations_used 落库（重启恢复）。safe-fail。"""
        g = self._goals.get(session_id)
        if g is None:
            return
        g.updated_at = time.time()
        await self.persist(g)

    async def persist_done(self, session_id: str) -> None:
        """落库 done 终态（与 persist_iteration 对称）。否则 load_persisted
        只召回 status='active'，已完成目标重启会复活成 active。safe-fail。
        """
        g = self._goals.get(session_id)
        if g is None:
            return
        await self.persist(g)


__all__ = ["SessionGoal", "SessionGoalStore"]
