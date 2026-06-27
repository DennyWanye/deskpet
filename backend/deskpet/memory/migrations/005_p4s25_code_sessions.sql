-- 005_p4s25_code_sessions.sql — Persist code-mode project enrollments (P4-S25 B4)
--
-- Before this migration the CodeModeManager kept project state purely
-- in-memory (`self._states: dict`), so every backend restart wiped the
-- project list and users had to re-add directories. Chat history was
-- still in `messages` (keyed by code_session_id) but the code panel
-- had no idea what the projects were called or where their roots
-- pointed, so they didn't show up in the sidebar / dashboard.
--
-- This table is the persistence layer:
--   * Primary key by base_session_id (one row per "tab" the user
--     has open in the code panel)
--   * code_session_id is the sha1-derived key under which messages /
--     todos already live
--   * project_root is the absolute path; project_name the leaf dir
--     name shown in the UI
--
-- CodeModeManager.load_persisted(sdb) reads this on startup and
-- repopulates the in-memory map; CodeModeManager.enter() upserts;
-- the new code_session_delete IPC deletes here too.

CREATE TABLE IF NOT EXISTS code_sessions (
    base_session_id  TEXT PRIMARY KEY,
    code_session_id  TEXT NOT NULL,
    project_root     TEXT NOT NULL,
    project_name     TEXT NOT NULL,
    created_at       REAL NOT NULL DEFAULT (julianday('now')),
    last_active_at   REAL NOT NULL DEFAULT (julianday('now'))
);

CREATE INDEX IF NOT EXISTS idx_code_sessions_active
    ON code_sessions(last_active_at DESC);

PRAGMA user_version = 13;
