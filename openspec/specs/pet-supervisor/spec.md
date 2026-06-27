# pet-supervisor Specification

## Purpose
TBD - created by archiving change p5-s1-pet-supervisor-agent. Update Purpose after archive.
## Requirements
### Requirement: Session activity tracking

The system SHALL maintain an in-memory `SessionActivity` structure keyed by `session_id` that records: `last_event_ts`, `status` (one of `idle`/`running`/`permission`/`error`), the most recent 5 AgentEvents (compact form: `{type, ts, name, args_hash, ok, snippet}`), a tool-call signature window (mapping `tool_name + args_hash → consecutive_count`), and `current_iteration`/`max_iterations`. Every AgentEvent emitted from the agent loop SHALL bump the session's `last_event_ts` and append to the recent-events ring buffer at the same code site that already forwards events to the WebSocket. When the user explicitly exits Code mode for a session, the activity entry for that session SHALL be dropped.

#### Scenario: Activity bumps on each agent event

- **GIVEN** a Code-mode session `code-a1b2c3d4` with `last_event_ts = T`
- **WHEN** the agent loop emits a `ToolCallEvent` at time `T+30s`
- **THEN** the SessionActivity entry for that session shows `last_event_ts = T+30s`
- **AND** the recent-events ring buffer contains the new tool call as its most recent entry

#### Scenario: Tool signature window detects repetition

- **WHEN** the same `bash_run` tool is called with identical arguments three times in a row
- **THEN** `tool_signature_window["bash_run:<args_hash>"] = 3`
- **AND** unrelated tool calls in between reset the count

#### Scenario: Activity dropped on Code mode exit

- **GIVEN** SessionActivity has an entry for sid `code-a1b2c3d4`
- **WHEN** the backend processes a `code_mode_exit` IPC message for that sid
- **THEN** the SessionActivity entry for that sid is removed
- **AND** any in-flight supervisor scan for that sid completes harmlessly without re-creating the entry

### Requirement: Watchdog scan loop

The system SHALL run a single Watchdog Loop as an independent `asyncio.Task` started after backend startup completes (≥30s grace period before first scan). The loop SHALL scan all Code-mode sessions every 60 seconds (configurable via `[supervisor].scan_interval_seconds`). For each session, the loop SHALL evaluate trigger conditions: (a) most recent emitted event was a `chat_v2_error` AND not yet handled by supervisor; (b) `now - last_event_ts > 900` seconds (15 minutes, configurable via `[supervisor].stuck_threshold_seconds`) AND status is `running` or `permission`. If a session was scanned by supervisor within the last 12 minutes, the loop SHALL skip it (de-duplication). Sessions are scanned serially per cycle to avoid LLM provider rate-limit pressure.

#### Scenario: Scan triggers on 15-minute inactivity

- **GIVEN** session `code-a1b2c3d4` with `status=running` and `last_event_ts = now - 901s`
- **AND** no supervisor scan has happened for this sid in the last 12 minutes
- **WHEN** the watchdog scan tick fires
- **THEN** the watchdog calls `build_snapshot(sid)` and proceeds to invoke the supervisor LLM

#### Scenario: De-duplication suppresses repeat scan

- **GIVEN** sid `code-a1b2c3d4` was scanned 5 minutes ago
- **WHEN** the watchdog scan tick fires
- **THEN** the watchdog skips this sid this cycle without invoking the supervisor LLM

#### Scenario: Scan triggers on chat_v2_error

- **GIVEN** session `code-a1b2c3d4` just emitted `chat_v2_error` at `last_event_ts = now - 5s`
- **WHEN** the next watchdog tick fires
- **THEN** the watchdog scans this sid even though the inactivity threshold has not passed
- **AND** marks the error as "handled" to avoid re-triggering on the same error

#### Scenario: Watchdog crashes are isolated

- **WHEN** the watchdog loop encounters an unhandled exception during snapshot building or LLM call
- **THEN** the exception is caught at the loop boundary
- **AND** logged as `supervisor_loop_error`
- **AND** the loop continues at the next tick interval
- **AND** the main agent loop is not affected

