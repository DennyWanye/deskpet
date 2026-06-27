# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-G1 — TeamStore (Companion+Code v2 Multi-Agent Team).

SQLite-backed store for the shared task list, mailbox, and permission
queue that drives the Team workflow. Each team gets its own .db file
under ``<base_dir>/<team_id>.db`` — keeps WAL lock-contention low and
makes per-team cleanup trivial (just delete the file).

Design choices
--------------

* **Atomic claim** — ``claim_task`` uses ``UPDATE ... WHERE task_id =
  (SELECT task_id FROM tasks WHERE status='pending' ORDER BY created_at
  LIMIT 1) AND status='pending' RETURNING *`` so two teammates that
  ``claim`` concurrently can never both grab the same task. ``RETURNING``
  is SQLite 3.35+ (Python 3.11+ stdlib ships >= 3.40).
* **WAL mode** — ``PRAGMA journal_mode=WAL`` + ``busy_timeout=5000``
  lets multiple async writers proceed without "database is locked".
* **In-memory cache** — none. SQLite is the source of truth; an
  in-memory mirror would just create cache-coherency bugs across the
  multi-teammate concurrent path. SQLite WAL reads are O(µs).
* **Safe-fail** — every public method returns a sentinel (None /
  False / []) on I/O error rather than raising, so a corrupted db
  file can't take down the whole agent loop. Errors are logged at
  warning.
* **No global singleton** — caller (``spawn_team``) constructs a
  fresh ``TeamStore`` per session and is responsible for cleanup.

Schema
------

Three tables, all keyed by ``team_id`` (denormalised because per-team
db files mean it's free):

* ``tasks(task_id PK, team_id, description, status, claimed_by,
  result, created_at, claimed_at, done_at)``
* ``messages(msg_id INTEGER PK AUTOINCREMENT, team_id, from_id,
  to_id, content, ts, read_flag)``
* ``permissions(req_id INTEGER PK AUTOINCREMENT, team_id, teammate_id,
  action, granted, decided_at, created_at)``
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import aiosqlite

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Dataclasses (immutable views — created from SQLite row tuples).
# ---------------------------------------------------------------------


@dataclass
class TeamTask:
    """One unit of work shared on a team's task list."""

    task_id: str
    team_id: str
    description: str
    status: str  # "pending" | "claimed" | "in_progress" | "done" | "failed"
    created_at: float
    claimed_by: Optional[str] = None
    result: Optional[str] = None
    claimed_at: Optional[float] = None
    done_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "team_id": self.team_id,
            "description": self.description,
            "status": self.status,
            "claimed_by": self.claimed_by,
            "result": self.result,
            "created_at": self.created_at,
            "claimed_at": self.claimed_at,
            "done_at": self.done_at,
        }


@dataclass
class TeamMessage:
    """One mailbox entry."""

    msg_id: int
    team_id: str
    from_id: str
    to_id: str
    content: str
    ts: float
    read_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "team_id": self.team_id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "content": self.content,
            "ts": self.ts,
            "read_flag": self.read_flag,
        }


@dataclass
class TeamPermissionRequest:
    """A teammate-asked permission awaiting leader decision."""

    req_id: int
    team_id: str
    teammate_id: str
    action: str
    created_at: float
    granted: Optional[bool] = None  # None = undecided
    decided_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "req_id": self.req_id,
            "team_id": self.team_id,
            "teammate_id": self.teammate_id,
            "action": self.action,
            "granted": self.granted,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


# Valid status transitions; ``update_task`` rejects illegal jumps
# (e.g. pending → done without claim) which would otherwise let a
# misbehaving teammate skip the claim step.
_VALID_STATUSES = frozenset({"pending", "claimed", "in_progress", "done", "failed"})


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    team_id      TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    claimed_by   TEXT,
    result       TEXT,
    created_at   REAL NOT NULL,
    claimed_at   REAL,
    done_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_created
    ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_team
    ON tasks(team_id);

