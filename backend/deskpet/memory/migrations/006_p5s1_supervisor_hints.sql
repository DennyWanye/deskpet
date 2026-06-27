-- 006_p5s1_supervisor_hints.sql — Pet supervisor audit log (P5-S1)
--
-- The supervisor LLM watchdog (backend/agent/supervisor.py) periodically
-- inspects stuck Code-mode sessions and dispatches a SupervisorAction
-- (wait | nudge | ask_user | cancel-coerced-to-ask_user). When the action
-- is anything other than ``wait``, we persist a row here for:
--   * Debugging — "why did the agent suddenly switch tactics on iter 12?"
--   * UI — count badge on Settings panel ("today supervisor stepped in N times")
--   * Cost — sum hint_text length to estimate supervisor LLM token spend
--
-- Schema is intentionally narrow. ``alert_id`` correlates with the
-- ``supervisor_alert`` ws event so the frontend bubble click handler
-- can pin a specific alert when the user picks one of the suggested
-- buttons (then we INSERT a follow-up ``user_choice`` row sharing the
-- same alert_id).

CREATE TABLE IF NOT EXISTS supervisor_hints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    alert_id    TEXT NOT NULL,
    hint_text   TEXT NOT NULL,
    action      TEXT NOT NULL,            -- nudge | ask_user | user_choice | cancel_coerced
    severity    TEXT NOT NULL,            -- green | yellow | red
    diagnosis   TEXT,                     -- supervisor's one-line diagnosis (≤200 chars)
    user_button TEXT,                     -- populated for action=user_choice rows
    ts          INTEGER NOT NULL          -- unix epoch (seconds)
);

CREATE INDEX IF NOT EXISTS idx_supervisor_hints_sid_ts
    ON supervisor_hints(session_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_supervisor_hints_alert
    ON supervisor_hints(alert_id);

PRAGMA user_version = 14;
