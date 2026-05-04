# skill-md-parser Specification

## Purpose
TBD - created by archiving change deskpet-skill-platform. Update Purpose after archive.
## Requirements
### Requirement: Parser recognizes Claude Code SKILL.md format

The system SHALL provide `parse_skill_md(path: Path) -> SkillMeta` that loads a `SKILL.md` file with YAML frontmatter and markdown body. The parser SHALL be the primary loader for files named `SKILL.md` (case-insensitive).

#### Scenario: Minimal SKILL.md
- **WHEN** parsing a file with frontmatter `---\ndescription: foo\n---\nbody` and filename `SKILL.md`
- **THEN** returns `SkillMeta(name="<dir>", description="foo", body_markdown="body", source="claude-code-v1")`

#### Scenario: Missing frontmatter rejected
- **WHEN** parsing a `SKILL.md` without `---` frontmatter
- **THEN** the parser raises `SkillParseError("missing YAML frontmatter")` and the loader skips the skill with a warning

#### Scenario: Invalid YAML rejected
- **WHEN** frontmatter contains invalid YAML
- **THEN** raises `SkillParseError("invalid YAML: ...")` and skill is skipped

### Requirement: Frontmatter recognized fields (v1)

The parser SHALL recognize these fields and populate `SkillMeta`:
- `name` (string, default: directory name)
- `description` (string, required for model invocation)
- `when_to_use` (string, optional, appended to description)
- `argument-hint` (string, optional)
- `disable-model-invocation` (bool, default: false)
- `user-invocable` (bool, default: true)
- `allowed-tools` (string or list, optional, parsed to list[str])
- `paths` (string or list of glob, optional, parsed to list[str])
- `context` (`inline` only in v1; `fork` parses but logs warning "fork not supported in v1")
- `hooks.PreToolUse` (list of {matcher, command}, optional)
- `version` (string, optional)

#### Scenario: All v1 fields parsed
- **WHEN** SKILL.md has full frontmatter `name: foo\ndescription: bar\nallowed-tools: [Read, Write]\npaths: ["**/*.py"]\ndisable-model-invocation: true`
- **THEN** SkillMeta has matching fields populated

#### Scenario: Unknown fields preserved in raw_frontmatter
- **WHEN** frontmatter has `name: foo\nfuture-field: xyz`
- **THEN** SkillMeta.raw_frontmatter contains `future-field: xyz` and parser logs `info` (not warning)

#### Scenario: allowed-tools accepts string format
- **WHEN** `allowed-tools: "Read Write Bash(git *)"`
- **THEN** parsed to `["Read", "Write", "Bash(git *)"]` (space-separated, paren-aware)

### Requirement: Variable substitution

The parser SHALL substitute these placeholders in body content at invocation time (NOT at parse time):
- `${CLAUDE_SKILL_DIR}` → absolute path to skill dir
- `${CLAUDE_SESSION_ID}` → current session id
- `$ARGUMENTS` → all arguments as one string
- `$ARGUMENTS[N]` and `$N` → 0-based positional arg

#### Scenario: Variable substitution applied
- **WHEN** body contains `Read ${CLAUDE_SKILL_DIR}/template.md and pass to $0`
- **AND** invocation context: skill_dir=`/skills/foo`, args=`["bar", "baz"]`
- **THEN** rendered body is `Read /skills/foo/template.md and pass to bar`

#### Scenario: Quoted argument preserved
- **WHEN** invocation `args=['hello world', 'second']` and body has `$0 then $1`
- **THEN** rendered = `hello world then second`

### Requirement: Inline shell injection

The parser SHALL execute `` !`cmd` `` syntax in body before sending to LLM. The output replaces the placeholder. Errors are inlined as `[command failed: <error>]`.

#### Scenario: Backtick command executed
- **WHEN** body contains `` Current branch: !`git rev-parse --abbrev-ref HEAD` ``
- **AND** the command outputs `master`
- **THEN** rendered body has `Current branch: master`

#### Scenario: Failed command surfaced
- **WHEN** command exits non-zero
- **THEN** placeholder is replaced with `[command failed: exit 1: <stderr>]`

#### Scenario: Multi-line fenced injection NOT supported in v1
- **WHEN** body has ` ```!\nls\n``` ` fenced injection
- **THEN** parser leaves it as-is and logs warning "fenced shell injection not supported in v1"

### Requirement: Skill location priority

The system SHALL search SKILL.md in this order, higher priority overrides lower on name conflict:
1. bundled (deskpet/skills/builtin/)
2. user (`%APPDATA%/deskpet/skills/`)
3. project (`<project>/.claude/skills/`)
4. plugin (`<plugin>/skills/`)

#### Scenario: User skill overrides bundled
- **WHEN** both `deskpet/skills/builtin/foo/SKILL.md` and `%APPDATA%/deskpet/skills/foo/SKILL.md` exist
- **THEN** SkillLoader uses the user version and logs `info: skill 'foo' overridden by user version`

### Requirement: Hot-reload watcher

The SkillLoader SHALL watch user / project skill directories with `watchdog` and reload on file changes within 1 second debounce. Hot-reload SHALL NOT crash in-flight chats using the old skill.

#### Scenario: New skill picked up without restart
- **WHEN** user adds `%APPDATA%/deskpet/skills/new-skill/SKILL.md` while backend is running
- **THEN** within 1.5s, `loader.list_skills()` includes "new-skill"

#### Scenario: Edit triggers reload
- **WHEN** existing skill's SKILL.md description is edited
- **THEN** the next `loader.list_skills()` reflects the new description

