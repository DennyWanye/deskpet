# code-mode — P4-S22

## ADDED Requirements

### Requirement: User MUST be able to enter Code mode via Toolbar or auto-suggest
The system SHALL expose a toolbar button (`🔧`) that triggers a native
folder picker; on folder selection (or cancel-→-auto-create) the
backend SHALL transition the user's chat session into Code mode and
report the active state via the `code_mode_state` control message.
When a chat turn matches "wants to start a project" patterns, the
backend SHALL also send a one-shot `code_mode_suggest` message so the
frontend can render an opt-in banner.

#### Scenario: Toolbar button + folder picker
- **WHEN** the user clicks `🔧` in the toolbar
- **THEN** a native OS folder picker opens
- **AND** on folder selection the backend stores the path as the project root
- **AND** the toolbar button shows a green "active" state

#### Scenario: Auto-suggest fires on project-intent message
- **GIVEN** Code mode is OFF and the user types "Scaffold a Rust project"
- **WHEN** the chat handler processes the message
- **THEN** a `code_mode_suggest` IPC msg is sent to the frontend
- **AND** the user can accept (auto-enter Code mode) or dismiss

### Requirement: Project root MUST be resolved with safe fallback
When the user enters Code mode, the system SHALL resolve a usable
project root path via this priority chain:
  1. User-supplied `project_path` (`mkdir -p` if missing)
  2. `%AppData%/deskpet/projects/<sanitized-name>/` with seeded README

Path traversal escape (e.g. `path="../../etc/passwd"`) SHALL be
rejected at tool-invocation time via `assert_within_project_root`.

#### Scenario: User-picked path is honoured
- **WHEN** user picks `D:/code/myproj`
- **THEN** backend resolves project_root to that path, creating it if absent

#### Scenario: Auto-fallback seeds README
- **WHEN** user enters Code mode with no path
- **THEN** backend creates `<user_data>/projects/untitled/`
- **AND** seeds a README.md describing the directory's purpose

### Requirement: Code mode MUST register five new tools
When Code mode is active for a chat turn, the AgentLoop SHALL have
access to these tools in addition to the existing OS toolset:
  * `glob` — find files by glob pattern under project root
  * `grep` — content search via Python regex (3 output modes)
  * `todo_write` — replace task list (idempotent), persists to SessionDB
  * `web_search` — DuckDuckGo HTML scrape (no API key)
  * `agent` — spawn a nested AgentLoop with read-only tool subset

#### Scenario: glob finds files relative to project root
- **GIVEN** project_root contains `src/main.py` and `tests/test_x.py`
- **WHEN** LLM invokes `glob` with pattern `**/*.py`
- **THEN** the result includes both files

#### Scenario: agent subagent cannot recursively spawn another agent
- **WHEN** agent tool is invoked with `tools=["agent", "read_file"]`
- **THEN** the subagent's tool subset contains read_file ONLY
- **AND** does not contain agent (recursion guard)

### Requirement: AgentLoop MUST allow 50 iterations in Code mode
The chat handler SHALL set `max_iterations=50` when the active session
is in Code mode (vs `max_iterations=8` for companion mode). This lets
long tool-use chains (read → grep → edit → bash → repeat) finish
without hitting the cap.

#### Scenario: Companion turn caps at 8 iterations
- **GIVEN** Code mode is OFF
- **THEN** AgentLoop is constructed with `max_iterations=8`

#### Scenario: Code mode turn allows 50 iterations
- **GIVEN** Code mode is ON
- **THEN** AgentLoop is constructed with `max_iterations=50`

### Requirement: Code mode session id MUST be stable per project
The system SHALL derive `code_session_id = "code-" + sha1(project_root)[:8]`
so re-opening the same project recovers the same SessionDB rows
(L1+L2+L3 memory persists across sessions).

#### Scenario: Same project → same code_session_id
- **GIVEN** project_root = `D:/proj/foo`
- **WHEN** user enters Code mode twice across separate backend boots
- **THEN** both invocations get the same `code_session_id`

### Requirement: Code-mode persona prompt MUST emphasise tool use
PersonaComponent SHALL emit a Code-mode-specific system prompt when
`config.code_mode.enabled=True`. The prompt names the project root,
lists available tools, and instructs the LLM to use `todo_write` to
plan complex tasks before executing.

#### Scenario: Code-mode prompt mentions project_root + todo_write
- **GIVEN** session is in Code mode at /tmp/proj
- **WHEN** PersonaComponent.provide() runs
- **THEN** the emitted text contains "/tmp/proj"
- **AND** mentions todo_write as a planning tool
