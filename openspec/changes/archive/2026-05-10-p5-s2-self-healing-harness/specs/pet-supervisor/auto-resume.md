# Spec: pet-supervisor / auto-resume

## ADDED Requirements

### Requirement: AutoResume orchestrator closes the supervisor → main-agent loop

When the agent loop fails recoverably (max_iterations / circuit_open / permanent_tool_error), the AutoResumeOrchestrator SHALL invoke the supervisor LLM, take its hint, and **automatically spawn a new chat task** on the same session — no user input required — up to `max_auto_resume_attempts` times before escalating.

#### Scenario: max_iterations triggers auto-resume

- **GIVEN** `auto_resume_enabled = true` and `max_auto_resume_attempts = 2`
- **AND** an agent loop emits `ErrorEvent(reason="max_iterations")` for session "s1"
- **AND** session "s1"'s `auto_resume_attempts` is currently 0
- **WHEN** orchestrator handles the error
- **THEN** orchestrator SHALL call `supervisor.diagnose("s1", snapshot)`
- **AND** if supervisor returns `action="nudge"` with a `hint_for_main_agent`, orchestrator SHALL invoke `chat_dispatcher` to spawn a fresh chat task with the hint injected as a system message
- **AND** `auto_resume_attempts` SHALL increment to 1
- **AND** an `auto_resume_started` ws event SHALL be emitted

#### Scenario: Permanent tool error triggers auto-resume

- **GIVEN** an agent loop emits `ErrorEvent(reason="permanent_tool_error", detail=...)` early (e.g. iteration 2)
- **WHEN** orchestrator handles it
- **THEN** orchestrator SHALL behave identically to the max_iterations case — diagnose + spawn — because permanent errors are also "human would normally intervene here" cases that the supervisor should fix in-loop

#### Scenario: Circuit-open triggers auto-resume

- **GIVEN** dispatch returns `error: "circuit_open"` and the agent loop emits `ErrorEvent(reason="circuit_open", tool="write_file")`
- **WHEN** orchestrator handles it
- **THEN** orchestrator SHALL call supervisor with the snapshot **including** the circuit_open detail, so supervisor can hint "switch to edit_file" or similar

#### Scenario: Supervisor decides ask_user → no auto-spawn

- **GIVEN** orchestrator is handling an error
- **AND** supervisor returns `action="ask_user"` (severity=red, e.g. user data at risk)
- **WHEN** orchestrator processes the action
- **THEN** orchestrator SHALL emit `supervisor_alert` ws event (the existing P5-S1 path) — popup user
- **AND** orchestrator SHALL NOT spawn a new chat task
- **AND** `auto_resume_attempts` SHALL NOT increment

#### Scenario: Max attempts caps the resume cycle

- **GIVEN** `max_auto_resume_attempts = 2` and `auto_resume_attempts` for session "s1" is already 2
- **AND** another error fires
- **WHEN** orchestrator handles it
- **THEN** orchestrator SHALL NOT call supervisor again
- **AND** orchestrator SHALL emit `auto_resume_exhausted` ws event with the original error details and cumulative attempt count
- **AND** the frontend SHALL show the popup ("agent 自愈失败 N 次，请人工介入")

#### Scenario: User new message resets the attempt counter

- **GIVEN** `auto_resume_attempts` for session "s1" is 2 (exhausted)
- **WHEN** the user sends a fresh chat message
- **THEN** orchestrator SHALL reset `auto_resume_attempts` to 0 BEFORE the new task starts
- **AND** subsequent failures may auto-resume up to 2 more times (the user implicitly granted a fresh budget)

#### Scenario: Auto-resume is gated by config

- **GIVEN** `auto_resume_enabled = false`
- **AND** an agent loop emits any recoverable error
- **WHEN** orchestrator would normally fire
- **THEN** orchestrator SHALL skip diagnose + spawn AND fall through to the existing P5-S1 popup path
- **AND** `auto_resume_attempts` SHALL NOT increment

### Requirement: Auto-resume audit trail

Every auto-resume event SHALL be recorded in the `supervisor_hints` table with `action='auto_resumed'` so users can later inspect "what did the bot do behind my back".

#### Scenario: Audit row written on every spawn

- **GIVEN** orchestrator successfully spawns a fresh chat task
- **WHEN** spawn completes (regardless of new task's outcome)
- **THEN** a row SHALL be appended to `supervisor_hints` with:
  - `session_id` = original sid
  - `alert_id` = the supervisor alert that triggered this
  - `action` = `'auto_resumed'`
  - `hint_text` = the hint injected into the new task
  - `severity` = supervisor's severity
  - `ts` = current time

## MODIFIED Requirements

### Requirement: Supervisor watchdog has a fourth trigger rule

The `WatchdogLoop._should_trigger` method SHALL gain trigger rule (d): if `tool_signature_window` shows the same `(tool_name, args_hash)` ≥3 times in the recent 5 events, fire even if no error event was emitted (proactive death-loop detection).

#### Scenario: Repeated identical tool call wakes watchdog

- **GIVEN** session_activity for "s1" has tool_signature_window:
  ```
  [("write_file", "{}"), ("write_file", "{}"), ("write_file", "{}"), ("read_file", "{path: 'x'}"), ...]
  ```
- **WHEN** watchdog tick scans s1
- **THEN** watchdog SHALL trigger supervisor regardless of `last_event_ts` age (this case can fire mid-flight, not just after stuck)
