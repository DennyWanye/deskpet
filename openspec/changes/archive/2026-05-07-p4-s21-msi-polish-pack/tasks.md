# Tasks: P4-S21 MSI Polish Pack

## Phase 1 — Backend code (no rebuild yet)

- [x] 1.1 `bundle.py`: add `history: list[dict]` field to `ContextBundle`
- [x] 1.2 `memory.py`: split rendering — `_render_l3_only` (memory_block) + populate `bundle.history` from raw L2 rows
- [x] 1.3 `main.py:1529`: `build_messages(history=_bundle.history, user_message=_text)`
- [x] 1.4 `voice_pipeline.py`: ctor accepts `tool_registry_v2`, `permission_gate`, `llm_provider`; `_handle_user_said` branches to AgentLoop when present, else legacy `chat_stream`
- [x] 1.5 `main.py:1757` VoicePipeline ctor: pass tool_registry_v2 + permission_gate + local_llm
- [x] 1.6 `permissions/gate.py`: add `auto_mode: bool` flag + short-circuit ALLOW when set
- [x] 1.7 `main.py` control WS: handle `permission_auto_mode_set` IPC msg → flip gate flag
- [x] 1.8 `permissions/gate.py`: voice-context TTS hint when popup is about to show
- [x] 1.9 `config.py:_bundle_default_config_path`: try `_MEIPASS/config.toml` first
- [x] 1.10 `config.py:seed_user_config_if_missing`: detect legacy schema, backup + replace
- [x] 1.11 `deskpet-backend.spec`: add `("../config.toml", ".")` to datas

## Phase 2 — Backend tests

- [x] 2.1 `tests/test_context_bundle_history.py` — bundle.history populated, build_messages emits history
- [x] 2.2 `tests/test_permission_gate_auto_mode.py` — auto_mode=true → ALLOW
- [x] 2.3 `tests/test_seed_user_config_legacy_migration.py` — legacy schema → backup + new schema written

## Phase 3 — Frontend code

- [x] 3.1 `bindings/config.ts`: replace fetch with `invoke("update_cloud_config")`
- [x] 3.2 `Toolbar.tsx`: add Quit button (calls `invoke("app_exit")`)
- [x] 3.3 `Toolbar.tsx`: remove `useToolUseLoop` toggle
- [x] 3.4 `App.tsx`: stop passing `useToolUseLoop` / `toggleToolUseLoop` props
- [x] 3.5 `SettingsPanel.tsx`: add 自动模式 checkbox + sends `permission_auto_mode_set`
- [x] 3.6 `index.css`: already has `user-select: none` / Selectable rule (#10) ✅
- [x] 3.7 `App.tsx`: already has `data-tauri-drag-region` (#11) ✅
- [x] 3.8 `SkillStorePanel.tsx`: already has WebkitLineClamp (#9) ✅

## Phase 4 — Rust code

- [x] 4.1 `src-tauri/src/commands.rs` (new): `update_cloud_config` + `app_exit`
- [x] 4.2 `src-tauri/src/lib.rs`: register both commands in `invoke_handler`
- [x] 4.3 `src-tauri/src/lib.rs`: `setup` adds system tray + Show/Hide/Quit menu
- [x] 4.4 `src-tauri/src/process_manager.rs`: `CREATE_NO_WINDOW` flag on backend spawn
- [x] 4.5 `src-tauri/Cargo.toml`: add `reqwest` dep if not already (for cmd HTTP)

## Phase 5 — Build packaging

- [x] 5.1 `scripts/build-msi.ps1`: patch `EmbedCab` to `"no"` (external cab) in addition to `<MediaTemplate>`
- [x] 5.2 README/doc: note that MSI ships as folder (msi + cab files), not single-file

## Phase 6 — Build + verify

- [x] 6.1 Run focused pytest (Phase 2 suites + paths/embedder regression)
- [x] 6.2 Run full backend pytest suite — must stay 786 passed (or higher)
- [x] 6.3 PyInstaller rebuild dist (`--distpath dist-msi`)
- [x] 6.4 Frozen smoke test — launch backend, /health 200, log shows no sealos
- [x] 6.5 `scripts/build-msi.ps1` — produces fresh MSI
- [x] 6.6 Manual install smoke (each on the user's box):
  - [x] no cmd window
  - [x] tray icon visible, Quit works
  - [x] Toolbar Quit works
  - [x] Settings save → no fetch error
  - [x] Chat with multi-turn context — pet remembers
  - [x] Voice "make a joke on desktop" → PermissionPopup → file created
  - [x] Auto mode toggle ON → next voice tool no popup

## Phase 7 — Archive

- [x] 7.1 Update `plans/2026-05-07-msi-known-issues.md` — mark fixed items closed
- [x] 7.2 git commit per logical chunk
- [x] 7.3 `openspec archive p4-s21-msi-polish-pack`
