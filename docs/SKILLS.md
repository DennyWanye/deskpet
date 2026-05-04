# Skills (P4-S20)

DeskPet supports the **Claude Code SKILL.md** standard. Any community
skill from <https://code.claude.com/docs/en/skills> or
<https://github.com/anthropics/skills> works in DeskPet too.

## Format

```markdown
---
name: my-skill                       # optional, defaults to dir name
description: One-line skill purpose   # required
when_to_use: When user asks ...       # optional, helps the LLM pick
argument-hint: <a path> [--flag]      # optional, shown to model
disable-model-invocation: false       # optional, default false
user-invocable: true                  # optional, default true
allowed-tools: [read_file, Bash]      # optional list or "Read Bash"
paths: ["**/*.py"]                    # optional glob list
hooks:                                # optional
  PreToolUse:
    - matcher: "Bash"
      command: "echo running shell"
version: 0.1.0                        # optional
---
# Body — the LLM receives this verbatim (after substitution)

Read ${CLAUDE_SKILL_DIR}/template.md and process $ARGUMENTS.

Today is !`date +%Y-%m-%d`.
```

### Variable substitution (rendered at invocation, not parse)

| Token                  | Replaced with                             |
|------------------------|-------------------------------------------|
| `${CLAUDE_SKILL_DIR}`  | absolute path to the skill's directory    |
| `${CLAUDE_SESSION_ID}` | current chat session id                   |
| `$ARGUMENTS`           | all positional args joined by space       |
| `$ARGUMENTS[N]`, `$N`  | 0-based positional argument               |

### Inline shell injection

`` !`<command>` `` is replaced with the command's stdout. The command
runs with `cwd=skill_dir` and a 10s timeout. Failures inline as
`[command failed: exit N: stderr]` so the LLM still gets coherent
text.

## Where skills live

DeskPet searches four locations in **priority order** (later overrides
earlier on name conflicts):

1. **bundled** — `deskpet/skills/builtin/` (ships with the app)
2. **user** — `%APPDATA%/deskpet/skills/`
3. **project** — `<project>/.claude/skills/` (when running in a project)
4. **plugin** — `<plugin>/skills/` (from enabled plugins)

When a name conflicts, the higher-tier wins and the loader logs
`info: skill <name> overridden by <tier> version`. The
`SkillLoader.list_metas()` records all overridden tiers so the UI can
show a badge.

## Hot-reload

The user/project skill dirs are watched with `watchdog`. Add or edit a
`SKILL.md` and DeskPet picks it up within ~1.5s without restart.

## Two formats coexist

DeskPet also supports the **legacy** built-in skill format (with
required `name`/`description`/`version`/`author` frontmatter, used by
the bundled built-in skills shipped before P4-S20). The loader
auto-dispatches by frontmatter shape:

- has both `version` AND `author` → legacy parser (strict)
- else → Claude Code v1 parser

## Installing community skills

Open the **🏪 SkillStore** panel from the dialog bar:

- **市场** (Marketplace) — official `registry.json` listing. One-click
  install via `git clone --depth 1` to a staging dir; you confirm the
  manifest before it's moved into your skills dir.
- **通过 URL 安装** (Install by URL) — paste a GitHub URL in any of
  these forms:
  - `github:owner/repo`
  - `github:owner/repo/tree/branch/subpath`
  - `https://github.com/owner/repo`
  - `git@github.com:owner/repo`
- **已安装** (Installed) — list your installed skills with one-click
  uninstall.

Sensitive permission categories (`shell`, `skill_install`,
`read_file_sensitive`) get red badges in the install confirm modal so
you see what the skill is asking for before approving.

## Writing a quick skill

```bash
mkdir -p %APPDATA%\deskpet\skills\quote-of-day
```

```markdown
---
description: Print today's motivational quote
---
The user said: $ARGUMENTS

Today is !`date +%Y-%m-%d`. Quote of the day:
```

DeskPet's hot-reloader picks this up immediately. Try saying "quote of
the day" in chat.
