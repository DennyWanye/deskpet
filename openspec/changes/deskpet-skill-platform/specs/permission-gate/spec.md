## ADDED Requirements

### Requirement: PermissionGate.check() async API

The system SHALL provide `PermissionGate.check(category: str, params: dict, session_id: str) -> PermissionDecision`. Every tool execution SHALL await this gate before performing the operation.

#### Scenario: Always-pass category (read_file default)
- **WHEN** `gate.check("read_file", {"path": "C:/tmp/note.txt"}, sid)` is called
- **THEN** returns `PermissionDecision(allow=True, source="default-allow")` without IPC popup

#### Scenario: Prompts user on first occurrence
- **WHEN** `gate.check("write_file", ...)` is called and no cached decision exists for this session
- **THEN** gate sends `{type: "permission_request", payload: {request_id, category, params, summary}}` IPC and awaits matching `permission_response`

#### Scenario: Returns deny on user "No"
- **WHEN** user clicks "No" in popup
- **THEN** gate returns `PermissionDecision(allow=False, source="user-denied")` and the tool execution is skipped

#### Scenario: Caches "Yes-always" per session
- **WHEN** user clicks "Always allow for this session" once for category=`shell` and command pattern `git *`
- **THEN** subsequent matching `shell` calls in the same session pass without prompting; new sessions still prompt

#### Scenario: Times out after 60 seconds
- **WHEN** the popup is shown and user does not respond within 60 seconds
- **THEN** gate auto-denies with `source="timeout"`

### Requirement: Permission categories table

The system SHALL define exactly these categories (v1):

| Category | Default | Highlight |
|---|---|---|
| `read_file` | allow | — |
| `read_file_sensitive` | prompt | red |
| `write_file` | prompt | yellow |
| `desktop_write` | prompt | yellow + path shown |
| `shell` | prompt | red + cmd shown |
| `network` | prompt | yellow + URL shown |
| `mcp_call` | prompt | yellow + server.tool shown |
| `skill_install` | prompt | red + manifest shown |

#### Scenario: Sensitive read upgraded
- **WHEN** `gate.check("read_file", {"path": "C:/Users/me/.ssh/id_rsa"}, sid)` is called
- **THEN** category is upgraded to `read_file_sensitive` and popup is shown

#### Scenario: Categories enum is exhaustive
- **WHEN** any tool calls gate with a category not in the table above
- **THEN** gate raises `ValueError("unknown permission category: ...")` (fail closed)

### Requirement: permission_request IPC contract

The `permission_request` IPC payload SHALL include: `request_id` (unique UUID), `category`, `summary` (one-line human-readable), `params` (full operation params for transparency), `default_action` (allow|prompt|deny).

#### Scenario: Frontend can render meaningful prompt
- **WHEN** backend sends permission_request with `summary="Write to C:\\Users\\me\\Desktop\\todo.txt (32 bytes)"` and `params={path, content_preview}`
- **THEN** the popup shows summary as title and params as collapsible details

### Requirement: Deny rules from config

The system SHALL load deny patterns from `config.toml::[permissions.deny]` and reject matching operations BEFORE any user prompt.

#### Scenario: Deny pattern matches
- **WHEN** config has `[permissions.deny] shell_patterns = ["rm -rf /", "format c:"]` and tool calls `shell` with `command="rm -rf /"`
- **THEN** gate returns `PermissionDecision(allow=False, source="config-deny", pattern="rm -rf /")` without prompting

#### Scenario: User cannot override deny
- **WHEN** even when "Yes-always" cached, a denied command is rejected
- **THEN** gate-deny takes precedence over cache

### Requirement: Frontend PermissionPopup component

The frontend SHALL provide a `PermissionPopup` modal with three buttons: **Yes (one time)**, **Yes, always for this session**, **No**. The popup SHALL show category-specific styling per the table.

#### Scenario: Modal blocks other UI
- **WHEN** a permission_request arrives
- **THEN** the popup overlays the window and prevents clicks on the chat input until decision is made

#### Scenario: ESC key denies
- **WHEN** user presses ESC
- **THEN** the popup sends `permission_response` with `decision="deny"` and closes
