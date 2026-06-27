# Proposal: P4-S22 — Code Mode

**Status**: in progress
**Sprint**: P4-S22
**Date**: 2026-05-08
**Owner**: claude (auto mode, all-night solo run)

## Why

The pet currently does single-shot tool calls — fine for "create todo.txt
on desktop" but completely insufficient for real coding tasks ("scaffold
this Python project", "run tests + fix the failures", "search the codebase
for X and refactor"). User explicitly asked for a Code mode comparable to
Claude Code's capability — the natural use case for someone whose desktop
already has the LLM, voice channel, and long-term memory wired up.

After this sprint a user can:

1. Click a `🔧` button (or have the pet auto-suggest after detecting "I
   want to build a project" intent) → enter Code mode
2. Pick or auto-create a project root directory
3. Say or type "build me a CLI todo app" → LLM runs a 30-iteration tool
   loop using Read / Write / Edit / Bash / Glob / Grep / TodoWrite /
   WebSearch / Agent (subagent) — same shape as Claude Code's harness
4. Watch the TodoWrite progress panel show what the model is doing
5. Exit back to companion mode without polluting chat history

Long-term memory (BGE-M3 + sqlite-vec) is the killer feature here — it
lets the pet remember a multi-day project across sessions in a way
Claude Code can't.

## What Changes

### Backend (Python) — biggest layer
- New `code_mode` per-session state on `service_context` + IPC to flip it
- New `ProjectRootResolver` — picks user-chosen path, falls back to
  `%LocalAppData%/deskpet/projects/<llm-suggested-name>/` and seeds an
  empty README.md so cwd is always valid
- AgentLoop's `max_iterations` driven by mode: `8` for chat (current),
  `50` for code
- New system prompt template `code_mode_persona.md` — engineering
  assistant tone, tool catalog summary, auto-TodoWrite hint
- Five new tools registered only in Code mode:
  - **Glob** — `pathlib.rglob` over project root, returns paths sorted
    by mtime (matches Claude Code semantics)
  - **Grep** — ripgrep-equivalent via Python `re` (no external binary
    needed). Supports `pattern`, `glob` filter, output modes
    `content`/`files_with_matches`/`count`, context lines, multiline
  - **TodoWrite** — persists task list to SessionDB (schema v11), pushes
    state to control WS so the frontend panel can render
  - **WebSearch** — DuckDuckGo HTML scraping (no API key required)
  - **Agent (subagent)** — spawns a nested AgentLoop with its own LLM +
    tool subset; result string fed back to parent loop
- Auto-detect: a lightweight classifier on every chat turn flags "wants
  to start a project" patterns ("帮我做一个", "build me", "scaffold")
  and fires a one-shot suggestion message via control WS
- Permission strategy: in Code mode, read-class tools default-allow at
  the gate; write/shell add a `code_session_always_allow` flag to the
  cache so a single approve covers the whole session
- `auto_mode = True` (P4-S21) still bypasses everything — that's how
  the user gets the unattended dev experience

### Frontend (TypeScript / React)
- New `CodeModeBanner` component on top of DialogBar showing project
  path, mode badge, exit button
- Toolbar: `🔧` button toggles Code mode; sends `code_mode_enter`
  with optional `project_path` payload
- New `ProjectPicker` dialog — opens Tauri `dialog.open(directory: true)`
  via Rust command, falls through to backend's auto-name logic
- New `TodoListPanel` floating side panel — subscribes to
  `code_todo_update` control msg, renders tasks with status pills
- Auto-suggestion banner: when backend sends `code_mode_suggest`, show
  yellow banner "Open Code mode for this?" with Yes / Dismiss

### Rust (Tauri)
- New `open_directory_dialog` command that wraps `tauri-plugin-dialog`'s
  pick-folder API — returns absolute path string

### Tests
- Unit tests for each of the five new tools (~30 tests)
- One integration test: mock LLM scripted to emit a deterministic tool
  call sequence (Glob → Read → Edit → Bash) and verify the loop runs
  end-to-end
- Manual smoke: open an empty project dir, ask LLM to "make a todo CLI"
  end-to-end

## Impact

### New / modified specs
- `code-mode` (new top-level capability)
- `agent-loop` (modified — pluggable max_iterations)
- `permissions` (modified — code_mode_always_allow flag)
- `tool-registry` (modified — five new tools)

### Compatibility
- Default mode unchanged; Code mode is opt-in. Existing users see
  zero behavior change until they click `🔧`.
- Old agent loop callers (chat handler, voice pipeline) keep their
  current `max_iterations=8`.
- No SessionDB migration risks beyond the v11 todos table (additive
  only, idempotent).

### Out of scope (deferred)
- Multi-project workspace switcher in one session
- Code review mode (review existing files for issues)
- Git integration tools (commit/branch/diff) — Bash already covers this
  via shelling to `git`, which is good enough for v1
