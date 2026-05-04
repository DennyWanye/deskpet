## ADDED Requirements

### Requirement: chat_stream runs tool_use loop

The `agent.chat_stream(...)` function SHALL run a multi-turn tool_use loop instead of regex `[tool:xxx]` tag detection. The loop SHALL terminate on (a) LLM returns final text without tool_use, (b) 25-turn limit reached, (c) user cancels via IPC, or (d) all tool calls in a turn are denied by permission gate.

#### Scenario: Single tool call resolved
- **WHEN** user sends "create todo.txt with 'milk' on desktop" and LLM returns `tool_calls=[desktop_create_file(name="todo.txt", content="milk")]`
- **THEN** agent_loop awaits permission gate, executes the tool, appends `tool_result` message, calls LLM again, and streams the final assistant message to frontend

#### Scenario: Multi-turn chained calls
- **WHEN** LLM returns `read_file("a.txt")` then on next turn returns `write_file("b.txt", <content>)`
- **THEN** loop runs 2 turns, both tool_results are appended in order, third turn returns final text

#### Scenario: 25-turn limit
- **WHEN** LLM keeps requesting tool calls without returning final text
- **THEN** loop aborts at turn 25 and emits `{type: "loop_aborted", reason: "max_turns"}` to frontend

#### Scenario: User cancellation mid-loop
- **WHEN** frontend sends `chat_cancel` IPC during turn 3
- **THEN** in-flight tool execution is cancelled (best-effort), loop exits, agent emits `{type: "cancelled"}`

### Requirement: Backward compat with regex fallback

When provider config has `tool_use_protocol: "regex"` (legacy) OR provider does not advertise tool_use support, the loop SHALL fall back to the existing `[tool:xxx]` parser without breakage.

#### Scenario: Provider lacks tool_use
- **WHEN** provider is `ollama:llama3` (no tool_use field) and config sets `tool_use_protocol: "regex"`
- **THEN** agent uses old `_parse_tool_calls_from_text` path; existing 17 hardcoded tools work; existing 679 tests pass

#### Scenario: Auto-detect protocol
- **WHEN** provider config has `tool_use_protocol: "auto"` and provider model has `supports_tools=True`
- **THEN** agent uses native tool_calls; otherwise falls back to regex

### Requirement: Streaming preserves tool events

The chat_stream WebSocket SHALL emit these event types in order during a tool-using turn: `assistant_chunk` (LLM thinking text) → `tool_use_request` (with permission summary) → `tool_use_result` (after execution) → next iteration → final `assistant_chunk` + `done`.

#### Scenario: Frontend renders tool steps
- **WHEN** loop runs `read_file("foo.txt")` and returns final text "the file says hello"
- **THEN** frontend receives: `assistant_chunk("Let me read the file...")`, `tool_use_request({name:"read_file", params:{path:"foo.txt"}})`, `tool_use_result({content:"hello"})`, `assistant_chunk("the file says hello")`, `done`

#### Scenario: Permission denial event
- **WHEN** user denies a tool_use_request popup
- **THEN** agent emits `tool_use_result({error: "permission denied"})` and feeds this back to LLM as the tool_result content; LLM typically apologizes and stops

### Requirement: Loop budget enforcement

The agent loop SHALL enforce per-turn time budget (default 60s for LLM call, 30s for each tool) and total session budget (default 5 minutes wall-clock). Configurable via `[agent_loop]` in config.toml.

#### Scenario: Per-tool timeout
- **WHEN** `run_shell` exceeds its 30s budget mid-execution
- **THEN** subprocess is killed and `tool_use_result({error: "timeout"})` is appended; loop continues

#### Scenario: Total session budget exhausted
- **WHEN** wall-clock exceeds 300s during turn 12
- **THEN** loop aborts with `{type: "loop_aborted", reason: "session_budget"}`