### Requirement: Snapshot construction

For each session being scanned, the system SHALL build a `SessionSnapshot` dict containing: `session_id`, `status`, `last_activity_age_seconds`, `current_iteration`, `max_iterations`, `last_5_events` (compact), `tool_signature_window`, `todos_state` (each todo's `content`, `status`, `stale_seconds`), `last_error` (if any), `context_token_pressure` (estimated `tokens_used / context_window`, 0.0..1.0), and `user_goal` (first user message of the session, truncated to ≤200 chars). The snapshot SHALL NOT include full conversation transcripts.

#### Scenario: Snapshot omits raw transcripts

- **WHEN** `build_snapshot(sid)` is called for a session with 50 messages
- **THEN** the resulting snapshot dict has no `messages` field
- **AND** the only conversation-derived field is `user_goal` (≤200 chars)

#### Scenario: Snapshot includes signature window

- **GIVEN** the same `bash_run` tool was called 3 times consecutively with identical args
- **WHEN** `build_snapshot(sid)` is called
- **THEN** snapshot's `tool_signature_window` includes the entry mapping `bash_run:<hash> → 3`

### Requirement: Supervisor LLM call

When the watchdog decides to scan a session, the system SHALL make a separate LLM call (independent from the main agent's LLM) using a configured `[supervisor].llm_provider` (default: same OpenAI-compatible endpoint as main, model selectable in settings). The call SHALL use a Chinese system prompt instructing the model to act as a supervisor agent and SHALL request strict JSON output matching `SupervisorAction` schema. The LLM call SHALL have a 30-second hard timeout enforced via `asyncio.wait_for`. On any failure (timeout, JSON parse error, network error, provider error), the system SHALL synthesize a default `SupervisorAction(action="wait", severity="green", diagnosis="supervisor unavailable")` and continue.

#### Scenario: Successful LLM call returns parsed action

- **WHEN** the supervisor LLM returns valid JSON `{"action":"nudge", "severity":"yellow", "diagnosis":"...", "hint_for_main_agent":"...", "user_message":"...", "suggested_buttons":["让它继续","我自己看看"]}`
- **THEN** the system parses it into a `SupervisorAction` dataclass
- **AND** dispatches the action

#### Scenario: Hard timeout

- **WHEN** the supervisor LLM call exceeds 30 seconds
- **THEN** `asyncio.wait_for` raises `asyncio.TimeoutError`
- **AND** the system logs `supervisor_llm_timeout` with sid
- **AND** falls back to `SupervisorAction(action="wait", severity="green")`

#### Scenario: Malformed JSON

- **WHEN** the LLM returns text that does not parse as JSON or fails schema validation
- **THEN** the system logs `supervisor_llm_invalid_output` with the raw text (truncated)
- **AND** falls back to `SupervisorAction(action="wait", severity="green")`

### Requirement: SupervisorAction protocol

The `SupervisorAction` schema SHALL define exactly these fields: `action` ∈ `{wait, nudge, ask_user, cancel}`, `severity` ∈ `{green, yellow, red}`, `diagnosis` (string, ≤200 chars), `hint_for_main_agent` (string, only used when action=nudge, ≤500 chars), `user_message` (string, only used when action ≠ wait, ≤120 chars, displayed in pet bubble), `suggested_buttons` (string array, ≤2 items, only used when action ≠ wait). Initial implementation SHALL implement `wait`, `nudge`, and `ask_user`; if the LLM returns `cancel`, the system SHALL treat it as `ask_user` with `user_message="任务可能已无法恢复，是否要中断？"`.

#### Scenario: cancel action coerced to ask_user

- **WHEN** the supervisor LLM returns `{"action":"cancel", ...}`
- **THEN** the dispatcher rewrites it to `{"action":"ask_user", "user_message":"任务可能已无法恢复，是否要中断？", "suggested_buttons":["中断","让它继续试"]}`
- **AND** logs the original cancel decision for future review

#### Scenario: action=wait does not broadcast

- **WHEN** SupervisorAction has `action=wait`
- **THEN** no `supervisor_alert` ws event is broadcast
- **AND** session severity in pet UI remains determined by raw severity_score

### Requirement: supervisor_alert broadcast

When SupervisorAction has `action ∈ {nudge, ask_user}`, the system SHALL broadcast a `supervisor_alert` ws event to all connected control WebSockets with payload `{session_id, severity, action, diagnosis, user_message, suggested_buttons}`. Broadcasting SHALL reuse the same multi-WS pattern as `_todo_broadcaster` so both the pet window and the code panel receive the event.

#### Scenario: Both windows receive the alert

- **GIVEN** pet (main) and code-panel WebSockets are both connected
- **WHEN** supervisor decides nudge for sid `code-a1b2c3d4`
- **THEN** both WebSockets receive `{type: "supervisor_alert", payload: {...}}`

#### Scenario: Disconnected WebSocket failure does not block other sends

- **GIVEN** a control WebSocket has died but is still in the connections dict
- **WHEN** broadcasting `supervisor_alert`
- **THEN** the failed send is logged and skipped
- **AND** other WebSockets still receive the event

### Requirement: Nudge queue with reentrancy safety

The system SHALL provide a per-session nudge queue (`nudge_queue.py`) protected by `asyncio.Lock`. The queue SHALL support `push(sid, hint)`, `pop_all(sid) → list[Hint]`, `peek(sid) → bool`, and `clear(sid)` operations. The queue SHALL cap each session's pending hints at 3 (configurable via `[supervisor].max_hints_per_session`). When pushing to a full queue, the oldest hint SHALL be dropped. When the user exits Code mode for a session, `clear(sid)` SHALL be called.

#### Scenario: Push beyond cap drops oldest

- **GIVEN** queue for sid `code-a1b2c3d4` already has 3 hints
- **WHEN** a fourth `push(sid, hint4)` is called
- **THEN** the oldest hint (hint1) is dropped
- **AND** the queue contains [hint2, hint3, hint4]

#### Scenario: pop_all consumes all hints

- **GIVEN** queue has [hint1, hint2]
- **WHEN** `pop_all(sid)` is called
- **THEN** the result is [hint1, hint2]
- **AND** subsequent `peek(sid)` returns False

#### Scenario: Clear on Code mode exit

- **GIVEN** queue has 2 hints for sid `code-a1b2c3d4`
- **WHEN** the backend processes `code_mode_exit` for that sid
- **THEN** `nudge_queue.clear(sid)` is invoked
- **AND** the queue for that sid is empty

### Requirement: Nudge follow-up scheduling

When SupervisorAction has `action=nudge`, the system SHALL push the hint into the nudge queue for that sid AND attach an `add_done_callback` to the current `_chat_inflight[sid]` task (if any). The callback SHALL check whether (a) a new task has replaced the original (user retry happened) — in which case do nothing, the new task will consume hints on its own — and (b) the queue still has hints, in which case schedule a follow-up `_run_chat` task with a synthesized trigger text (`<<supervisor_followup>>`). The follow-up task SHALL pop hints from the queue and inject them as a system message at the top of the system stack in the message construction phase.

#### Scenario: Normal nudge follow-up

- **GIVEN** sid `code-a1b2c3d4` is running task_A
- **WHEN** supervisor pushes a hint and task_A completes
- **THEN** done_callback fires
- **AND** since `_chat_inflight[sid] == task_A` (still the same), the callback schedules a new follow-up task_B
- **AND** task_B's `_msgs` includes a system message with the supervisor hint at the top

#### Scenario: User retry preempts follow-up

- **GIVEN** sid `code-a1b2c3d4` is running task_A
- **WHEN** supervisor pushes a hint, then user re-types and triggers cancel-on-retry creating task_C, then task_A completes
- **THEN** done_callback fires for task_A
- **AND** sees `_chat_inflight[sid] == task_C` (not the same as task_A) — skips scheduling follow-up
- **AND** task_C, when constructing its `_msgs`, pops the queued hint and injects it normally

#### Scenario: System message marks supervisor origin

- **WHEN** a hint is injected into `_msgs`
- **THEN** the system message text begins with `[Supervisor]` prefix
- **AND** carries metadata flag `is_supervisor_hint: True` for downstream tooling

### Requirement: Hint persistence and audit

The system SHALL persist every supervisor hint that is dispatched (whether nudge actually injected or just ask_user displayed) to a SessionDB table `supervisor_hints` with schema `(id INTEGER PRIMARY KEY, session_id TEXT, alert_id TEXT, hint_text TEXT, action TEXT, severity TEXT, diagnosis TEXT, user_button TEXT, ts INTEGER)`. The ``action`` column accepts these values: ``nudge`` (supervisor decided nudge), ``ask_user`` (supervisor decided ask_user), ``cancel_coerced`` (supervisor said cancel; coerced to ask_user — original recorded for audit), ``dispatched`` (a queued hint was actually injected into a chat task's messages), ``user_choice`` (user clicked a bubble button). Schema migration SHALL bump SessionDB to v14.

#### Scenario: Each nudge persists one row

- **WHEN** supervisor decides nudge with hint "switch pip mirror"
- **THEN** a new row is inserted into `supervisor_hints` with `action="nudge"`, `severity="yellow"`, `hint_text="switch pip mirror"`, `session_id="code-a1b2c3d4"`, `ts=now()`

#### Scenario: ask_user also persists

- **WHEN** supervisor decides ask_user with user_message "permission seems ignored"
- **THEN** a new row is inserted with `action="ask_user"`, `hint_text="permission seems ignored"`, `severity` set accordingly

#### Scenario: Database migration to v14

- **GIVEN** SessionDB is at v13
- **WHEN** backend starts with this change deployed
- **THEN** migration `003_p5s1_supervisor_hints.sql` runs
- **AND** `supervisor_hints` table is created
- **AND** schema_version is bumped to 14

### Requirement: Supervisor disable toggle

The system SHALL expose a configuration toggle `[supervisor].enabled` (default: `true`) that fully disables supervisor behavior when set to `false`: the watchdog scan loop SHALL not be started; no `supervisor_alert` events SHALL be broadcast; nudge queue SHALL remain empty. The toggle SHALL be readable and writable via the existing settings IPC surface.

#### Scenario: Disabled supervisor takes no actions

- **GIVEN** `[supervisor].enabled = false` at backend startup
- **WHEN** a session goes idle for 30 minutes
- **THEN** no supervisor_alert is broadcast
- **AND** no rows are inserted into supervisor_hints
- **AND** the watchdog task object does not exist

#### Scenario: Toggle visible in settings UI

- **WHEN** user opens Settings panel
- **THEN** there is a labeled toggle "桌宠 supervisor (自动监督卡住任务)" reflecting current state
- **AND** flipping it issues `supervisor_toggle` IPC and persists to config

### Requirement: Tool-level hard timeout

The tool dispatch layer SHALL enforce a per-tool hard timeout via `asyncio.wait_for`. Default SHALL be 60 seconds; specific tools (e.g. `bash_run`) MAY override via tool metadata (e.g. 300 seconds). On timeout the dispatcher SHALL emit a `tool_result` event with `ok=false` and `error="tool_timeout"`, NOT raise an exception that breaks the loop. The supervisor LLM has its own 30-second timeout (separate concern).

#### Scenario: bash_run timeout returns failure result

- **GIVEN** `bash_run` is configured with timeout=300s
- **WHEN** a `bash_run` invocation runs longer than 300 seconds
- **THEN** dispatcher cancels the tool task
- **AND** emits tool_result with `ok=false`, `error="tool_timeout"`, `tool="bash_run"`
- **AND** the agent loop continues to the next iteration with this failure result

#### Scenario: MCP tool timeout

- **WHEN** an MCP tool exceeds the default 60-second timeout
- **THEN** the dispatcher cancels and returns `tool_timeout` failure result without breaking the loop

