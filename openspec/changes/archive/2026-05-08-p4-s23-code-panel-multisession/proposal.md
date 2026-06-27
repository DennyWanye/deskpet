# Proposal: P4-S23 — Code Panel + Multi-Session Dashboard

**Status**: in progress
**Sprint**: P4-S23
**Date**: 2026-05-08
**Owner**: claude (auto, all-night solo)

## Why

The P4-S22 Code mode crammed a Claude-Code–class workflow into a 280-px-wide
desktop pet bubble: every tool call fights the Live2D canvas for screen real
estate, no scrollback, no syntax-highlighted diffs, and you can only run one
project at a time per backend process. That's enough to prove the wiring
works, but it's not enough to actually use Code mode for real work.

The user's specific asks (with a screenshot of OctoAlly):

1. A Claude-Code–style chat panel that *expands* into a real workspace —
   message stream, sidebar, code rendering, room to read 50 lines of
   `read_file` output.
2. Multi-project dashboard: see N active code sessions in a grid, each
   ticking forward independently. Click a tile → that session's chat
   panel.
3. The companion pet stays. The panel is the *workbench*; the pet is the
   *companion*. Both visible simultaneously without cramping each other.
4. Anything the panel breaks should be diagnosed and fixed properly,
   not papered over.

This sprint also folds in three deferred fixes that require a backend
rebuild anyway, so we ship them together rather than baking another
PyInstaller round-trip:

- **chat_v2 ConnectError "unknown" toast** (P4-S22 leftover) — provider
  layer doesn't wrap httpx errors, so a the relay keep-alive RST surfaces
  as "chat_v2 错误: unknown" in the UI. Already coded; awaiting build.
- **todo_write live broadcast** (P4-S22 leftover) — broadcaster wired
  in main.py, but frozen exe ships the no-op version. Already coded.
- **MCP filesystem portable workspace path** (P4-S22 leftover) — manager
  rewrites %APPDATA%/deskpet → user_data_dir(). Already coded.

## What Changes

### Phase A — Code Chat Panel (independent Tauri window)

A second Tauri window (`code-panel`, 1024×720, resizable) opens
automatically when the user enters Code mode and closes when they
exit. Toolbar gets a 💬 toggle so users can hide/restore the panel
without losing their place.

The new window renders a Claude-Code–shaped React tree:

```
┌── CodeChatPanel ────────────────────────────────────────────────┐
│  Header: project path · 📋 N todos · status · close             │
├── SessionSidebar ──┬── MessageStream (right) ───────────────────┤
│ Active sessions    │ <user bubble>   make a hello.py            │
│  ▸ proj-a   📋 3   │ <assistant>     I'll create it now.         │
│  ▸ proj-b   📋 0   │ <ToolCallCard>  write_file(path,content)   │
│ [+ New project]    │   permission: ✓ allow                      │
│                    │   result: { ok: true, bytes: 38 }          │
│ Todos              │ <assistant>     Done. Want me to run it?   │
│  ⏳ Implement      │ ...                                         │
│  ○ Test            │                                             │
│  ✓ README          │                                             │
│                    ├─────────────────────────────────────────────┤
│ Token usage        │ Input: ___________________________ [Send]   │
│ the relay 12k/100k    │ ☑ tools  model: gpt-5.5  /code mode         │
└────────────────────┴─────────────────────────────────────────────┘
```

Components:
- `CodeChatPanel` — top-level layout
- `SessionSidebar` — project list, todo list, token panel
- `MessageStream` — virtualized via react-virtuoso; per-message height
  pre-computed with **chenglou/pretext** (canvas measurement, no DOM
  thrash) so 1000-message scrollback stays smooth
- `MessageBubble` — user / assistant distinction
- `ToolCallCard` — collapsible tool name / args / result; copy button
- `CodeBlock` — `react-markdown` + `react-syntax-highlighter` (Prism
  light, only python/typescript/rust/bash/json/yaml/sql preloaded)
- `InputBar` — textarea + send + chat_v2 toggle + model badge

