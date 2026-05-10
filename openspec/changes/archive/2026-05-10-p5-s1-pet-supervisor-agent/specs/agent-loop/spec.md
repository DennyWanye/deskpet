## ADDED Requirements

### Requirement: Agent events feed SessionActivity

For Code-mode sessions, every `AgentEvent` emitted by `AgentLoop.run(...)` (specifically `AssistantMessageEvent`, `ToolCallEvent`, `ToolResultEvent`, `FinalEvent`, `ErrorEvent`) SHALL be observed at the same call site that already forwards events to the WebSocket (`backend/main.py` around line 2197 onward). The observation hook SHALL update the `SessionActivity` table for that session: bumping `last_event_ts`, appending to the recent-events ring buffer (cap 5), and updating the tool-call signature window for `ToolCallEvent` entries. The agent loop's internal logic (`agent/agent_loop.py`) SHALL NOT be modified.

#### Scenario: Tool call signature window updates

- **GIVEN** a Code-mode session emits a `ToolCallEvent(name="bash_run", args={"cmd":"pip install foo"})`
- **WHEN** the WS forwarder processes the event
- **THEN** SessionActivity for that sid records the event in its ring buffer
- **AND** `tool_signature_window["bash_run:<args_hash>"]` increments by 1

#### Scenario: Companion-mode events do not feed activity

- **GIVEN** a non-Code-mode session emits AgentEvents
- **THEN** SessionActivity has no entry for that sid (supervisor only watches Code mode)

### Requirement: Supervisor hint injection at message build time

When constructing the `_msgs` array for a chat run (around `backend/main.py:2089`), the system SHALL call `nudge_queue.pop_all(sid)` BEFORE invoking `_agent.run(...)`. Any returned hints SHALL be formatted as a single system message prefixed with `[Supervisor]` and inserted into `_msgs` at the position immediately after the existing system stack (using the same insertion pattern as P4-S25 plan injection). The injection SHALL happen for every chat task that runs (user-initiated or supervisor-follow-up), so hints don't get stranded if a follow-up race resolves to a user retry.

#### Scenario: Hint injected on follow-up task

- **GIVEN** nudge_queue for sid has hint `"换 pip 源"`
- **WHEN** the supervisor follow-up task starts and constructs `_msgs`
- **THEN** `nudge_queue.pop_all(sid)` returns the hint
- **AND** `_msgs` contains a system message at the top of the system stack: `[Supervisor] 换 pip 源`
- **AND** the queue is empty after the pop

#### Scenario: Hint injected when user retries before follow-up scheduled

- **GIVEN** nudge_queue has a hint, and the user retries (creating new chat task) before supervisor's done_callback fires
- **WHEN** the new chat task constructs `_msgs`
- **THEN** the hint is popped and injected normally
- **AND** the supervisor's done_callback (when it later fires) sees the queue empty and does not schedule a redundant follow-up

### Requirement: Done callback for supervisor follow-up

When a chat task is created and stored in `_chat_inflight[sid]`, the system SHALL register an `add_done_callback` that schedules a `_maybe_supervisor_followup(sid, ws)` coroutine. The coroutine SHALL: (a) check whether `_chat_inflight[sid]` has been replaced by a newer task (user retry); if so, return without action. (b) Otherwise, check `nudge_queue.peek(sid)`; if empty, return. (c) Otherwise, create a new `_run_chat` task with synthesized trigger text `<<supervisor_followup>>` (so the chat handler can recognize and skip user-echo), assign it to `_chat_inflight[sid]`, and let it pick up the queued hint via the standard injection path.

#### Scenario: Normal follow-up

- **GIVEN** task_A is running for sid and nudge_queue has a hint
- **WHEN** task_A completes successfully
- **THEN** done_callback fires
- **AND** sees `_chat_inflight[sid] == task_A` (still the same)
- **AND** schedules a new task_B with trigger `<<supervisor_followup>>`
- **AND** sets `_chat_inflight[sid] = task_B`

#### Scenario: User retry preempts callback

- **GIVEN** task_A is running, supervisor pushes hint, user retries (cancel task_A → task_C created)
- **WHEN** task_A's done_callback fires (task_A was cancelled)
- **THEN** the callback sees `_chat_inflight[sid] == task_C ≠ task_A`
- **AND** returns without action
- **AND** task_C consumes the hint via standard injection (no double-handling)

#### Scenario: Empty queue is no-op

- **GIVEN** task_A completes normally with no hints queued
- **WHEN** done_callback fires
- **THEN** `nudge_queue.peek(sid)` returns False
- **AND** the callback returns without scheduling any task
