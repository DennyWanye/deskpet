# P4-S21 MSI Polish Pack — Status & Handoff

**Date**: 2026-05-07
**Auto-mode session**: completed without interruption
**State**: backend complete + deployed; Tauri rebuild blocked on missing Windows SDK

## What's done

### Backend (Python) — fully complete + deployed ✅

- **#16** ContextBundle.history field; MemoryComponent populates it from
  raw L2 rows; main.py chat handler passes `history=bundle.history` to
  `build_messages`. LLM now sees real OpenAI message turns instead of
  a system-prompt text dump. Regression for the "VPN bug" — user says
  "let's build a VPN", 5 turns later pet asks "what do you want to do?"
  — was previously the smoking gun.
- **#13** `voice_pipeline.py` accepts `tool_registry_v2 +
  permission_gate + local_llm` constructor params; when present,
  `_handle_user_said` routes through AgentLoop instead of plain
  `chat_stream`. Voice "make me todo.txt on desktop" now actually
  invokes `desktop_create_file`. Falls back to legacy `chat_stream`
  when the v2 stack isn't wired (tests, dev configs).
- **#13** `PermissionGate.auto_mode` flag. When ON, all permission
  requests auto-allow with `source="auto-mode"`. Beats deny patterns
  intentionally — opt-in only. Toggleable via control WS message
  `permission_auto_mode_set`.
- **#13** Voice TTS prompt: when `gate.current_source == "voice"` and
  popup is about to show, gate fires a TTS line "我需要确认才能执行
  ..., 请点击允许按钮" so the user knows to look at the screen. Best-
  effort; TTS errors don't block popup.
- **#12** `seed_user_config_if_missing` detects legacy `[llm.local]` /
  `[llm.cloud]` schema, backs up as `.legacy-bak`, replaces with bundle
  default. Eliminates the sealos 401 probe permanently.
- **#12** `_bundle_default_config_path` now tries `_MEIPASS/config.toml`
  first; spec ships repo root `config.toml` to the bundle so the
  migration source is always available in frozen builds.

### Tests — 811 passed (+25 new) ✅

Three new suites under `backend/tests/`:

- `test_p4s21_context_bundle_history.py` (8 tests) — bundle.history
  default empty, build_messages threads history, MemoryComponent
  populates meta["l2_history"], L2 not double-charged in memory_block.
- `test_p4s21_permission_gate_auto_mode.py` (8 tests) — auto_mode
  short-circuits ALLOW, beats deny patterns, isolated per instance,
  voice TTS hint fires only for `current_source == "voice"`, TTS
  failure doesn't block popup.
- `test_p4s21_seed_user_config_legacy_migration.py` (9 tests) — legacy
  schema detector, seed-on-first-run, migrate-with-backup, no-bundle
  fallback, _MEIPASS preference.

### Frontend (TypeScript) — code complete ✅

TS typecheck passes cleanly. Ships only when Tauri rebuilds.

- `bindings/config.ts`: `fetch` → `invoke("update_cloud_config")` (#1)
- `Toolbar.tsx`: `⏻` Quit button (#7)
- `App.tsx`: `handleBootExit` invokes `app_exit` Rust command (#7);
  `useToolUseLoop` state removed (#14)
- `SettingsPanel.tsx`: new `AutoModeToggle` component; sends
  `permission_auto_mode_set` over control WS (#13)
- `index.css`: `user-select: none` + `re-allow` for inputs/code (#10)
- `App.tsx`: `data-tauri-drag-region` (#11)
- `SkillStorePanel.tsx`: WebkitLineClamp on marketplace card (#9)

### Rust (Tauri) — code complete, build BLOCKED ⚠️

- `commands.rs` (new): `update_cloud_config` (HTTP proxy via reqwest)
  + `app_exit` (graceful AppHandle exit) (#1 #7)
- `lib.rs`: register both commands; setup() adds system tray icon
  with Show/Hide/Quit menu (#7)
- `process_manager.rs`: `CREATE_NO_WINDOW` (0x08000000) flag on
  Windows backend spawn — eliminates the orphan cmd window (#8)
- `process_manager.rs`: `BackendProcess::shared_secret_clone()` +
  `port()` accessors so the IPC bridge can read state without
  re-parsing the SHARED_SECRET stdout (#1)
- `Cargo.toml`: added `reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }`

### Packaging — `scripts/build-msi.ps1` patched ✅

External-cab patch added (`EmbedCab="no"`). Once Tauri builds, the
output is `.msi` (~10 MB) + N `.cab` siblings totaling ~5.4 GB. The
.msi alone fits in Windows Installer's C: cache (no more "C: needs
7 GB free") (#2).

## What's blocked

### Tauri build needs `rc.exe` (Windows SDK) ⛔

User uninstalled Windows SDK 10.0.22621 + 10.0.26100 in the C-drive
cleanup step earlier in the session (~5 GB recovered). `tauri-winres`
panics on missing rc.exe; cargo build fails before even reaching the
WiX step.

**Resolution path** (user must run, requires admin):

```powershell
# Pick one of:
winget install --id Microsoft.WindowsSDK.10.0.22621 --silent --accept-source-agreements --accept-package-agreements
# or via Visual Studio Installer: add the "Desktop development with C++" workload's
# "Windows 10/11 SDK" component (smaller than the standalone SDK).
```

After SDK is back:

```powershell
# Build the MSI end-to-end:
powershell -ExecutionPolicy Bypass -File /path/to/deskpet\scripts\build-msi.ps1
```

Output: `tauri-app/src-tauri/target/release/bundle/msi/DeskPet_*.msi` plus sibling `*.cab`.

## What's deployed right now

`G:\tools\deskpet\backend\` was overwritten with the new 7.7 GB
PyInstaller dist (`backend/dist-msi/deskpet-backend/`). User can
launch the existing `G:\tools\deskpet\deskpet.exe` (unchanged Tauri
exe from previous build) and immediately see backend changes:

- Multi-turn chat with real history (#16 fix)
- Voice "make me X.txt on desktop" → tool invocation + permission popup (#13 fix)
- `[llm.cloud]` legacy config auto-migrated on next launch (#12 fix)
- New unified config.toml shipped at `_internal/config.toml`

Frontend / tray / Quit button / IPC fetch fix won't be visible until
Tauri rebuilds — those bits are still backed by the previous 0.6.0
deskpet.exe from `G:\tools\deskpet\deskpet.exe`.

## Files modified

```
backend/
  config.py
  deskpet-backend.spec
  deskpet/agent/assembler/assembler.py
  deskpet/agent/assembler/bundle.py
  deskpet/agent/assembler/components/memory.py
  deskpet/permissions/gate.py
  main.py
  pipeline/voice_pipeline.py
  tests/test_p4s21_context_bundle_history.py        (new)
  tests/test_p4s21_permission_gate_auto_mode.py     (new)
  tests/test_p4s21_seed_user_config_legacy_migration.py  (new)

