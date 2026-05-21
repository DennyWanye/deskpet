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
    last_recalled  REAL
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
"""

# Cache so we don't re-run executescript every call. Keyed by absolute db path.
_ensured: Set[str] = set()
_lock = asyncio.Lock()


async def ensure_memory_v2_tables(db_path: str | Path) -> None:
    """Idempotent: CREATE TABLE IF NOT EXISTS for every memory-v2 table.

    Safe to call concurrently and repeatedly. Caches per-path so the
    second call is a free no-op. Does NOT bump ``PRAGMA user_version``.

    Failure modes:
      * SQLite error → re-raise. Callers (Phase A-E stores) should fail
        loudly when their tables can't be created — the alternative is
        silent NULL behaviour which is worse to debug.
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
        _ensured.add(key)
        log.debug("memory_v2 tables ensured for %s", key)


def _reset_cache_for_tests() -> None:
    """Test helper. Clears the per-path cache so a fresh tmp_path DB
    re-runs DDL. Never call from production code.
    """
    _ensured.clear()
