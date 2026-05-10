# Spec: agent-loop / error-taxonomy

## ADDED Requirements

### Requirement: Tool error classification

The agent loop SHALL classify every tool execution error into one of three classes — `TransientToolError`, `PermanentToolError`, `HallucinationError` — and decide whether to retry, break out, or escalate based on the class.

Classifier input: the tool result dict OR raised exception. Output: a class type.

#### Scenario: Missing-required-parameter is permanent

- **GIVEN** a tool returns `{"ok": false, "error": "missing required parameter: path"}`
- **WHEN** classifier sees that error
- **THEN** classifier SHALL return `PermanentToolError`

#### Scenario: Network timeout is transient

- **GIVEN** a tool returns `{"ok": false, "error": "timeout"}` or raises `httpx.ReadTimeout`
- **WHEN** classifier sees that error
- **THEN** classifier SHALL return `TransientToolError`

#### Scenario: Unknown error string defaults to transient

- **GIVEN** a tool returns `{"ok": false, "error": "<some string we never saw>"}`
- **WHEN** classifier sees that error
- **THEN** classifier SHALL return `TransientToolError` (conservative default — better to waste a retry than give up too early)

#### Scenario: Tool not found is hallucination

- **GIVEN** the LLM emits a tool_call with a name not in the registry
- **WHEN** dispatch detects unknown tool name
- **THEN** dispatch SHALL produce `{"ok": false, "error": "tool_not_found", "available_tools": [...]}` AND classifier SHALL return `HallucinationError`

#### Scenario: Permanent error breaks the loop early

- **GIVEN** AgentLoop is mid-iteration with `max_iterations=50` and only 2 iterations spent
- **AND** the latest tool result classifies as `PermanentToolError`
- **WHEN** AgentLoop processes that result
- **THEN** AgentLoop SHALL emit `ErrorEvent(reason="permanent_tool_error", detail=...)` AND return immediately
- **AND** AgentLoop SHALL NOT iterate further (saving up to 48 wasted iterations)

#### Scenario: Transient error allows normal ReAct continuation

- **GIVEN** AgentLoop receives a tool result classified as `TransientToolError`
- **WHEN** AgentLoop processes that result
- **THEN** AgentLoop SHALL feed the error back to the LLM as a regular tool_result message (no break)
- **AND** the LLM is free to retry with adjusted args or move on

#### Scenario: Hallucination error notifies supervisor

- **GIVEN** AgentLoop receives a tool result classified as `HallucinationError`
- **WHEN** AgentLoop processes that result
- **THEN** AgentLoop SHALL emit `ErrorEvent(reason="hallucination", detail=..., tool_name=...)` AND return
- **AND** the orchestrator (Phase 4) SHALL pick this up and route to supervisor for diagnosis (not retry blindly)