Companion pet keeps running unchanged in the main window. While Code
mode is active, the pet's DialogBar shows a placeholder "代码工作中…"
so the user isn't confused by parallel rendering of the same chat.

### Phase B — Multi-session dashboard

Backend gets honest multi-session support:

- `chat handler` accepts `payload.session_id` (currently hard-coded to
  `default`). Each session_id gets its own AgentLoop coroutine.
- `CodeModeManager.enter` already supported multiple base sessions
  (P4-S22 left it that way) — we expose this through a new
  `code_sessions_list` IPC that returns all currently-enabled sessions
  with `{base_sid, project_root, project_name, todo_count, last_activity, status}`.
- All broadcast events (`code_todo_update`, `chat_response`,
  `tool_call`, `tool_result`) carry `payload.session_id` so the
  frontend can route each to the correct UI tile.
- Per-session control_ws routing: `_control_connections` is already
  per-sid; we just need to make sure broadcasts from inside an
  AgentLoop know which sid they belong to.

Frontend gets:

- `useSessionsStore` (zustand) — `Map<sid, SessionState>` where
  `SessionState = { project_root, messages, todos, status, last_at }`.
- WS dispatcher that routes incoming events by `payload.session_id` to
  the right slice of the store (default to `"default"` for legacy
  events that don't carry a sid).
- `SessionGridView` — responsive 4-column grid; each tile shows
  project name / path / todos snapshot / last assistant line / status
  pill (idle / thinking / running / waiting-for-permission).
  Click → switches the chat panel to that session.
- "+ New project" button on the dashboard → folder picker → creates
  a new code session via the existing `code_mode_enter` IPC with a
  freshly-derived base_sid.
- Concurrency limiter on outbound LLM calls (default `max_in_flight=2`)
  to keep the relay happy when 5 sessions all want a turn.

### Phase C — Polish

- `@filename` autocomplete in `InputBar`: scan project_root, fuzzy
  match, on selection insert path relative to root.
- ToolCallCard: collapse-by-default for results > 30 lines; "copy"
  buttons on path / args / result.
- Token-usage progress bar; show queue depth when concurrency limiter
  is throttling.
- Theme + font-size knobs in `SettingsPanel`.
- Session persistence: on backend startup, restore all CodeModeState
  rows from a small `code_sessions` table and rehydrate the in-memory
  manager. So a deskpet restart doesn't lose your active projects.
- Light visual polish: enter/exit transitions, subtle empty states.

### Folded-in fixes (no separate sprint needed)

- chat_v2 ConnectError → LLMProviderError wrap (provider layer)
- chat_v2_error frontend renders concrete reason, not `"unknown"`
- todo_write broadcaster wired to per-sid control_ws
- MCP filesystem `%APPDATA%/deskpet` → portable `user_data_dir()`

## Impact

### New / modified specs
- `code-mode` (extended — multi-session semantics, panel window)
- `frontend-ipc-surface` (new commands `open_code_panel` /
  `close_code_panel`; new IPC `code_sessions_list`)
- `agent-loop` (modified — concurrent runs, per-session token tracking)

### Compatibility
- Companion mode unchanged; Code mode is opt-in.
- The single-window experience still works if the user just doesn't
  open the panel — chat falls through to the existing DialogBar.
- Old `code_mode_enter` IPC (no `payload.session_id`) keeps working;
  backend defaults to a derived sid based on project_root hash.

### Risks
- Tauri second webview costs ~80 MB RAM. Mitigation: destroy on close
  (no warm hidden window).
- the relay concurrent calls may rate-limit. Mitigation: front-end
  concurrency limiter with backoff + status pill.
- Long scrollback + syntax highlighting can jank. Mitigation:
  react-virtuoso (only render visible window) + pretext (precise
  height before render so virtuoso doesn't reflow).

### Out of scope
- Inline file editor (monaco) — too heavy for chat panel scope.
- Terminal pane (xterm) — Bash tool already handles shell; no need
  for a free terminal.
- Diff viewer — first cut just renders edit_file before/after as
  two code blocks; proper diff UI is Phase D.
