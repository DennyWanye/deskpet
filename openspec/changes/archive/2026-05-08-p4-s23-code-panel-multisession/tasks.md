# Tasks: P4-S23 Code Panel + Multi-session

## Phase 0 — Dev environment

- [x] 0.1 Rebuild backend .venv (was lost during C: cleanup)
- [x] 0.2 pip install -r requirements.txt + verify dev backend boots

## Phase A — Code Chat Panel (independent Tauri window)

- [x] A.1 tauri.conf.json: add 2nd window `code-panel` (1024×720, hidden initially)
- [x] A.2 capabilities/default.json: extend permissions to both windows + add show/hide
- [x] A.3 Rust commands: `open_code_panel`, `close_code_panel`
- [x] A.4 main.tsx: route by `window.location.hash` → `<App>` or `<CodePanelRoot>`
- [x] A.5 npm install: react-markdown, react-syntax-highlighter, @chenglou/pretext, react-virtuoso, zustand
- [x] A.6 stores/sessionsStore.ts (zustand)
- [x] A.7 components/CodePanelRoot.tsx (top-level; subscribes WS + store)
- [x] A.8 components/code-panel/SessionSidebar.tsx
- [x] A.9 components/code-panel/MessageStream.tsx (react-virtuoso + pretext)
- [x] A.10 components/code-panel/MessageBubble.tsx (user/assistant)
- [x] A.11 components/code-panel/ToolCallCard.tsx (collapsible)
- [x] A.12 components/code-panel/CodeBlock.tsx (RSH Prism light, ~6 langs)
- [x] A.13 components/code-panel/InputBar.tsx
- [x] A.14 App.tsx: 💬 toolbar button → invoke open/close_code_panel
- [x] A.15 App.tsx: on code_mode_enter auto-invoke open_code_panel
- [x] A.16 App.tsx: on code_mode_exit close panel + DialogBar shows "代码工作中…" placeholder when panel open

## Phase B — Multi-session

### Backend
- [x] B.1 main.py chat handler: accept `payload.session_id`, fallback to ws sid
- [x] B.2 main.py: stamp `payload.session_id` on every emitted event
- [x] B.3 main.py: new IPC handler `code_sessions_list` → `code_sessions_list_response`
- [x] B.4 main.py: maintain `_chat_inflight` map, cancel prior task on same-sid retry
- [x] B.5 broadcaster: route per-sid (already coded in P4-S22 wire-up; verify)

### Frontend
- [x] B.6 sessionsStore: keep all sessions, switch active_sid
- [x] B.7 WS dispatcher: route by payload.session_id to store slices
- [x] B.8 SessionGridView component (4-col responsive grid)
- [x] B.9 SessionTile component (project / todos / status pill / last AI line)
- [x] B.10 "+ New project" button → folder picker → code_mode_enter
- [x] B.11 ConcurrencyLimiter for outbound chat sends (max 2)
- [x] B.12 Status pill rendering (idle / thinking / running / permission / error)

## Phase C — Polish

- [x] C.1 @filename autocomplete in InputBar (project_root scan + fuzzy)
- [x] C.2 ToolCallCard collapse-by-default for >30 line results, copy buttons
- [x] C.3 Token usage progress bar
- [x] C.4 Settings panel: theme + font-size knobs
- [x] C.5 Session persistence (sqlite table `code_sessions`)

## Folded-in bug fixes (carryover)

- [x] X.1 providers/openai_compatible.py: wrap httpx errors → LLMProviderError (already in tree)
- [x] X.2 App.tsx chat_v2_error: show concrete reason not "unknown" (already in tree)
- [x] X.3 todo_write broadcaster wired to per-sid control_ws (already in tree)
- [x] X.4 deskpet/mcp/manager.py: portable userdata path (already in tree)

## Tests

- [x] T.1 test_p4s23_chat_handler_session_routing.py — payload.sid honored, isolation
- [x] T.2 test_p4s23_code_sessions_list.py — IPC returns enabled sessions
- [x] T.3 test_p4s23_inflight_cancellation.py — same-sid retry cancels prior
- [x] T.4 Existing 900+ pytest suite stays green
- [x] T.5 frontend tsc -b clean

## Visual end-to-end (computer-use)

- [x] V.1 launch deskpet, screenshot pet alone
- [x] V.2 click 🔧 → folder picker → select proj A → screenshot panel open
- [x] V.3 type "create hello.py" → permission popup → allow → screenshot file appears
- [x] V.4 click "+ New project" in panel → pick proj B → screenshot grid w/ 2 tiles
- [x] V.5 switch tile → screenshot session A history preserved
- [x] V.6 send msg in B → screenshot A unchanged (isolation)
- [x] V.7 close panel via 💬 → reopen → screenshot state restored
- [x] V.8 exit code mode → screenshot pet resumed, panel closed
- [x] V.9 verify AppData has zero deskpet folders (portable confirmed)

## Build + ship

- [x] S.1 backend pytest full pass
- [x] S.2 PyInstaller rebuild dist-portable + smoke
- [x] S.3 Tauri MSI rebuild via build-msi.ps1
- [x] S.4 Deploy backend to G:\tools\deskpet\backend
- [x] S.5 Final visual smoke

## Archive

- [x] Z.1 Update plans/2026-05-07-msi-known-issues.md (close all P4-S22 carryovers)
- [x] Z.2 openspec archive p4-s23-code-panel-multisession
