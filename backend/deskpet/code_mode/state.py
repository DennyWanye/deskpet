"""Per-base-session Code mode state.

A user normally lives in ``session_id="default"`` for chat. When they
enter Code mode we **don't** rename their session — the chat history
should stay intact under "default". Instead we derive a sibling
session id (``"code-<sha[:8]>"``) keyed on the project root path, and
route Code mode chat / tool calls through that.

This way:
- Companion-mode chat history under "default" stays clean (no code
  spam).
- Each project gets its own conversation memory; opening the same
  project root next week recovers the prior thread.
- L3 (BGE-M3 vector recall) can still cross-fertilise — the embedder
  doesn't filter by session by default — so the pet remembers what
  you talked about in project A while you're working on project B.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Optional


def _code_session_id(project_root: Path) -> str:
    """Stable hash so the same project always maps to the same session."""
    h = hashlib.sha1(str(project_root.resolve()).encode("utf-8")).hexdigest()
    return f"code-{h[:8]}"


@dataclass
class CodeModeState:
    """One row of the per-session enable map."""

    enabled: bool = False
    project_root: Optional[Path] = None
    code_session_id: Optional[str] = None
    # Optional human-readable name shown in UI; defaults to dir name.
    project_name: str = ""


class CodeModeManager:
    """Singleton on service_context. Thread-safe — chat handler & control
    WS handler may both touch state simultaneously.

    P4-S25 B4: persists enrolled projects to SessionDB.code_sessions so
    they survive a backend restart. The in-memory ``_states`` dict is the
    fast-path read; DB is the source of truth on startup.
    """

    def __init__(self) -> None:
        self._states: dict[str, CodeModeState] = {}
        self._lock = RLock()
        # Optional SessionDB binding — set by ``bind_persistence(sdb)``
        # at startup. When None, manager runs purely in-memory (the
        # legacy P4-S22 behaviour) so unit tests don't need a DB.
        self._sdb = None  # type: ignore[var-annotated]

    def bind_persistence(self, sdb) -> None:  # type: ignore[no-untyped-def]
        """P4-S25 B4: install the SessionDB used for code_sessions persistence."""
        self._sdb = sdb

    async def load_persisted(self, sdb=None) -> int:  # type: ignore[no-untyped-def]
        """P4-S25 B4: rebuild ``_states`` from the code_sessions table.

        Idempotent — calling twice merges with whatever's already in
        memory (DB rows win on conflict, since they're the durable
        truth). Returns the count of restored sessions.
        """
        s = sdb or self._sdb
        if s is None:
            return 0
        rows = await s.list_code_sessions()
        with self._lock:
            for r in rows:
                bsid = r["base_session_id"]
                root_str = r["project_root"]
                self._states[bsid] = CodeModeState(
                    enabled=True,
                    project_root=Path(root_str),
                    code_session_id=r["code_session_id"],
                    project_name=r["project_name"],
                )
        return len(rows)

    # --------------------------- lifecycle -----------------------------

    def enter(self, base_session_id: str, project_root: Path) -> CodeModeState:
        """Bind ``base_session_id`` → Code mode rooted at ``project_root``.
        Returns the resulting state; caller can read ``code_session_id``
        for routing.

        P4-S25 B4: also fire-and-forget persists to SessionDB so the
        project survives restart. Caller doesn't await the persist —
        we want UI feedback (state returned) to be instant; the DB
        write is sub-ms anyway.
        """
        with self._lock:
            project_root = project_root.resolve()
            state = CodeModeState(
                enabled=True,
                project_root=project_root,
                code_session_id=_code_session_id(project_root),
                project_name=project_root.name,
            )
            self._states[base_session_id] = state
        # Schedule persistence outside the sync lock.
        if self._sdb is not None:
            import asyncio as _asyncio
            _coro = self._sdb.upsert_code_session(
                base_session_id=base_session_id,
                code_session_id=state.code_session_id or "",
                project_root=str(state.project_root or ""),
                project_name=state.project_name,
            )
            try:
                _asyncio.get_running_loop().create_task(_coro)
            except RuntimeError:
                # No loop yet (early init); skip — load_persisted on
                # next start will re-add via list_code_sessions, but
                # this row won't be in DB. Acceptable: enter is
                # always called from within an asyncio handler.
                _coro.close()
        return state

    def exit(self, base_session_id: str) -> None:
        """Disable code mode for the given base session.

        Idempotent — calling ``exit`` twice (or on a session that never
        entered) is a no-op. We **drop** the in-memory state record so
        a future ``enter`` starts cleanly; the SessionDB ``messages``
        and ``code_sessions`` rows are untouched (resuming the same
        project recovers history because ``code_session_id`` is stable,
        AND the project shows up again at the next restart via
        load_persisted).

        For *real* deletion (the user clicks 🗑️ → confirm), use
        :meth:`delete` instead — that one wipes the persistence row
        and todos.
        """
        with self._lock:
            self._states.pop(base_session_id, None)

    async def delete(self, base_session_id: str) -> str | None:
        """P4-S25 B4: hard delete — drops in-memory state AND persistence row.

        Returns the code_session_id we just disowned (so caller can
        also delete code_todos), or None if nothing was registered.
        Messages stay; the project just won't appear after restart.
        """
        with self._lock:
            state = self._states.pop(base_session_id, None)
        csid = state.code_session_id if state else None
        if self._sdb is not None:
            try:
                await self._sdb.delete_code_session(base_session_id)
            except Exception:  # noqa: BLE001 — durability is best-effort
                pass
        return csid

    # --------------------------- queries -------------------------------

    def is_enabled(self, base_session_id: str) -> bool:
        with self._lock:
            s = self._states.get(base_session_id)
            return bool(s and s.enabled)

    def get(self, base_session_id: str) -> Optional[CodeModeState]:
        with self._lock:
            return self._states.get(base_session_id)

    def project_root(self, base_session_id: str) -> Optional[Path]:
        s = self.get(base_session_id)
        return s.project_root if s else None

    def code_session_id(self, base_session_id: str) -> Optional[str]:
        s = self.get(base_session_id)
        return s.code_session_id if s else None

    # --------------------------- introspection -------------------------

    def all_sessions(self) -> dict[str, CodeModeState]:
        """Snapshot of current per-session state (e.g. for /metrics / debug)."""
        with self._lock:
            return dict(self._states)
