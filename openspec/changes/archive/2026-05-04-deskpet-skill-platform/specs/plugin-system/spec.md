## ADDED Requirements

### Requirement: Plugin manifest format

The system SHALL recognize plugin directories that contain `plugin.json` at the root. The manifest SHALL declare: `name`, `version`, `description`, `skills_dir` (relative path, default "skills/"), `mcp_servers_file` (default "mcp.json"), `tools_dir` (optional, for plugin-shipped Python tools), `requires` (optional list of other plugin names).

#### Scenario: Valid plugin loaded
- **WHEN** a directory `%APPDATA%/deskpet/plugins/notion-plugin/` exists with valid `plugin.json` declaring `skills_dir: "skills"` and `mcp_servers_file: "mcp.json"`
- **THEN** PluginManager registers the plugin in its in-memory list and loads its sub-resources

#### Scenario: Missing manifest skipped
- **WHEN** a directory has no `plugin.json`
- **THEN** PluginManager logs `info: <dir> not a plugin` and skips it

#### Scenario: Invalid version rejected
- **WHEN** plugin.json has `version: "not-semver"`
- **THEN** PluginManager logs warning and DOES NOT load the plugin

### Requirement: Plugin skill loading

When a plugin is enabled, all SKILL.md files under `<plugin>/skills_dir/` SHALL be loaded via the standard SKILL.md parser, with priority just above `<project>/.claude/skills/` (per spec skill-md-parser §location priority).

#### Scenario: Plugin skill discoverable
- **WHEN** plugin `notion` is enabled with `skills/create-page/SKILL.md`
- **THEN** `loader.list_skills()` includes that skill with `source="plugin:notion"`

#### Scenario: Plugin name conflicts namespaced
- **WHEN** two plugins both have a skill named `summarize`
- **THEN** they are both available, identified as `notion:summarize` and `slack:summarize` respectively

### Requirement: Plugin MCP server registration

When a plugin is enabled and provides `mcp.json`, MCPManager SHALL merge those server entries with the global config.toml `[mcp]` block at startup.

#### Scenario: Plugin adds MCP server
- **WHEN** plugin `slack` has `mcp.json: {"servers": [{"name": "slack-mcp", "command": "node", "args": ["./slack-server.js"]}]}` and config.toml has only filesystem
- **THEN** MCPManager spawns both `filesystem` and `slack-mcp` servers

#### Scenario: Same-name server: plugin overrides + warns
- **WHEN** plugin defines a server with same name as in config.toml
- **THEN** plugin version is used and a warning is logged

### Requirement: Plugin enable/disable

The system SHALL allow plugins to be enabled/disabled at runtime via `enabled_plugins` config + IPC handlers `plugin_enable` / `plugin_disable`. Disabling unloads skills + stops MCP servers cleanly.

#### Scenario: Disable a plugin
- **WHEN** user calls `plugin_disable(name="notion")`
- **THEN** all skills under that plugin are removed from `loader.list_skills()` AND all MCP servers from that plugin are stopped

#### Scenario: Enable persists to config
- **WHEN** user calls `plugin_enable(name="notion")`
- **THEN** `notion` is added to `config.toml::[plugins].enabled` and persists across restarts

### Requirement: Plugin scaffold generator

The system SHALL provide `scripts/scaffold_plugin.py <name>` that generates a starter plugin layout including `plugin.json`, `README.md`, `skills/` directory, and a sample skill.

#### Scenario: Scaffold creates valid plugin
- **WHEN** `python scripts/scaffold_plugin.py my-plugin` is run
- **THEN** a directory `./my-plugin/` is created with valid `plugin.json` and a `skills/example/SKILL.md` that PluginManager can load

### Requirement: Plugin marketplace (deferred)

The system SHALL NOT ship a full plugin marketplace UI in v1. v1 SHALL support only manual install (git clone) into `%APPDATA%/deskpet/plugins/` and IPC enable/disable. Marketplace UI MUST be deferred to a future iteration.

#### Scenario: Manual install workflow
- **WHEN** user clones `git clone https://github.com/foo/notion-plugin %APPDATA%/deskpet/plugins/notion-plugin` and enables it via IPC
- **THEN** PluginManager loads it on next backend start (or live reload if hot-reload enabled)
