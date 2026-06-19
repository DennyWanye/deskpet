# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""memory-v2 (Phase A-E) — runtime-applied schema helper.

Why not a `migrations/009_*.sql` file?
-------------------------------------
The 008 migration is the last one tied to a `PRAGMA user_version` bump
that backend tests pin to ``16``. Adding a 009 file changes the bump
and forces edits to several hardcoded test assertions. Instead, we
apply our additive tables at runtime via ``CREATE TABLE IF NOT EXISTS``
— idempotent, no version bump, no test churn.

Strangler-fig: ``ensure_memory_v2_tables`` is invoked on demand from
each Phase A-E module's first DB call. When nothing uses them, the
tables are never created and the DB stays at v16 byte-identical to
pre-memory-v2.

Tables (same set as the previous 009 SQL):
    memory_qa_set, memory_eval_run, memory_user_feedback,
    facts, messages_chunks, workspace_state, skill_memory
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Set

import aiosqlite

log = logging.getLogger(__name__)


_DDL = """
-- =====================================================================
-- Phase A — Evaluation
-- =====================================================================
CREATE TABLE IF NOT EXISTS memory_qa_set (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,
    query           TEXT    NOT NULL,
    expected_msg_id INTEGER NOT NULL,
    tags            TEXT,
    created_at      REAL    NOT NULL,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_qa_source ON memory_qa_set(source);

CREATE TABLE IF NOT EXISTS memory_eval_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    REAL    NOT NULL,
    finished_at   REAL,
    qa_set_size   INTEGER NOT NULL,
    metrics_json  TEXT,
    config_json   TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_run_time ON memory_eval_run(started_at);

CREATE TABLE IF NOT EXISTS memory_user_feedback (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_msg_id  INTEGER NOT NULL,
    value          INTEGER NOT NULL,
    context_query  TEXT,
    created_at     REAL    NOT NULL,
    session_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_msg ON memory_user_feedback(source_msg_id);
CREATE INDEX IF NOT EXISTS idx_feedback_time ON memory_user_feedback(created_at);

-- =====================================================================
-- Phase B — Facts
-- =====================================================================
-- 记忆系统升级 WI-M1.4 / PRD §3.1：facts 走向量召回（中文整句 LIKE
-- 子串匹配几乎不命中）。``embedding`` 存规范文本 "key: value" 的 BGE-M3
-- 向量（float32 BLOB）。facts 表小，召回走 Python brute-force cosine，
-- 不必上向量索引。facts 表此前从不被调用（死代码），DDL 直接带该列。
CREATE TABLE IF NOT EXISTS facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    category       TEXT    NOT NULL,
    subject        TEXT    NOT NULL,
    key            TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    confidence     REAL    NOT NULL DEFAULT 0.5,
    source_msg_id  INTEGER,
    created_at     REAL    NOT NULL,
    updated_at     REAL    NOT NULL,
    evidence       TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    decay_rate     REAL    NOT NULL DEFAULT 0.02,
    last_recalled  REAL,
    embedding      BLOB,
    -- Stage 2 D1：cross-key 矛盾 / memory_forget 配套列。
    -- 老库由 schema_v2_migrator.ensure_memory_v2_columns 通过 ALTER
    -- 补齐；新库一次到位避免启动后立即再 ALTER。
    superseded_by  INTEGER REFERENCES facts(id),
    forgotten_at   REAL,
    -- FP-4 Task 1：scope（user/session）+ pinned（用户主动钉住，跳过衰减）。
    -- 老库同样由 schema_v2_migrator._COLUMN_ADDS ALTER 补齐。
    scope          TEXT    DEFAULT 'user',
    pinned         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_subject_key ON facts(subject, key, is_active);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category, is_active);
CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_at);

-- =====================================================================
-- Phase C — Long-message chunks
-- =====================================================================
CREATE TABLE IF NOT EXISTS messages_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    embedding   BLOB,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_message ON messages_chunks(message_id);

-- =====================================================================
-- Phase D — Workspace memory
-- =====================================================================
CREATE TABLE IF NOT EXISTS workspace_state (
    session_id      TEXT    NOT NULL,
    path            TEXT    NOT NULL,
    last_action     TEXT    NOT NULL,
    last_action_ts  REAL    NOT NULL,
    content_hash    TEXT,
    content_summary TEXT,
    byte_size       INTEGER,
    PRIMARY KEY (session_id, path)
);
CREATE INDEX IF NOT EXISTS idx_workspace_session ON workspace_state(session_id, last_action_ts);

-- =====================================================================
-- Phase E — Skill / procedural memory
-- =====================================================================
CREATE TABLE IF NOT EXISTS skill_memory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    trigger_pattern TEXT,
    steps_json      TEXT,
    usage_count     INTEGER NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    last_used_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_skill_name ON skill_memory(name);

-- FEAT-A4 (superpowers): plan-confirm 硬门的 awaiting plan sidecar。
-- 不走 messages 表（避免 _on_message_written → VectorWorker embed + FTS5
-- 污染语义检索）。PK=session_id：plan 门 per-session 单 future，同时只一个
-- awaiting plan，upsert 天然覆盖。F5/HMR rehydration 据此重建 [执行]/[取消] 栏。
CREATE TABLE IF NOT EXISTS session_plans (
    session_id  TEXT    PRIMARY KEY,
    rationale   TEXT    NOT NULL DEFAULT '',
    steps_json  TEXT    NOT NULL DEFAULT '[]',
    awaiting    INTEGER NOT NULL DEFAULT 0,
    ts          REAL    NOT NULL
);

"""

