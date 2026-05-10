# Tasks: P4-S22 Code Mode

## Phase 1 — Backend foundation

- [x] 1.1 `backend/deskpet/code_mode/` package skeleton (state.py, project_root.py, intent_detector.py, __init__.py)
- [x] 1.2 `CodeModeState` dataclass + `CodeModeManager` (per-session map, enter/exit/is_enabled)
- [x] 1.3 `resolve_project_root()` — user choice → AppData fallback → seed README
- [x] 1.4 `maybe_suggest_code_mode()` — keyword trigger detector
- [x] 1.5 SessionDB v11 migration: `code_todos` table + index
- [x] 1.6 `service_context.register("code_mode", CodeModeManager())` in main.py

## Phase 2 — New tools

- [x] 2.1 `tools/glob_tool.py` — pathlib.rglob with mtime sort + cap
- [x] 2.2 `tools/grep_tool.py` — re.compile + line walk, three output modes
- [x] 2.3 `tools/todo_write_tool.py` — SessionDB write + control WS broadcast
- [x] 2.4 `tools/web_search_tool.py` — DDG HTML scrape, defensive parse
- [x] 2.5 `tools/agent_tool.py` — nested AgentLoop, read-only subset by default
- [x] 2.6 Tool registration: only added to registry when `code_mode.is_enabled(sid)`
- [x] 2.7 Path safety helper: `assert_within_project_root(path, root)`

## Phase 3 — Wiring

- [x] 3.1 `main.py` chat handler: pass per-session `max_iterations` (50 if code, 8 else)
- [x] 3.2 PersonaComponent: branch on code_mode → emit `_CODE_MODE_PERSONA_TEMPLATE`
- [x] 3.3 Control WS handlers: `code_mode_enter` / `code_mode_exit` / `code_mode_suggest_dismiss`
- [x] 3.4 Daily budget temp override (100 CNY in code mode)
- [x] 3.5 PermissionGate: in code mode, write/shell first prompt offers
       "Code 模式期间始终允许" → cache key

## Phase 4 — Backend tests

- [x] 4.1 test_p4s22_code_mode_state.py — enter/exit/session_id derivation
- [x] 4.2 test_p4s22_project_root.py — sanitize, fallback, readme seed
- [x] 4.3 test_p4s22_intent_detector.py — triggers / non-triggers
- [x] 4.4 test_p4s22_glob.py
- [x] 4.5 test_p4s22_grep.py
- [x] 4.6 test_p4s22_todo_write.py — replace semantics, schema v11
- [x] 4.7 test_p4s22_web_search.py — mock httpx
- [x] 4.8 test_p4s22_agent_subagent.py — mocked sub LLM
- [x] 4.9 test_p4s22_path_safety.py — traversal escape rejected
- [x] 4.10 test_p4s22_code_mode_integration.py — mock LLM end-to-end

## Phase 5 — Frontend

- [x] 5.1 Toolbar: `🔧` Code mode toggle button + state indicator
- [x] 5.2 Project picker — invoke `open_directory_dialog` Rust cmd
- [x] 5.3 `CodeModeBanner` component (project path + exit button)
- [x] 5.4 `TodoListPanel` component (subscribes `code_todo_update`)
- [x] 5.5 Auto-suggest banner (responds to `code_mode_suggest` msg)
- [x] 5.6 IPC types in messages.ts: `CodeModeEnter`, `CodeModeState`,
        `CodeTodoUpdate`, `CodeModeSuggest`

## Phase 6 — Rust

- [x] 6.1 `commands.rs::open_directory_dialog` (uses tauri-plugin-dialog)
- [x] 6.2 register command in lib.rs invoke_handler

## Phase 7 — Manual smoke

- [x] 7.1 `scripts/e2e_code_mode_smoke.py` — automated WS smoke
- [x] 7.2 Manual: pick a sandbox dir, ask "build me a CLI todo app"
- [x] 7.3 Manual: cancel mid-loop, verify graceful stop
- [x] 7.4 Manual: enable auto_mode → ensure no permission popups in code mode
- [x] 7.5 Verify TodoListPanel renders + updates live

## Phase 8 — Archive

- [x] 8.1 Update HANDOFF if rebuild needed
- [x] 8.2 Update plans/2026-05-07-msi-known-issues.md (#15 → done)
- [x] 8.3 `openspec archive p4-s22-code-mode`
