## ADDED Requirements

### Requirement: Marketplace IPC handlers

The backend SHALL expose 4 control-WS message types for marketplace operations:
- `skill_marketplace_list` → returns the official registry contents
- `skill_list_installed` → returns currently installed user skills
- `skill_install_from_url` → clone a GitHub URL into user skill dir, then return manifest for confirmation
- `skill_uninstall` → delete a user skill

#### Scenario: List marketplace
- **WHEN** frontend sends `{type: "skill_marketplace_list"}`
- **THEN** backend fetches the official registry.json (cache TTL 1h) and replies `{type: "skill_marketplace_list_response", payload: {skills: [{name, description, source_url, manifest_url, ...}]}}`

#### Scenario: List installed
- **WHEN** frontend sends `{type: "skill_list_installed"}`
- **THEN** backend returns all skills present under `%APPDATA%/deskpet/skills/` with manifest summary

#### Scenario: Install requires confirmation
- **WHEN** frontend sends `{type: "skill_install_from_url", payload: {url: "github:user/repo"}}`
- **THEN** backend (a) clones to a staging dir, (b) parses skill manifest.json + SKILL.md, (c) returns `skill_install_pending` with declared `allowed-tools` + `permission_categories`
- **AND** the frontend MUST send `{type: "skill_install_confirm", payload: {staging_id: ..., approve: true|false}}` to finalize

#### Scenario: Install rejected on safety check
- **WHEN** the skill manifest contains a denylist tool (e.g. unrestricted `run_shell`) AND user denies
- **THEN** the staging dir is removed and a confirmation reply is `{ok: false, reason: "user denied"}`

### Requirement: skill_install_from_url uses git clone

The backend SHALL use `git clone --depth 1` to install skills. SHALL accept these URL forms:
- `github:owner/repo[/path]`
- `https://github.com/owner/repo[/tree/branch/path]`
- `git@github.com:owner/repo`

#### Scenario: GitHub shorthand
- **WHEN** url is `github:anthropics/skills`
- **THEN** backend runs `git clone --depth 1 https://github.com/anthropics/skills <staging>`

#### Scenario: Subpath in repo
- **WHEN** url is `github:anthropics/skills/tree/main/web-search`
- **THEN** backend clones repo + checks out the `web-search` subfolder only as the skill root

#### Scenario: Network failure clean
- **WHEN** clone fails (network / 404)
- **THEN** staging dir is removed and IPC reply is `{ok: false, error: "<git stderr>"}`

### Requirement: Manifest.json safety check

Each skill SHALL have a `manifest.json` (auto-generated from SKILL.md frontmatter if absent) declaring required permission categories and tools. Backend SHALL validate against an allow-list before staging.

#### Scenario: Disallowed tool blocked
- **WHEN** manifest declares `tools: ["read_file", "exec_arbitrary_pyc"]` and `exec_arbitrary_pyc` is not in deskpet's known tool list
- **THEN** install is blocked with `{ok: false, error: "unknown tool: exec_arbitrary_pyc"}`

#### Scenario: Sensitive permissions surfaced
- **WHEN** manifest declares `permission_categories: ["shell", "network"]`
- **THEN** the confirmation popup highlights these categories in red

### Requirement: Marketplace UI panel

The frontend SHALL provide a `SkillStorePanel` component reachable from `SettingsPanel`. The panel SHALL show three tabs: **Installed**, **Marketplace**, **Add by URL**.

#### Scenario: Marketplace tab loads on open
- **WHEN** user opens SkillStorePanel and switches to Marketplace tab
- **THEN** the panel sends `skill_marketplace_list` and renders the returned skills with name, description, install button

#### Scenario: One-click install
- **WHEN** user clicks "Install" on a marketplace skill
- **THEN** the panel sends `skill_install_from_url`, shows a confirmation modal listing requested permissions, and on approval sends `skill_install_confirm`
- **AND** on success the skill appears under "Installed" tab without page reload

#### Scenario: Uninstall
- **WHEN** user clicks "Uninstall" on an installed skill
- **THEN** confirmation popup → `skill_uninstall` IPC → skill dir removed → SkillLoader hot-reload

### Requirement: Skill registry source

The official registry SHALL live at a public GitHub raw URL configured in `backend/config.py` (default: `https://raw.githubusercontent.com/<deskpet-org>/skills-registry/main/registry.json`). User MAY override the URL in config.toml.

#### Scenario: Custom registry URL respected
- **WHEN** user sets `[marketplace]\nregistry_url = "https://example.com/registry.json"`
- **THEN** backend fetches that URL instead of the default

#### Scenario: Registry fetch failure shows graceful empty
- **WHEN** registry URL is unreachable
- **THEN** marketplace_list_response returns `{skills: [], error: "registry unreachable"}` (UI shows error message but does not crash)