# ─────────────────────────────────────────────────────────────────────
# goal-completion FP-1 — 目标持久化（WI-1.1，冻结 §1.3）
# ─────────────────────────────────────────────────────────────────────
# ⚠️ 故意 NOT 放进共享 `_DDL`：`ensure_memory_v2_tables` 被 facts /
# session_plans 等常态调用，若把 session_goals 塞进共享 DDL，则 goal_mode
# OFF 但 memory_v2 ON（默认）的用户也会被建空表 → 违反护城河「flag-OFF 用户
# DB 字节不变」（R-T5 字节基线会 FAIL）。改为独立 ensure，只有 goal store
# 真正落库时（goal_mode ON）才触发建表。多目标物理支持（goal_id PK），API
# 层 last-write-wins 单活跃目标；criteria 占位列 FP-3(2.3) 用。
_SESSION_GOALS_DDL = """
CREATE TABLE IF NOT EXISTS session_goals (
    goal_id         TEXT    PRIMARY KEY,
    session_id      TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active',
    progress        REAL    NOT NULL DEFAULT 0.0,
    criteria        TEXT,
    max_iterations  INTEGER NOT NULL DEFAULT 10,
    iterations_used INTEGER NOT NULL DEFAULT 0,
    set_at          REAL    NOT NULL,
    updated_at      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_goals_sid
    ON session_goals(session_id, status);
"""

