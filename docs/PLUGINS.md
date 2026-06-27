# Plugins (P4-S20)

A DeskPet **plugin** is a packaged set of skills + optional MCP
servers + optional Python tools. Compared to a single SKILL.md skill,
a plugin lets you ship multiple skills that share state and bring
their own MCP backends.

## Layout

```
%APPDATA%/deskpet/plugins/<your-plugin>/
├── plugin.json         (required)
├── README.md           (optional)
├── skills/             (optional, default name; configurable)
│   ├── skill-a/SKILL.md
│   └── skill-b/SKILL.md
├── mcp.json            (optional, default name)
└── tools/              (optional, future use)
```

## plugin.json

```json
{
  "name": "notion",
  "version": "1.0.0",
  "description": "Notion integration",
  "skills_dir": "skills",
  "mcp_servers_file": "mcp.json",
  "tools_dir": "",
  "requires": ["other-plugin"]
}
```

Required fields: `name`, `version` (must be semver), `description`.
Everything else has a sensible default.

## mcp.json (optional)

```json
{
  "servers": [
    {
      "name": "slack-mcp",
      "command": "node",
      "args": ["./slack-server.js"]
    }
  ]
}
```

When the plugin is enabled, the MCPManager merges these servers with
the global `[mcp]` block from `config.toml`. Each server is annotated
with `source: "plugin:<name>"` so DeskPet can cleanly stop them when
the plugin is disabled.

If a plugin's server name collides with a global server, the
plugin version wins and a warning is logged.

## Scaffold

```bash
cd backend && python -m scripts.scaffold_plugin my-plugin --out .
```

This generates a starter layout with:
- valid `plugin.json` (semver `0.1.0`)
- README
- `skills/example/SKILL.md` (Claude Code v1 format with variable +
  shell-injection demo body)

The generated skill loads via the v1 parser and the plugin discovers
via `PluginManager.discover()` immediately — verified by
`backend/scripts/e2e_stage_d.py`.

## Install / enable / disable

**Manual install (v1 only — full marketplace deferred):**

```bash
git clone https://github.com/foo/notion-plugin %APPDATA%\deskpet\plugins\notion-plugin
```

Restart DeskPet (or trigger SkillLoader reload via IPC). The plugin
appears in the SkillStorePanel's "已安装" tab.

**Enable / disable** via control-WS IPC:

```jsonc
{ "type": "plugin_enable", "payload": { "name": "notion" } }
{ "type": "plugin_disable", "payload": { "name": "notion" } }
```

Or persist across restarts in `config.toml`:

```toml
[plugins]
enabled = ["notion", "slack"]
```

Disabling unloads the plugin's skills + stops its MCP servers cleanly.

## Skill namespacing

When two enabled plugins ship a skill with the same name (e.g. both
`notion` and `slack` ship `summarize`), the SkillLoader keeps both
visible and tags each with its `plugin:<name>` source. The UI badges
disambiguate them.

## Marketplace UI for plugins

Deferred to a future iteration. v1 is **install-by-URL only** for
plugins; the full SkillStorePanel handles community **skills** today.

## Security

- Plugin skill loads still go through the SKILL.md v1 safety path
  (allowed-tools allow-list + permission_categories enforced by
  PermissionGate).
- Plugin MCP servers run as subprocesses spawned by MCPManager — they
  are **not** sandboxed beyond what the user's OS provides. Only
  install plugins you trust.
- Plugin `mcp.json` server entries do **not** bypass deskpet's
  `[permissions.deny]` patterns at tool-call time.