tauri-app/
  src/App.tsx
  src/bindings/config.ts
  src/components/SettingsPanel.tsx
  src/components/Toolbar.tsx
  src-tauri/Cargo.toml
  src-tauri/src/commands.rs                         (new)
  src-tauri/src/lib.rs
  src-tauri/src/process_manager.rs

scripts/
  build-msi.ps1
```

## Verification done

- [x] backend pytest: 811 passed (was 786 baseline + 25 new)
- [x] frontend TypeScript: `tsc -b` clean
- [x] PyInstaller frozen smoke: `loaded engine='vad_engine|asr_engine|tts_engine'`,
      no sealos URL, `llm_unified_schema_loaded` log emitted
- [x] dist-msi/deskpet-backend/_internal/config.toml present (11.5 KB)
- [ ] **Tauri MSI build** (blocked — needs Windows SDK)
- [ ] Manual install + smoke (blocked — no fresh MSI)

## Next steps (for the user)

1. Reinstall Windows SDK (one of: winget command above, or via Visual Studio Installer).
2. Run `powershell -File scripts/build-msi.ps1` — produces the new .msi + cab siblings.
3. Uninstall current DeskPet (Settings → Apps → DeskPet → Uninstall).
4. Install the new .msi (must keep .msi + cabs in same folder).
5. Smoke test:
   - No black cmd window appears alongside the pet (#8)
   - Tray icon shows up; right-click → Quit closes the pet (#7)
   - Toolbar `⏻` Quit button works (#7)
   - Settings → 自动模式 toggle visible (#13)
   - Settings → save LLM config — no "Failed to fetch" (#1)
   - Multi-turn chat: "我喜欢喝可乐" then "我喜欢喝什么" — answers 可乐 (#16)
   - Voice: "帮我桌面生成todo.txt内容是吃饭买菜" → permission popup → 允许 → file appears (#13)
   - Auto mode ON → repeat → no popup, file appears immediately (#13)
   - C: drive doesn't need 7 GB free during install (#2)
