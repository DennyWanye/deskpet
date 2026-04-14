-- 001_initial.sql — conversation turns (S2 baseline)
--
-- Append-only log of chat exchanges, indexed by (session_id, created_at)
-- so the common "last N turns for this session" query is a cheap seek.
CREATE TABLE IF NOT EXISTS conversation (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_time
    ON conversation(session_id, created_at);
