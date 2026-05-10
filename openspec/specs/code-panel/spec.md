# code-panel Specification

## Purpose
TBD - created by archiving change p4-s23-code-panel-multisession. Update Purpose after archive.
## Requirements
### Requirement: Code mode MUST surface a dedicated Tauri webview window
On entering Code mode, the system SHALL show a second Tauri window
(`code-panel`, 1024×720, resizable) and on exiting Code mode it SHALL
hide that window. The Toolbar SHALL also expose a 💬 toggle so users
can hide/restore the panel without leaving Code mode.

#### Scenario: Click 🔧 → folder picker → panel auto-opens
- **GIVEN** the user clicks 🔧 in the pet's toolbar
- **AND** picks a project folder via the native dialog
- **WHEN** backend confirms `code_mode_state.enabled=true`
- **THEN** the code-panel window SHALL be visible and focused

#### Scenario: Toolbar 💬 toggles visibility
- **GIVEN** Code mode is active and the panel window is visible
- **WHEN** the user clicks 💬
- **THEN** the panel SHALL hide without changing Code mode state
- **WHEN** the user clicks 💬 again
- **THEN** the panel SHALL re-show with full state preserved

### Requirement: Code panel MUST stream per-session events isolated by session_id
Every backend WS event emitted from a chat run (chat_response,
chat_v2_final, chat_v2_error, tool_call, tool_result, code_todo_update)
SHALL carry `payload.session_id`. The frontend dispatcher SHALL route
each event into the matching slice of the sessions store, never
clobbering a sibling session's state.

#### Scenario: Two parallel sessions don't bleed
- **GIVEN** sessions A and B are both enabled
- **WHEN** A's chat emits a tool_call event
- **THEN** only A's tile / chat panel slot updates
- **AND** B's slot remains unchanged

### Requirement: Code panel MUST tolerate same-session retry
When a chat_v2 message arrives for a session_id that already has an
in-flight chat task, the backend SHALL cancel the prior task before
spawning the new one. This prevents stale tool calls from leaking
into the panel after the user retries a failed turn.

#### Scenario: Rapid retry cancels prior turn
- **GIVEN** session A has an in-flight chat task that's mid-tool-loop
- **WHEN** the user sends another chat_v2 for session A
- **THEN** the prior task SHALL be cancelled (CancelledError) before
  the new one starts

### Requirement: Code mode MUST support a multi-session dashboard view
The panel SHALL render a grid of all currently-enabled code sessions
when the user clicks the dashboard button. Each tile SHALL show the
project name, path, todo progress, status pill, last assistant
message, and last activity time. Clicking a tile SHALL switch the
panel's active session.

#### Scenario: Dashboard shows N tiles for N sessions
- **GIVEN** N code sessions are enabled
- **WHEN** the user clicks "⊞ 仪表盘"
- **THEN** the panel SHALL render N tiles in a responsive 4-column grid

#### Scenario: New project creates a new session
- **WHEN** the user clicks "+ 新项目" and picks a folder
- **THEN** a new code session SHALL be created with a unique base sid
- **AND** the panel SHALL switch to it as the active session

### Requirement: Code panel MUST NOT collide with the pet's WebSocket
The code-panel window's WS connection SHALL use a distinct
session_id (`code-panel-main`) so it doesn't kick the pet's
`default` connection out of `_control_connections`.

#### Scenario: Both windows stay connected
- **GIVEN** the pet shell holds a control_ws on session_id="default"
- **WHEN** the code panel opens its own control_ws
- **THEN** the pet's connection SHALL remain alive
- **AND** todo_write broadcasts SHALL fan out to BOTH windows

### Requirement: Outbound chat sends MUST be concurrency-limited
The frontend SHALL throttle outbound chat_v2 messages so no more
than `inflight_max=2` in-flight LLM round-trips run concurrently.
Excess messages SHALL queue with visible "等待中: N" status.

#### Scenario: 5 simultaneous tile sends respect the cap
- **GIVEN** 5 code sessions all send chat_v2 within 100ms
- **THEN** at most 2 SHALL hit chinzy at once
- **AND** the remaining 3 SHALL queue + display "等待中: 3"

