# Permission system (P4-S20)

DeskPet's tool-use loop never silently runs sensitive operations on
your computer. Every call to a permission-categorized tool flows
through the **PermissionGate** before the handler executes:

```
agent loop  →  ToolRegistry.execute_tool(name, params, session)
            →  PermissionGate.check(category, params, session)
            →  (allow | deny)
            →  handler runs only on allow
```

## The 7 categories

| Category               | Default  | UI highlight     | Used by                         |
|------------------------|----------|------------------|---------------------------------|
| `read_file`            | allow    | none             | `read_file`, `list_directory`   |
| `read_file_sensitive`  | prompt   | red              | auto-upgraded from `read_file`  |
| `write_file`           | prompt   | yellow           | `write_file`, `edit_file`       |
| `desktop_write`        | prompt   | yellow + path    | `desktop_create_file`           |
| `shell`                | prompt   | red + cmd shown  | `run_shell`                     |
| `network`              | prompt   | yellow + URL     | `web_fetch`                     |
| `mcp_call`             | prompt   | yellow + tool    | MCP-sourced tools               |
| `skill_install`        | prompt   | red + manifest   | marketplace install flow        |

`read_file_sensitive` is auto-applied when the path matches one of:
`.ssh/id_rsa`, `.aws/credentials`, `.env`, `id_ed25519`,
`cookies.sqlite`, `login.keychain`, `shadow`, `password`. (Defined in
`backend/deskpet/permissions/gate.py` `_SENSITIVE_PATH_RE`.)

## Popup options

When the gate prompts you, three buttons appear:

- **允许一次** (Allow once) — runs the operation; no caching
- **本会话始终允许** (Allow for this session) — runs and caches the
  decision keyed by `(session_id, category, param-keyset hash)`. Other
  sessions still prompt.
- **拒绝** (Deny) — operation rejected; the LLM sees a structured
  `permission denied` envelope and typically apologizes / stops.

`ESC` is equivalent to **拒绝**. Popups time out to deny after 60s if
ignored.

## Deny patterns (config-side override)

Add hard rejections to `config.toml`:

```toml
[permissions]
[permissions.deny]
shell_patterns   = ["rm -rf /", "format c:", "del /f /s /q c:"]
write_patterns   = []
network_patterns = []
```

Deny patterns are checked **before** the popup and **before** the
session cache — even a "session-always-allow" decision can't override
them. This is by design: it's the only way to fail-closed on
catastrophic operations the user might mis-click through.

## Audit

Every gate decision is logged via structlog with fields:
`category`, `source` (default-allow / cache-hit / user-allowed /
config-deny / timeout), `request_id`, and `params_keyset_hash`. Tail
the structured log to see exactly which categories your skills are
exercising.