CREATE TABLE IF NOT EXISTS messages (
    msg_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id   TEXT NOT NULL,
    from_id   TEXT NOT NULL,
    to_id     TEXT NOT NULL,
    content   TEXT NOT NULL,
    ts        REAL NOT NULL,
    read_flag INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_recipient
    ON messages(team_id, to_id, read_flag);

CREATE TABLE IF NOT EXISTS permissions (
    req_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     TEXT NOT NULL,
    teammate_id TEXT NOT NULL,
    action      TEXT NOT NULL,
    granted     INTEGER,
    created_at  REAL NOT NULL,
    decided_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_permissions_pending
    ON permissions(team_id, granted);
"""


class TeamStore:
    """Async SQLite-backed task list / mailbox / permission queue.

    Parameters
    ----------
    base_dir:
        Directory under which per-team ``.db`` files live. Created on
        first ``ensure_schema``.
    """

    def __init__(self, base_dir: Path | str) -> None:
        self._base = Path(base_dir)
        # In-process cache of which team-id db files we've already
        # initialised, so we don't run CREATE-IF-NOT-EXISTS on every
        # method call (cheap but avoidable).
        self._initialised: set[str] = set()

    def cleanup_old(self, *, max_age_days: float = 7.0, max_files: int = 200) -> int:
        """删过期/超量 team ``.db`` 文件（boot 时调，防 ``<user_data>/teams/``
        无限堆积，WI-2.6/R6）。返回删除的 db 数。**best-effort，永不抛。**"""
        removed = 0
        try:
            base = self._base
            if not base.exists():
                return 0

            def _unlink_db(p: Path) -> None:
                nonlocal removed
                gone = False
                for suff in ("", "-wal", "-shm"):
                    try:
                        Path(str(p) + suff).unlink()
                        if suff == "":
                            gone = True
                    except OSError:
                        continue
                if gone:
                    removed += 1

            dbs = sorted(base.glob("*.db"), key=lambda p: p.stat().st_mtime)
            cutoff = time.time() - max_age_days * 86400.0
            survivors: list[Path] = []
            for p in dbs:
                try:
                    if p.stat().st_mtime < cutoff:
                        _unlink_db(p)
                    else:
                        survivors.append(p)
                except OSError:
                    continue
            # survivors 已按 mtime 升序 → 超量时删最旧的
            if len(survivors) > max_files:
                for p in survivors[: len(survivors) - max_files]:
                    _unlink_db(p)
            if removed:
                log.info("team_store cleanup removed=%d db files", removed)
        except Exception as exc:  # noqa: BLE001 — 清理失败不影响启动
            log.debug("TeamStore.cleanup_old failed: %s", exc)
        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _db_path(self, team_id: str) -> Path:
        # Filename: keep team_id printable; assume caller passes a uuid hex
        # or alphanum slug. Reject path-separator chars defensively.
        if "/" in team_id or "\\" in team_id or ".." in team_id:
            raise ValueError(f"invalid team_id: {team_id!r}")
        return self._base / f"{team_id}.db"

    async def _ensure_schema(self, team_id: str) -> None:
        """Create db + tables + WAL pragmas on first touch per team."""
        if team_id in self._initialised:
            return
        self._base.mkdir(parents=True, exist_ok=True)
        path = self._db_path(team_id)
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA_SQL)
            await db.commit()
        self._initialised.add(team_id)

    @staticmethod
    def _row_to_task(row: tuple) -> TeamTask:
        return TeamTask(
            task_id=row[0],
            team_id=row[1],
            description=row[2],
            status=row[3],
            claimed_by=row[4],
            result=row[5],
            created_at=float(row[6]),
            claimed_at=float(row[7]) if row[7] is not None else None,
            done_at=float(row[8]) if row[8] is not None else None,
        )

    # ------------------------------------------------------------------
    # Task list
    # ------------------------------------------------------------------

    async def create_task(self, team_id: str, description: str) -> str:
        """Create a new pending task. Returns its uuid task_id."""
        await self._ensure_schema(team_id)
        task_id = uuid.uuid4().hex
        now = time.time()
        async with aiosqlite.connect(self._db_path(team_id)) as db:
            await db.execute(
                "INSERT INTO tasks(task_id, team_id, description, status,"
                " created_at) VALUES (?, ?, ?, 'pending', ?)",
                (task_id, team_id, description, now),
            )
            await db.commit()
        return task_id

    async def claim_task(
        self, team_id: str, teammate_id: str
    ) -> Optional[TeamTask]:
        """Atomically claim the oldest pending task. None if pool empty.

        Concurrency: SQLite's UPDATE...RETURNING is transactional under
        WAL — if two coroutines race, only one gets the row. We add an
        explicit ``BEGIN IMMEDIATE`` so the second concurrent claim sees
        the change before its own UPDATE evaluates the subquery.
        """
        await self._ensure_schema(team_id)
        now = time.time()
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                # BEGIN IMMEDIATE acquires the RESERVED lock right away;
                # serialises concurrent claimers. WAL still allows readers
                # to proceed.
                await db.execute("BEGIN IMMEDIATE")
                cur = await db.execute(
                    "UPDATE tasks SET status='claimed',"
                    " claimed_by=?, claimed_at=?"
                    " WHERE task_id = ("
                    "   SELECT task_id FROM tasks"
                    "    WHERE team_id=? AND status='pending'"
                    "    ORDER BY created_at LIMIT 1"
                    " ) AND status='pending'"
                    " RETURNING task_id, team_id, description, status,"
                    " claimed_by, result, created_at, claimed_at, done_at",
                    (teammate_id, now, team_id),
                )
                row = await cur.fetchone()
                await cur.close()
                await db.commit()
        except aiosqlite.Error as exc:
            log.warning("team_store: claim_task failed: %s", exc)
            return None
        if row is None:
            return None
        return self._row_to_task(row)

    async def update_task(
        self,
        team_id: str,
        task_id: str,
        status: str,
        result: Optional[str] = None,
    ) -> bool:
        """Update a task's status (+ optional result). Returns True on
        success, False if task missing or status invalid."""
        if status not in _VALID_STATUSES:
            log.warning("team_store: update_task invalid status %r", status)
            return False
        await self._ensure_schema(team_id)
        now = time.time()
        done_at = now if status in ("done", "failed") else None
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                cur = await db.execute(
                    "UPDATE tasks SET status=?, result=COALESCE(?, result),"
                    " done_at=COALESCE(?, done_at) WHERE task_id=? AND team_id=?",
                    (status, result, done_at, task_id, team_id),
                )
                changed = cur.rowcount
                await cur.close()
                await db.commit()
        except aiosqlite.Error as exc:
            log.warning("team_store: update_task failed: %s", exc)
            return False
        return changed > 0

    async def list_tasks(
        self, team_id: str, status: Optional[str] = None
    ) -> list[TeamTask]:
        """Snapshot of tasks (optionally filtered by status)."""
        await self._ensure_schema(team_id)
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                if status is None or status == "all":
                    cur = await db.execute(
                        "SELECT task_id, team_id, description, status,"
                        " claimed_by, result, created_at, claimed_at, done_at"
                        " FROM tasks WHERE team_id=? ORDER BY created_at",
                        (team_id,),
                    )
                else:
                    cur = await db.execute(
                        "SELECT task_id, team_id, description, status,"
                        " claimed_by, result, created_at, claimed_at, done_at"
                        " FROM tasks WHERE team_id=? AND status=?"
                        " ORDER BY created_at",
                        (team_id, status),
                    )
                rows = await cur.fetchall()
                await cur.close()
        except aiosqlite.Error as exc:
            log.warning("team_store: list_tasks failed: %s", exc)
            return []
        return [self._row_to_task(r) for r in rows]

    async def get_task(self, team_id: str, task_id: str) -> Optional[TeamTask]:
        """Read one task by id."""
        await self._ensure_schema(team_id)
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                cur = await db.execute(
                    "SELECT task_id, team_id, description, status,"
                    " claimed_by, result, created_at, claimed_at, done_at"
                    " FROM tasks WHERE task_id=? AND team_id=?",
                    (task_id, team_id),
                )
                row = await cur.fetchone()
                await cur.close()
        except aiosqlite.Error as exc:
            log.warning("team_store: get_task failed: %s", exc)
            return None
        return self._row_to_task(row) if row else None

    # ------------------------------------------------------------------
    # Mailbox
    # ------------------------------------------------------------------

    async def send_message(
        self, team_id: str, from_id: str, to_id: str, content: str
    ) -> bool:
        """Append a message to the team mailbox."""
        await self._ensure_schema(team_id)
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                await db.execute(
                    "INSERT INTO messages(team_id, from_id, to_id, content, ts)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (team_id, from_id, to_id, content, time.time()),
                )
                await db.commit()
        except aiosqlite.Error as exc:
            log.warning("team_store: send_message failed: %s", exc)
            return False
        return True

    async def get_messages(
        self,
        team_id: str,
        recipient_id: str,
        *,
        only_unread: bool = True,
        mark_read: bool = True,
    ) -> list[TeamMessage]:
        """Fetch messages for ``recipient_id``. By default returns only
        unread + marks them read in the same transaction (so callers
        get exactly-once delivery semantics without manual ack)."""
        await self._ensure_schema(team_id)
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                if only_unread:
                    cur = await db.execute(
                        "SELECT msg_id, team_id, from_id, to_id, content, ts,"
                        " read_flag FROM messages"
                        " WHERE team_id=? AND to_id=? AND read_flag=0"
                        " ORDER BY ts",
                        (team_id, recipient_id),
                    )
                else:
                    cur = await db.execute(
                        "SELECT msg_id, team_id, from_id, to_id, content, ts,"
                        " read_flag FROM messages"
                        " WHERE team_id=? AND to_id=? ORDER BY ts",
                        (team_id, recipient_id),
                    )
                rows = await cur.fetchall()
                await cur.close()
                msgs = [
                    TeamMessage(
                        msg_id=int(r[0]),
                        team_id=r[1],
                        from_id=r[2],
                        to_id=r[3],
                        content=r[4],
                        ts=float(r[5]),
                        read_flag=bool(r[6]),
                    )
                    for r in rows
                ]
                if mark_read and msgs:
                    await db.execute(
                        "UPDATE messages SET read_flag=1"
                        " WHERE team_id=? AND to_id=? AND read_flag=0",
                        (team_id, recipient_id),
                    )
                    await db.commit()
        except aiosqlite.Error as exc:
            log.warning("team_store: get_messages failed: %s", exc)
            return []
        return msgs

    # ------------------------------------------------------------------
    # Permission queue
    # ------------------------------------------------------------------

    async def request_permission(
        self, team_id: str, teammate_id: str, action: str
    ) -> int:
        """Queue a permission request. Returns its req_id.

        Caller (teammate tool) typically polls ``get_permission`` until
        a leader grants/denies. The leader uses
        ``grant_permission(req_id, allow=bool)``.
        """
        await self._ensure_schema(team_id)
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                cur = await db.execute(
                    "INSERT INTO permissions(team_id, teammate_id, action,"
                    " created_at) VALUES (?, ?, ?, ?)",
                    (team_id, teammate_id, action, time.time()),
                )
                new_id = int(cur.lastrowid or 0)
                await cur.close()
                await db.commit()
        except aiosqlite.Error as exc:
            log.warning("team_store: request_permission failed: %s", exc)
            return 0
        return new_id

    async def grant_permission(
        self, team_id: str, req_id: int, allow: bool
    ) -> bool:
        """Leader decides on a queued permission request."""
        await self._ensure_schema(team_id)
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                cur = await db.execute(
                    "UPDATE permissions SET granted=?, decided_at=?"
                    " WHERE req_id=? AND team_id=? AND granted IS NULL",
                    (1 if allow else 0, time.time(), req_id, team_id),
                )
                changed = cur.rowcount
                await cur.close()
                await db.commit()
        except aiosqlite.Error as exc:
            log.warning("team_store: grant_permission failed: %s", exc)
            return False
        return changed > 0

    async def get_permission(
        self, team_id: str, req_id: int
    ) -> Optional[TeamPermissionRequest]:
        """Read one permission request (any state)."""
        await self._ensure_schema(team_id)
        try:
            async with aiosqlite.connect(self._db_path(team_id)) as db:
                cur = await db.execute(
                    "SELECT req_id, team_id, teammate_id, action, granted,"
                    " created_at, decided_at FROM permissions"
                    " WHERE req_id=? AND team_id=?",
                    (req_id, team_id),
                )
                row = await cur.fetchone()
                await cur.close()
        except aiosqlite.Error as exc:
            log.warning("team_store: get_permission failed: %s", exc)
            return None
        if row is None:
            return None
        return TeamPermissionRequest(
            req_id=int(row[0]),
            team_id=row[1],
            teammate_id=row[2],
            action=row[3],
            granted=None if row[4] is None else bool(row[4]),
            created_at=float(row[5]),
            decided_at=float(row[6]) if row[6] is not None else None,
        )


__all__ = [
    "TeamStore",
    "TeamTask",
    "TeamMessage",
    "TeamPermissionRequest",
]
