## ADDED Requirements

### Requirement: Supervisor state cleanup on Code mode exit

When a session exits Code mode (via the existing `code_mode_exit` IPC handler), the system SHALL synchronously clear all supervisor-related state for that session: `nudge_queue.clear(sid)`, `session_activity.drop(sid)`, and any queued `add_done_callback` for that sid SHALL be allowed to no-op when fired (the callback already checks task identity, so cancellation is automatic via the standard cancel-on-exit path). This SHALL run before the existing `code_mode_state` ws broadcast announcing the exit.

#### Scenario: Exit clears nudge queue

- **GIVEN** sid `code-a1b2c3d4` has 2 hints in nudge_queue and an entry in session_activity
- **WHEN** the user clicks "退出 Code 模式" and backend processes `code_mode_exit`
- **THEN** `nudge_queue.clear("code-a1b2c3d4")` is called
- **AND** `session_activity.drop("code-a1b2c3d4")` is called
- **AND** `code_mode_state` ws event is broadcast with `enabled=false`
- **AND** subsequent watchdog scans skip this sid (no entry in session_activity)

#### Scenario: Re-entry after exit starts fresh

- **GIVEN** sid was cleaned up via Code mode exit
- **WHEN** the user re-enters Code mode for the same project (same project_root)
- **THEN** session_activity is recreated empty
- **AND** nudge_queue starts empty
- **AND** supervisor watchdog treats this as a brand-new session (no prior scan timestamp)
