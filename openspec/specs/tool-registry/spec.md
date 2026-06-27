# tool-registry Specification

## Purpose
TBD - created by archiving change deskpet-skill-platform. Update Purpose after archive.
## Requirements
### Requirement: ToolSpec extended fields

The `ToolSpec` dataclass SHALL be extended with these required fields for tool_use protocol support:
- `description_for_llm: str` — concise one-line description shown to the LLM
- `input_schema_json: dict` — JSON Schema for parameters (OpenAPI 3.0 subset)
- `permission_category: str` — one of the 7 categories from permission-gate spec
- `source: str` — `"builtin"` | `"plugin:<name>"` | `"mcp:<server>"` (default `"builtin"`)
- `dangerous: bool` — flag for red-highlighted operations (default false)

Existing fields (`name`, `handler`, `category`) SHALL remain backward compatible.

#### Scenario: Existing tool registration unchanged
- **WHEN** legacy code calls `registry.register(name="foo", handler=fn, category="util")` without new fields
- **THEN** registration succeeds with defaults: `description_for_llm=""`, `input_schema_json={}`, `permission_category="read_file"`, `source="builtin"`, `dangerous=False`
- **AND** existing 17 hardcoded tools work unchanged

#### Scenario: New tool with full spec
- **WHEN** `desktop_create_file` is registered with all new fields
- **THEN** `registry.get("desktop_create_file").input_schema_json` returns the full JSON Schema

### Requirement: Schema generation for providers

The registry SHALL provide:
- `to_openai_schema(tool_names: list[str] | None) -> list[dict]` returning `[{"type":"function", "function":{"name","description","parameters"}}]` per OpenAI spec
- `to_anthropic_schema(tool_names: list[str] | None) -> list[dict]` returning `[{"name","description","input_schema"}]` per Anthropic spec
- `to_ollama_schema(...)` (alias for OpenAI format)

When `tool_names=None`, SHALL include all registered tools where `disable_model_invocation` is not set.

#### Scenario: OpenAI schema generated
- **WHEN** `registry.to_openai_schema(["read_file","write_file"])` is called
- **THEN** returns 2-element list, each shaped `{type: "function", function: {name, description, parameters: <schema>}}`

#### Scenario: Anthropic schema generated
- **WHEN** `registry.to_anthropic_schema(["read_file"])` is called
- **THEN** returns `[{name: "read_file", description: "...", input_schema: {type: "object", properties: {...}, required: [...]}}]`

#### Scenario: Filter by permission category for safe-mode
- **WHEN** `registry.to_openai_schema(filter_categories=["read_file","read_file_sensitive"])` is called in "safe mode"
- **THEN** only read-only tools are exposed to LLM

### Requirement: Registry surfaces source tier

The registry SHALL track tool source: builtin (compiled in), plugin (from a plugin), mcp (from an MCP server). When listing tools, source tier SHALL be visible to enable provenance UI and audit logs.

#### Scenario: List tools by source
- **WHEN** `registry.list_tools(source="plugin:notion")` is called
- **THEN** returns only tools registered by the notion plugin

#### Scenario: Audit log records source
- **WHEN** a tool execution is logged
- **THEN** log entry includes `source` field for traceability

### Requirement: MCP tool integration into registry

When MCPManager spawns an MCP server, its declared tools SHALL be registered into the same `ToolRegistry` with `source="mcp:<server_name>"` and `permission_category="mcp_call"`. This unifies the tool dispatch path.

#### Scenario: MCP tool invokable via registry
- **WHEN** `slack-mcp` server declares tool `send_message` and is spawned
- **THEN** `registry.get("slack:send_message")` returns ToolSpec; agent loop can invoke it through the same `execute_tool(name, params)` API as builtin tools

#### Scenario: MCP server stops removes tools
- **WHEN** an MCP server is stopped (e.g., plugin disabled)
- **THEN** all tools with `source="mcp:<server>"` are unregistered from the registry

### Requirement: Tool execution with permission gate

The `registry.execute_tool(name, params, session_id)` async API SHALL:
1. Look up ToolSpec
2. Await `PermissionGate.check(spec.permission_category, params, session_id)`
3. If allowed, invoke handler with timeout
4. Return `{ok, result | error}` envelope

This SHALL replace direct handler calls so permission gating cannot be bypassed.

#### Scenario: Permission allow path
- **WHEN** `registry.execute_tool("read_file", {"path":"foo.txt"}, sid)` and gate returns allow
- **THEN** handler runs and returns result

#### Scenario: Permission deny path
- **WHEN** gate returns deny
- **THEN** handler is NOT called and execute_tool returns `{ok: False, error: "permission denied"}`

#### Scenario: Handler exception caught
- **WHEN** handler raises `OSError("disk full")`
- **THEN** execute_tool returns `{ok: False, error: "OSError: disk full"}` and exception is logged but not propagated

### Requirement: Tool name namespacing

Tool names SHALL be globally unique within the registry. Plugin-shipped tools SHALL be auto-prefixed with `<plugin_name>:` to avoid collisions. Builtin tools SHALL NOT have prefix.

#### Scenario: Plugin tool prefixed
- **WHEN** plugin `notion` ships tool named `create_page`
- **THEN** registered as `notion:create_page`; LLM schema also uses prefixed name

#### Scenario: Builtin name conflict rejected
- **WHEN** code tries to register `read_file` again with a different handler
- **THEN** `registry.register` raises `ToolNameConflictError("read_file already registered (source=builtin)")`

