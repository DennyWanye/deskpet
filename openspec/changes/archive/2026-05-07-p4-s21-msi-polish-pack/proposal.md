# Proposal: P4-S21 — MSI Polish Pack

**Status**: in progress
**Sprint**: P4-S21
**Date**: 2026-05-07
**Owner**: claude (auto mode)

## Why

The first installable MSI (P4-S20-LLM-Unified, shipped 2026-05-07) exposed a
short list of paper-cut bugs that make the install unusable end-to-end. They
were all logged in `plans/2026-05-07-msi-known-issues.md` (#1, #2, #7, #8,
#12, #13, #14, #16; plus #9/#10/#11 which already have code committed and
need a build to ship). Each individually is small, but together they prevent
a clean "double-click installer → use it like any desktop app" flow. This
sprint clears all of them in one rebuild.

Concretely, after install today the user hits, in order:

1. **MSI install bombs** if C: has < 7 GB free — Windows Installer caches an
   embedded cab on system drive whether or not you target the install dir
   to a different drive (#2, #12).
2. **A blank console window** opens alongside the pet — closing it kills
   the backend (#8).
3. **No way to quit** — the only "Exit" button is on the boot-failure
   overlay; once startup succeeds, the user must reach for Task Manager (#7).
4. **"Save" in Settings throws `TypeError: Failed to fetch`** — `https://
   tauri.localhost` webview can't fetch `http://127.0.0.1:8100`
   (mixed-content) (#1).
5. **A useless `chat_v2` toggle** on the toolbar — backend already routes
   both `chat` and `chat_v2` through the tool-use loop (#14).
6. **Voice input never triggers tools** — `voice_pipeline` calls
   `agent.chat_stream` directly, bypassing AgentLoop (#13). User said
   "make me a joke on desktop" via mic → got "I can't create files" instead
   of a `desktop_create_file` invocation.
7. **The pet forgets context every turn** — `ContextAssembler` packs L2
   history into a system-message text block instead of a real OpenAI
   `messages[]` history. LLM (gpt-5.5, qwen) treats it as instruction noise
   and answers as if no prior turn happened (#16). User: "let's build a
   VPN project together" … 5 turns later: "what do you want to do?"
8. **Old `[llm.cloud]` schema in user_data_dir/config.toml** keeps spawning
   sealos 401 probes from a URL the user never configured (#12).

These break the contract of "install and use", so they get fixed together
before anything else (Code mode #15, certificate signing #3, etc.).

## What Changes

This sprint touches three layers (frontend, Rust, Python backend) plus
packaging.

### Frontend (TypeScript / React)
- `SettingsPanel`: replace `fetch("/config/cloud")` with `invoke("update_cloud_config")` — Tauri IPC bypasses webview mixed-content
- `SettingsPanel`: add an "Auto mode" toggle (default OFF) — when ON, the backend's PermissionGate auto-allows every tool category for the session
- `Toolbar`: add a Quit button (cmd+invoke `app_exit` Rust command)
- `Toolbar`: remove the now-redundant `useToolUseLoop` toggle (backend has unified, both routes use AgentLoop already)
- `App.tsx`: `data-tauri-drag-region` (already done in #11)
- `index.css`: blanket `user-select: none` (already done in #10)
- `SkillStorePanel`: `WebkitLineClamp` on marketplace card description (already done in #9)

### Rust (Tauri)
- New command `update_cloud_config(payload)` — proxies to backend `POST /config/cloud` with the SHARED_SECRET attached server-side; frontend never touches HTTP
- New command `app_exit()` — graceful shutdown (closes WS, stops backend supervisor, exits)
- System tray icon with right-click menu: Show / Hide / Quit
- `process_manager.rs`: spawn backend with `CREATE_NO_WINDOW` (Windows-only `creation_flags(0x08000000)`)

### Backend (Python)
- `ContextBundle`: add `history: list[dict]` field; `MemoryComponent.gather()` populates it from raw L2 rows; `main.py` chat handler passes it to `bundle.build_messages(history=...)` — LLM sees real OpenAI message history instead of a textual "近期对话" summary
- `voice_pipeline.py`: accept optional `tool_registry_v2` + `permission_gate` constructor params; if provided, `_handle_user_said` routes through `AgentLoop.run()` instead of `agent.chat_stream`
- `main.py`: pass `deskpet_tool_registry_v2` + `permission_gate_v2` into `VoicePipeline` ctor
- `PermissionGate`: add `auto_mode` flag (settable via WS control message); when ON, `request_permission()` short-circuits to ALLOW for all categories
- `PermissionGate`: when not auto-mode and the request is from voice context, optionally TTS-prompt "I need permission to … please click Allow"
- `deskpet-backend.spec`: ship `config.toml` (the new unified-schema one from repo root) into `_internal/config.toml` so frozen `_bundle_default_config_path` finds it
- `config.py`: `seed_user_config_if_missing` detects legacy schema (`[llm.local]` or `[llm.cloud]` present) → backup as `.legacy-bak` → overwrite with bundle default

### Packaging
- `scripts/build-msi.ps1`: in addition to existing `<MediaTemplate>` patch, set `EmbedCab="no"`. Effect: cab data lives next to the .msi instead of inside, so Windows Installer caches only the small .msi (~10 MB) on C:, not the 5.4 GB payload. User can install without 7 GB free C: drive
- Distribute as a .zip (`.msi` + sibling `.cab` files together), or keep the .msi + cab files in one folder for direct install — this is documented in the readme

## Impact

### Affected Spec Capabilities
- `agent-context-assembly` — bundle gains a new field (history)
- `voice-pipeline` — gains tool-use capability
- `permissions` — auto_mode added to gate
- `bundle-packaging` — config.toml shipped, MSI uses external cab
- `frontend-ipc-surface` — new Rust commands

### Compatibility / Migration
- **Old user `config.toml` with [llm.local]/[llm.cloud]**: detected on next startup, auto-backed-up to `.legacy-bak`, replaced with new schema. User's existing `llm_runtime.json` (the actual setting values) preserved.
- **Old SessionDB**: untouched. `messages` table already has the data; we just start passing it through.
- **Frontend `chat_v2` msg-type**: still accepted (back-compat); `Toolbar.useToolUseLoop` removed but the WS still routes both correctly.
- **Existing PermissionPopup behavior**: unchanged when `auto_mode = false` (default). Opt-in.

### What Could Break
- If `voice_pipeline` AgentLoop change introduces a regression that breaks the chat_stream fallback, voice commands stop working. **Mitigation**: keep the `agent.chat_stream` branch as fallback when tool_registry/permission_gate aren't injected.
- If `ContextBundle.history` accidentally duplicates content with `memory_block`, prompt cost doubles. **Mitigation**: drop L2 content from `memory_block`, leave only L3 RRF recall there.
- MSI external cab means we ship a folder, not a single file. If user moves only the .msi, install fails. **Mitigation**: documented; recommend distributing as .zip or `.msi + .cab` zip.

### Out of Scope
- #3 SmartScreen code signing — needs a paid certificate
- #15 "Code mode" — sprint-level scope, separate proposal
- voice TTS prompt for permission requests is best-effort; if TTS fails it falls back silently to the popup
