# W34 — Data Dir Relocate + Backup Retention Fix

> Date: 2026-05-21
> Branch: `feat/2026-05-18-session-and-e2e`
> Trigger: User reported 20 GB occupied by `%AppData%\deskpet`
> on C: drive; wanted to move to F: and configure path via Settings UI

## TL;DR

| Before | After |
|---|---|
| `%AppData%\deskpet`: **20.96 GB** (428 state.db.bak files) | `F:\deskpet\data`: **70.55 MB** (3 .bak files, retention enforced) |
| C: drive consumed | C: drive freed; data on F: |
| Backup bug: new .bak every restart (no migration) | Skip backup when already at target schema version |
| No retention | Max 3 backups; oldest pruned automatically |
| Path hard-coded to `%AppData%` | Path configurable via Settings → 数据目录 |

## Two-Step Migration (one-time, this session)

### Step 1 — Cleanup
- 428 `state.db.bak.*` files at C:\Users\24378\AppData\Roaming\deskpet\data
- Kept newest 3 (timestamps 23:10:44 / :48 / :52)
- Deleted 425, freed **20.89 GB**
- Confirmed via `Get-ChildItem | Measure-Object Length -Sum`

### Step 2 — Relocate to F:
- `robocopy /E /MOVE /MT:8` of `C:\Users\24378\AppData\Roaming\deskpet` → `F:\deskpet\data`
- Source cleared (verified `Test-Path` = False)
- Destination: 70.55 MB, all critical files present (state.db, device_id, config.toml, billing.db)
- `[Environment]::SetEnvironmentVariable('DESKPET_USER_DATA', 'F:\deskpet\data', 'User')`
- New process picks up env var on next launch via `paths::user_data_dir()` env check (paths.rs §57)

## Code Changes

### Backend (Python) — backup bug fix

`backend/deskpet/memory/migrator.py`:
- Added `MAX_BACKUPS = 3` constant
- Added `_prune_old_backups(db_path, keep)` — sorts by **filename** (timestamp embedded, lexicographically sortable), deletes past index `keep`
- Added `read_user_version(db_path)` helper for cheap version probing
- `backup_db()` now calls `_prune_old_backups()` after each new backup

`backend/deskpet/memory/schema.py`:
- `initialize_state_db()` peeks at `PRAGMA user_version` first
- If `current_version >= TARGET_SCHEMA_VERSION` → skips backup entirely
- Worst case (corrupt DB, can't read version) → falls back to defensive backup as before

**Smoke test confirms behaviour**:
```python
# Pre-seed 10 fake backups + create real db → backup_db() prunes
BEFORE: 10 backups
After backup_db: 3 backups (newest survives by filename ordering)
```

### Rust — IPC commands

`tauri-app/src-tauri/src/user_data.rs`:
- `get_data_dir_setting()` → returns `{effective, default, env_override, effective_exists, effective_size_bytes}`
- `set_data_dir_preference(new_path)` → validates + creates dir + sets user env var via PowerShell
- `move_data_dir_contents(src, dst)` → recursive copy + remove source (portable, not robocopy-dependent)
- `validate_target_path(p)` → rejects empty / relative / drive-root / shell-metachar inputs (defence against arg injection in PowerShell command)

`tauri-app/src-tauri/src/lib.rs`:
- Registered 3 new commands in `invoke_handler!`

**7 new cargo tests** all passing:
- `validate_target_path_rejects_empty`
- `validate_target_path_rejects_relative`
- `validate_target_path_rejects_shell_metachars`
- `validate_target_path_rejects_drive_root`
- `validate_target_path_accepts_normal_path`
- `copy_dir_recursive_handles_nested_tree`
- `dir_size_bytes_sums_all_files`

Total cargo tests: **54 passed** (was 47).

### Frontend — Settings UI

`tauri-app/src/components/SettingsPanel.tsx`:
- New `DataDirSection` component, inserted between `<SupervisorToggleSection>` and `<DangerZoneSection>`
- Shows: 当前生效 path + size, 环境变量 raw value, 默认路径
- Input field + 浏览… button (uses existing `open_directory_dialog` IPC)
- ✅ 同时移动现有数据 checkbox (default on)
- 应用 / 恢复默认 / 刷新 buttons
- Two-step confirm dialog before applying (shows projected size, target path, what move means)
- "已保存，请重启 DeskPet" success state

## Verification

| Gate | Result |
|---|---|
| `npx tsc -b` | ✅ 0 errors |
| `npx vitest run` | ✅ 242 passed (unchanged) |
| `cargo test --lib` | ✅ **54 passed** (was 47 → +7 data-dir tests) |
| Backend smoke (retention) | ✅ 10 → 3 after prune, newest survives |
| Production `tauri:build` | ✅ Built `target\release\deskpet.exe` (23:42:12) |
| windows-mcp launch | ✅ Pet UI + 已连接 + 30 FPS + 👤 pill (restoreSession from F:\) |
| Settings → 数据目录 | ✅ Section renders, shows `F:\deskpet\data` as effective path |
| Backup bug fix in production | ✅ Ran deskpet TWICE in this session; new .bak count = **0** |

## Critical Verification: Persistence Across Process Boundary

Most important assertion: the relay session survived the data move AND the recompile.

- Original login: `e2e-03-account-panel.png` showed `<redacted-user@example.com>` / ¥712.95 (chinzy.com response)
- After: moved data dir from C: to F:, set env var, killed all processes, rebuilt release exe with new code, relaunched
- Result: 👤 pill appeared immediately, no login modal
- Proves: **Windows Credential Manager tokens (RelayAuthAdapter persistence) are independent of the data dir relocation**

This is the right separation: keyring is for SECRETS (tokens/keys), AppData is for STATE (db/config/device_id). Moving one doesn't break the other.

## Evidence Files

- `evidence/e2e-07-data-dir-section.png` — Settings UI showing the new section with `F:\deskpet\data` pre-filled

## Process Hygiene Notes

- Backup bug had been actively writing every ~4 seconds on previous deskpet sessions (visible in the timestamp gaps of pre-cleanup .bak files)
- After this session's fix: zero new .bak files were created during ~10 minutes of deskpet runtime
- Future schema bumps still create exactly one .bak per upgrade (since `user_version` will then be below TARGET)
- Retention caps the worst-case footprint at 3× sizeof(state.db) ~ 210 MB