# ─────────────────────────────────────────────────────────────────────
# goal-completion FP-2 — task graph（WI-1.2，冻结 §1.3）
# ─────────────────────────────────────────────────────────────────────
# ⚠️ 故意 NOT 放进共享 `_DDL`：同 session_goals 理由，flag-OFF 用户
# DB 字节不变（R-T5 字节基线）。只有 TaskGraphStore 真正落库时
# （goal_mode ON）才触发建表。
_GOAL_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS goal_tasks (
    task_id     TEXT    PRIMARY KEY,
    goal_id     TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    depends_on  TEXT    NOT NULL DEFAULT '[]',
    claimed_by  TEXT,
    result      TEXT,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal
    ON goal_tasks(goal_id, status);
"""

# Cache so we don't re-run executescript every call. Keyed by absolute db path.
_ensured: Set[str] = set()
_lock = asyncio.Lock()

# Separate cache + lock for the goal-mode-gated session_goals table (see
# _SESSION_GOALS_DDL above — kept out of the shared _DDL on purpose).
_goals_ensured: Set[str] = set()
_goals_lock = asyncio.Lock()

# Separate cache + lock for goal_tasks (FP-2 WI-1.2, same flag-OFF moat).
_goal_tasks_ensured: Set[str] = set()
_goal_tasks_lock = asyncio.Lock()


async def ensure_memory_v2_tables(db_path: str | Path) -> None:
    """Idempotent: CREATE TABLE IF NOT EXISTS for every memory-v2 table.

    Safe to call concurrently and repeatedly. Caches per-path so the
    second call is a free no-op. Does NOT bump ``PRAGMA user_version``.

    Stage 2: after the CREATE TABLE pass, also runs
    :func:`schema_v2_migrator.ensure_memory_v2_columns` to additively
    ALTER in ``superseded_by`` / ``forgotten_at`` on legacy DBs (fresh
    DBs already have them via ``_DDL``). main.py reads
    :func:`schema_v2_migrator.alter_failures` to disable dependent
    feature flags on ALTER failure (R8/D17 v2).

    Failure modes:
      * CREATE TABLE error → re-raise. Callers (Phase A-E stores) should
        fail loudly when their tables can't be created.
      * Stage 2 ALTER failure → logged and recorded; not raised, so the
        rest of the app boots and the feature flag layer can decide.
    """
    key = str(Path(db_path).resolve())
    if key in _ensured:
        return
    async with _lock:
        if key in _ensured:
            return
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.executescript(_DDL)
            await conn.commit()
        # Stage 2 D1：补齐老库可能缺的列。ALTER 失败不抛，
        # main.py 据 alter_failures() 关 flag。
        try:
            from deskpet.memory.schema_v2_migrator import (
                ensure_memory_v2_columns,
            )

            await ensure_memory_v2_columns(db_path)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "memory_v2 stage2 column migration failed: %s", exc,
            )
        _ensured.add(key)
        log.debug("memory_v2 tables ensured for %s", key)


async def ensure_session_goals_table(db_path: str | Path) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for ``session_goals`` only.

    Deliberately separate from :func:`ensure_memory_v2_tables` so the goal
    table is created ONLY when the goal store actually persists (goal_mode
    ON). This preserves the "flag-OFF → DB bytes unchanged" moat: a user
    with goal_mode OFF (even with memory_v2 ON) never gets this table.
    Per-path cached like the shared ensure. Does NOT bump user_version.
    """
    key = str(Path(db_path).resolve())
    if key in _goals_ensured:
        return
    async with _goals_lock:
        if key in _goals_ensured:
            return
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.executescript(_SESSION_GOALS_DDL)
            await conn.commit()
        _goals_ensured.add(key)
        log.debug("session_goals table ensured for %s", key)


async def ensure_goal_tasks_table(db_path: str | Path) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for ``goal_tasks`` only.

    Deliberately separate from :func:`ensure_memory_v2_tables` and
    :func:`ensure_session_goals_table` so the task-graph table is created
    ONLY when TaskGraphStore actually persists (goal_mode ON).
    This preserves the "flag-OFF → DB bytes unchanged" moat (R-T5).
    Per-path cached like the shared ensure. Does NOT bump user_version.
    """
    key = str(Path(db_path).resolve())
    if key in _goal_tasks_ensured:
        return
    async with _goal_tasks_lock:
        if key in _goal_tasks_ensured:
            return
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.executescript(_GOAL_TASKS_DDL)
            await conn.commit()
        _goal_tasks_ensured.add(key)
        log.debug("goal_tasks table ensured for %s", key)


def _reset_cache_for_tests() -> None:
    """Test helper. Clears the per-path cache so a fresh tmp_path DB
    re-runs DDL. Never call from production code.
    """
    _ensured.clear()
    _goals_ensured.clear()
    _goal_tasks_ensured.clear()
