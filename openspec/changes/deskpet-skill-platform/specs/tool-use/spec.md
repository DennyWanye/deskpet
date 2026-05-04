## ADDED Requirements

### Requirement: LLM Provider supports tool_use API

The system SHALL provide a `chat_with_tools(messages, tools)` method on every LLM provider that implements the OpenAI tool_calls protocol. The method SHALL accept a list of tool descriptors (name, description, JSON schema) and return either a final text response OR a tool_calls list to execute.

#### Scenario: Provider returns tool_calls when tools are useful
- **WHEN** `chat_with_tools(messages=[{role: 'user', content: 'create a file todo.txt on my desktop'}], tools=[desktop_create_file_schema])` is called
- **THEN** the provider returns a response with `tool_calls=[{id: 'call_1', name: 'desktop_create_file', arguments: {name: 'todo.txt', content: '...'}}]` and no final text yet

#### Scenario: Provider returns final text when no tools needed
- **WHEN** `chat_with_tools(messages=[{role: 'user', content: 'hello'}], tools=[...])` is called
- **THEN** the provider returns a response with `content="Hi! How can I help?"` and `tool_calls=[]`

#### Scenario: Provider streams tool_calls incrementally
- **WHEN** the underlying API streams tool_call deltas (OpenAI SSE format)
- **THEN** the provider accumulates partial JSON of arguments correctly until the call is complete

#### Scenario: Provider falls back gracefully when tools unsupported
- **WHEN** `chat_with_tools` is called against a provider/model that does not support tool_calls API
- **THEN** the provider raises `ToolUseNotSupported` with provider/model info; the agent loop catches and falls back to regex-based `[tool:xxx]` detection

### Requirement: ToolUseAgent runs tool_use loop until completion

The system SHALL provide a `ToolUseAgent.chat_stream(messages, session_id)` that loops `chat_with_tools` → execute tool_calls → append tool_results → next iteration, until the LLM returns a final text response or the loop limit is hit.

#### Scenario: Single-tool task completes in 2 turns
- **WHEN** user says "create todo.txt with content '吃饭买菜'"
- **THEN** turn 1: LLM returns tool_call(desktop_create_file)
- **AND** turn 2: agent appends `{role: 'tool', content: '...success...'}` and LLM returns `"Created todo.txt on your desktop."`
- **AND** chat_stream yields the final text tokens

#### Scenario: Multi-tool task chains 3+ tools
- **WHEN** user says "list desktop files, then read todo.txt, then summarize it"
- **THEN** the agent executes list_directory → read_file → final text in 4 turns

#### Scenario: Loop limit prevents runaway
- **WHEN** the LLM keeps calling tools without converging
- **THEN** after 25 turns, the agent yields an error message "Maximum tool turns exceeded (25)" and stops

### Requirement: Tool descriptors auto-generated from registry

The system SHALL convert each `ToolSpec` in the registry to OpenAI tool schema format on demand. ToolSpec SHALL gain `description_for_llm: str` and `input_schema_json: dict` fields.

#### Scenario: ToolSpec to OpenAI schema conversion
- **WHEN** `tool_spec.to_openai_schema()` is called for `desktop_create_file`
- **THEN** the result equals `{"type": "function", "function": {"name": "desktop_create_file", "description": "Create a file on user's desktop. ...", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "content": {"type": "string"}}, "required": ["name", "content"]}}}`

#### Scenario: ToolSpec missing input_schema_json fails fast
- **WHEN** registering a tool without `input_schema_json`
- **THEN** `ToolRegistry.register()` raises `ValueError("tool 'X' missing input_schema_json")`

### Requirement: tool_call execution honors permission gate

The system SHALL call `permission_gate.check(category, params)` before executing each tool. If the gate returns deny, the agent SHALL inject `{role: 'tool', content: 'permission denied'}` into the conversation and continue.

#### Scenario: User denies write_file permission
- **WHEN** LLM calls `write_file(path='/etc/passwd', content='...')` and user clicks "No" in popup
- **THEN** the tool result is `{"error": "permission denied", "category": "write_file"}` and the loop continues so LLM can adapt
