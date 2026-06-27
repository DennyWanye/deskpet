# skill-loader Specification

## Purpose
TBD - created by archiving change deskpet-skill-platform. Update Purpose after archive.
## Requirements
### Requirement: Dual-format SkillLoader

The SkillLoader SHALL detect SKILL.md format at parse time and dispatch to the correct parser:
- Files with YAML frontmatter (starts with `---`) → `parse_skill_md` (Claude Code v1)
- Files without frontmatter (legacy deskpet builtin) → existing `parse_legacy_skill_md`

Both formats SHALL coexist; legacy skills under `deskpet/skills/builtin/` MUST keep working unchanged.

#### Scenario: New format detected
- **WHEN** loader scans `%APPDATA%/deskpet/skills/foo/SKILL.md` whose first line is `---`
- **THEN** parse_skill_md is invoked and SkillMeta has `source="claude-code-v1"`

#### Scenario: Legacy format still works
- **WHEN** loader scans `deskpet/skills/builtin/voice-recorder/SKILL.md` (existing P3 skill, no frontmatter)
- **THEN** parse_legacy_skill_md is invoked, behavior is identical to pre-upgrade, all existing tests pass

#### Scenario: Mixed dir
- **WHEN** a single search root contains both legacy and v1 skills
- **THEN** loader loads each with appropriate parser; both appear in `list_skills()` results

### Requirement: Skill location resolution + override

The SkillLoader SHALL search 4 location tiers in priority order: bundled (lowest) < user < project < plugin (highest). Same-name conflicts SHALL be resolved by higher tier overriding, with an info-level log entry.

#### Scenario: User overrides bundled
- **WHEN** `deskpet/skills/builtin/summarize/SKILL.md` and `%APPDATA%/deskpet/skills/summarize/SKILL.md` both exist
- **THEN** `loader.list_skills()` returns the user version once, with `source="user"` and metadata `overrides=["bundled"]`

#### Scenario: Plugin highest priority
- **WHEN** all 4 tiers have a skill named `git-commit`
- **THEN** the plugin version wins; `metadata.overrides = ["bundled","user","project"]`

### Requirement: Hot-reload via watchdog

SkillLoader SHALL watch user, project, and plugin skill directories using `watchdog` Observer with PollingObserver fallback (Windows network drives). Changes SHALL trigger reload within 1.5s (1s debounce + 0.5s parse). Hot-reload SHALL NOT crash in-flight chats using the old skill instance.

#### Scenario: Add new skill without restart
- **WHEN** user creates `%APPDATA%/deskpet/skills/new/SKILL.md` while backend is running
- **THEN** within 1.5s, `list_skills()` includes `new` and IPC `skill_list_changed` event is broadcast

#### Scenario: Edit propagates
- **WHEN** existing skill SKILL.md description is edited and file saved
- **THEN** next `get_skill("name")` returns updated description; in-flight chat using old description completes unaffected

#### Scenario: Delete handled
- **WHEN** user deletes a skill directory
- **THEN** loader removes the skill from registry; subsequent invocation of that skill returns `{error: "skill not found"}` without crashing

### Requirement: list_skills metadata expansion

The `loader.list_skills()` return SHALL include each skill's full metadata: `name`, `description`, `when_to_use`, `source` (bundled|user|project|plugin), `disable_model_invocation`, `user_invocable`, `allowed_tools`, `paths`, `version`, `path` (absolute), `overrides` (list of overridden tiers).

#### Scenario: Frontend renders skill list
- **WHEN** frontend sends IPC `skill_list_request`
- **THEN** backend returns array of full metadata objects, frontend can render `SkillsPanel` with source-tier badges

#### Scenario: Filter for model invocation
- **WHEN** agent_loop calls `loader.list_skills(for_model=True)`
- **THEN** returns only skills where `disable_model_invocation=False`, sorted by source tier (plugin first)

### Requirement: Skill execution context

When a skill is invoked, SkillLoader SHALL build an execution context containing: `skill_dir` (absolute path), `session_id`, `args` (parsed from invocation), `env` (filtered subset), and pass it to the parser's variable substitution + inline shell injection.

#### Scenario: Substitution at invocation
- **WHEN** skill body contains `${CLAUDE_SKILL_DIR}/template.md` and `$ARGUMENTS`
- **AND** invocation provides `args=["analyze foo.py"]`
- **THEN** rendered body has actual paths and arguments substituted; LLM receives final text

#### Scenario: Shell injection runs in skill_dir
- **WHEN** body has `` !`git status` `` and skill_dir is `/skills/git-helper`
- **THEN** shell command runs with cwd=skill_dir and stdout is inlined

