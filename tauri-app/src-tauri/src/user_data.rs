// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! P3-S8 / P3-S9 — user-data filesystem commands exposed to the UI.
//!
//! - `open_log_dir`  — P3-S8: the "打开日志目录" button on the startup
//!   error dialog must work even when the backend refuses to spawn,
//!   so we compute the path in Rust (mirroring `backend/paths.py`) and
//!   hand off to the opener plugin.
//! - `open_app_data_dir` — conservative variant used elsewhere in the
//!   UI, opens `%AppData%\deskpet\`.
//! - `purge_user_data` — P3-S9: the SettingsPanel "完全卸载" button.
//!   Recursively removes `%AppData%\deskpet\` (and optionally
//!   `%LocalAppData%\deskpet\`), then exits the app.
//!
//! Safety: all three commands only ever touch paths under
//! `paths::user_data_dir()` / `paths::user_models_dir()`. We refuse to
//! operate if either resolves to a suspicious root (drive root, empty
//! path, or a path shorter than 3 components) — defensive guard against
//! a misconfigured env var turning "purge" into "`rm -rf C:\\`".

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::{command, AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

use crate::paths;

/// Minimum number of path components we require before we're willing to
/// delete recursively. `C:\Users\X\AppData\Roaming\deskpet` has 6 on
/// Windows; `C:\` has 1. We conservatively require ≥ 4 — this rules out
/// every plausible root/drive-letter target without false-positiving on
/// unusual env var overrides.
const MIN_PURGE_COMPONENTS: usize = 4;

fn looks_safe_to_delete(p: &Path) -> bool {
    let comps = p.components().count();
    if comps < MIN_PURGE_COMPONENTS {
        return false;
    }
    // Must end in "deskpet" or "models" or "logs" — guards against a
    // mis-pointed env var that targets e.g. "C:\Users\U\AppData\Roaming".
    matches!(
        p.file_name().and_then(|s| s.to_str()),
        Some("deskpet") | Some("models") | Some("logs")
    )
}

#[command]
pub fn open_log_dir(app: AppHandle) -> Result<(), String> {
    let log = paths::user_log_dir()
        .ok_or_else(|| "无法确定日志目录（%AppData% 不存在？）".to_string())?;
    // Make sure it exists so explorer doesn't pop "path not found".
    if let Err(e) = paths::ensure_dir(&log) {
        return Err(format!("创建日志目录失败：{e}"));
    }
    app.opener()
        .open_path(log.to_string_lossy().to_string(), None::<&str>)
        .map_err(|e| format!("打开日志目录失败：{e}"))
}

#[command]
pub fn open_app_data_dir(app: AppHandle) -> Result<(), String> {
    let dir = paths::user_data_dir()
        .ok_or_else(|| "无法确定用户数据目录".to_string())?;
    if let Err(e) = paths::ensure_dir(&dir) {
        return Err(format!("创建用户数据目录失败：{e}"));
    }
    app.opener()
        .open_path(dir.to_string_lossy().to_string(), None::<&str>)
        .map_err(|e| format!("打开目录失败：{e}"))
}

/// P3-S9 — wipe AppData (and optionally LocalAppData/models) then exit.
/// The UI shows a two-step confirmation before invoking this; we still
/// re-guard here via `looks_safe_to_delete` so a compromised UI / bad
/// env var can't turn this into an arbitrary-file-delete primitive.
#[command]
pub fn purge_user_data(app: AppHandle, include_models: bool) -> Result<(), String> {
    let data = paths::user_data_dir()
        .ok_or_else(|| "无法确定用户数据目录".to_string())?;

    if !looks_safe_to_delete(&data) {
        return Err(format!(
            "拒绝删除可疑路径：{}（组件太少或名称不是 deskpet）",
            data.display()
        ));
    }

    if data.exists() {
        std::fs::remove_dir_all(&data).map_err(|e| {
            format!("删除 {} 失败：{e}", data.display())
        })?;
    }

    if include_models {
        if let Some(models) = paths::user_models_dir() {
            if looks_safe_to_delete(&models) && models.exists() {
                // Junction-aware: on Windows, remove_dir_all follows
                // junctions by default which we do NOT want — that would
                // delete the repo's backend/models/ in dev mode. Check
                // junction status first and unlink instead.
                #[cfg(windows)]
                {
                    if let Ok(meta) = std::fs::symlink_metadata(&models) {
                        // is_symlink on Windows covers both junctions
                        // and symbolic links.
                        if meta.file_type().is_symlink() {
                            std::fs::remove_dir(&models).map_err(|e| {
                                format!("删除 junction {} 失败：{e}", models.display())
                            })?;
                        } else {
                            std::fs::remove_dir_all(&models).map_err(|e| {
                                format!("删除 {} 失败：{e}", models.display())
                            })?;
                        }
                    }
                }
                #[cfg(not(windows))]
                {
                    std::fs::remove_dir_all(&models).map_err(|e| {
                        format!("删除 {} 失败：{e}", models.display())
                    })?;
                }
            }
        }
    }

    // Kill the backend child before exiting so we don't leave a stale
    // Python process holding file handles (which would also block the
    // purge above on Windows if the backend was still logging).
    if let Some(state) = app.try_state::<crate::process_manager::BackendProcess>() {
        state.kill_child();
    }

    // Give the caller a beat to see the UI dismiss, then exit.
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(400));
        handle.exit(0);
    });
    Ok(())
}

// ──────────────────────────────────────────────────────────────────
// 2026-05-21: User-configurable data directory
//
// Background: state.db.bak files were piling up under
// %AppData%\deskpet\data (300+ files, 20 GB) because the migrator's
// backup() helper is invoked on every restart. Users want to be able
// to relocate the entire data root to a roomier drive without leaving
// PowerShell + manually setting the env var.
//
// Design:
//
//   - The single source of truth for the effective data dir is
//     `paths::user_data_dir()`, which already honours `$DESKPET_USER_DATA`
//     ahead of the `%AppData%\deskpet` fallback (see paths.rs §57).
//
//   - This module exposes IPC commands so the Settings UI can read the
//     current setting + write a new one + (optionally) move existing
//     files. Persistence is via the Windows user-level environment
//     variable so it survives reboots and is visible to all future
//     deskpet launches without DeskPet itself having to read its own
//     config first.
//
//   - We intentionally do NOT call `SetEnvironmentVariable(..., 'User')`
//     directly from Rust. Instead we shell out to PowerShell because
//     (a) the Windows API call needs HWND_BROADCAST to notify other
//     processes, which requires extra unsafe code we'd rather avoid,
//     and (b) PowerShell's `[Environment]::SetEnvironmentVariable`
//     handles the broadcast for us. The shell-out is gated by a path
//     validator so a malicious caller can't inject arbitrary commands.
//
//   - Moving data uses std::fs (copy + remove) rather than `robocopy`
//     so the behaviour stays consistent on macOS / Linux when we
//     eventually support those.
// ──────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct DataDirSetting {
    /// What `paths::user_data_dir()` would return right now, taking
    /// `DESKPET_USER_DATA` into account.
    pub effective: String,
    /// `%AppData%\deskpet` — used when the env var is unset.
    pub default: Option<String>,
    /// Value of `DESKPET_USER_DATA` as read from the env at startup.
    /// `None` if unset/empty.
    pub env_override: Option<String>,
    /// Whether the effective path currently exists.
    pub effective_exists: bool,
    /// Total size of the effective path in bytes — UI shows this as
    /// a guard rail ("you are about to move 70 MB") before kicking
    /// off a copy.
    pub effective_size_bytes: u64,
}

#[command]
pub fn get_data_dir_setting() -> Result<DataDirSetting, String> {
    let env_override = std::env::var("DESKPET_USER_DATA")
        .ok()
        .filter(|s| !s.is_empty());
    let default = paths::BaseDirs::from_env()
        .app_data
        .map(|p| p.join("deskpet").to_string_lossy().to_string());
    let effective_path = paths::user_data_dir()
        .ok_or_else(|| "无法确定用户数据目录".to_string())?;
    let effective_exists = effective_path.is_dir();
    let effective_size_bytes = if effective_exists {
        dir_size_bytes(&effective_path).unwrap_or(0)
    } else {
        0
    };
    Ok(DataDirSetting {
        effective: effective_path.to_string_lossy().to_string(),
        default,
        env_override,
        effective_exists,
        effective_size_bytes,
    })
}

/// Path validator shared by `set_data_dir_preference` and
/// `move_data_dir_contents`. Rejects empty / drive-root / paths
/// containing characters that would let an attacker break out of
/// the `[Environment]::SetEnvironmentVariable(...)` string literal.
fn validate_target_path(p: &str) -> Result<PathBuf, String> {
    let trimmed = p.trim();
    if trimmed.is_empty() {
        return Err("路径不能为空".to_string());
    }
    // Reject characters that have shell-quoting meaning in PowerShell
    // string literals. We're about to interpolate this into a `'...'`
    // single-quoted string so the only escape is the single quote
    // itself, but we also bar backtick / newline / `$` to be safe
    // against future refactors that switch to double-quoted strings.
    let bad: &[char] = &['\'', '"', '`', '\n', '\r', '$', ';', '|', '&', '<', '>'];
    if trimmed.chars().any(|c| bad.contains(&c)) {
        return Err("路径包含非法字符（引号 / 换行 / shell 元字符）".to_string());
    }
    let path = PathBuf::from(trimmed);
    // Require absolute path. Relative paths would resolve against
    // the current working directory of whichever process eventually
    // launches deskpet, which is unstable.
    if !path.is_absolute() {
        return Err("请提供绝对路径".to_string());
    }
    // Defence in depth: don't let the user pick a drive root (we'd
    // never be able to safely clean up data there). On Windows a
    // root like `C:\` decomposes into [Prefix, RootDir] — we require
    // at least ONE Normal component beneath that.
    let has_normal_segment = path
        .components()
        .any(|c| matches!(c, std::path::Component::Normal(_)));
    if !has_normal_segment {
        return Err("路径太浅，至少需要一个子目录（例如 F:\\deskpet\\data）".to_string());
    }
    Ok(path)
}

#[command]
pub fn set_data_dir_preference(new_path: String) -> Result<DataDirSetting, String> {
    let path = validate_target_path(&new_path)?;
    let path_str = path.to_string_lossy().to_string();

    // Ensure the target directory exists. If it doesn't, create it
    // here so the next deskpet launch doesn't fall back to %AppData%
    // because the override path is missing.
    std::fs::create_dir_all(&path)
        .map_err(|e| format!("无法创建目标目录 {}: {e}", path.display()))?;

    // Persist via PowerShell. The single-quoted string literal prevents
    // PowerShell expansion; `validate_target_path` rejects characters
    // that would break out of those quotes.
    #[cfg(target_os = "windows")]
    {
        let ps_script = format!(
            "[Environment]::SetEnvironmentVariable('DESKPET_USER_DATA', '{}', 'User')",
            path_str.replace('\\', "\\\\")
        );
        let output = std::process::Command::new("powershell")
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                &ps_script,
            ])
            .output()
            .map_err(|e| format!("执行 PowerShell 失败：{e}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!("写入用户环境变量失败：{stderr}"));
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        // macOS/Linux path TBD when those builds ship — for now the
        // command simply succeeds at validation and creates the dir.
        // Future: append `export DESKPET_USER_DATA=...` to a shell
        // profile file under the user's discretion.
        let _ = path_str; // silence unused warning
    }

    // Update the current process so subsequent IPC calls observe the
    // new override immediately (the change otherwise wouldn't take
    // effect until restart since std::env reads at process start).
    std::env::set_var("DESKPET_USER_DATA", &path_str);

    get_data_dir_setting()
}

/// Recursively copy `src` into `dst`, then remove `src`. We intentionally
/// don't use `std::fs::rename` even when both paths are on the same
/// drive, because that operation would silently succeed when paths
/// live on different filesystems on macOS/Linux. Copy-then-remove is
/// uniformly portable, slower but correct.
///
/// The UI calls this AFTER `set_data_dir_preference` so by the time we
/// reach here, `dst` is the freshly-validated new effective path.
#[command]
pub fn move_data_dir_contents(src: String, dst: String) -> Result<u64, String> {
    let src_path = PathBuf::from(src.trim());
    let dst_path = validate_target_path(&dst)?;

    if !src_path.is_dir() {
        // Nothing to move — surface gracefully so the UI can show
        // "no existing data to move" without treating it as an error.
        return Ok(0);
    }
    if src_path == dst_path {
        return Err("源目录和目标目录相同".to_string());
    }
    // Refuse to move into a sub-path of the source — would create
    // infinite recursion and silently leave the user with garbage.
    if dst_path.starts_with(&src_path) {
        return Err("目标目录不能是源目录的子目录".to_string());
    }

    std::fs::create_dir_all(&dst_path)
        .map_err(|e| format!("无法创建目标目录: {e}"))?;
    let copied = copy_dir_recursive(&src_path, &dst_path)?;

    // Best-effort cleanup. If a file is locked (e.g. another deskpet
    // process still has state.db open) we surface the partial copy
    // so the user knows what to do — we don't try to be clever about
    // retrying.
    if let Err(e) = std::fs::remove_dir_all(&src_path) {
        return Err(format!(
            "复制完成（{} 字节），但清理源目录失败：{e}。可以手动删除：{}",
            copied,
            src_path.display(),
        ));
    }
    Ok(copied)
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<u64, String> {
    let mut total: u64 = 0;
    let entries = std::fs::read_dir(src)
        .map_err(|e| format!("读取 {} 失败：{e}", src.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("枚举条目失败：{e}"))?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        let file_type = entry
            .file_type()
            .map_err(|e| format!("读取类型失败：{e}"))?;
        if file_type.is_dir() {
            std::fs::create_dir_all(&to)
                .map_err(|e| format!("创建子目录 {} 失败：{e}", to.display()))?;
            total = total.saturating_add(copy_dir_recursive(&from, &to)?);
        } else if file_type.is_file() {
            let copied = std::fs::copy(&from, &to)
                .map_err(|e| format!("复制 {} 失败：{e}", from.display()))?;
            total = total.saturating_add(copied);
        }
        // symlinks: skip rather than follow — copying targets blindly
        // can suck in arbitrary content the user didn't intend to move.
    }
    Ok(total)
}

fn dir_size_bytes(path: &Path) -> std::io::Result<u64> {
    let mut total: u64 = 0;
    for entry in std::fs::read_dir(path)? {
        let entry = entry?;
        let ft = entry.file_type()?;
        if ft.is_dir() {
            total = total.saturating_add(dir_size_bytes(&entry.path())?);
        } else if ft.is_file() {
            total = total.saturating_add(entry.metadata()?.len());
        }
    }
    Ok(total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn rejects_drive_root() {
        assert!(!looks_safe_to_delete(Path::new("C:\\")));
    }

    #[test]
    fn rejects_shallow_path() {
        assert!(!looks_safe_to_delete(Path::new("C:\\Users\\X")));
    }

    #[test]
    fn rejects_appdata_root_itself() {
        // Name is "Roaming", not "deskpet".
        let p = PathBuf::from("C:/Users/U/AppData/Roaming");
        assert!(!looks_safe_to_delete(&p));
    }

    #[test]
    fn accepts_deskpet_dir() {
        let p = PathBuf::from("C:/Users/U/AppData/Roaming/deskpet");
        assert!(looks_safe_to_delete(&p));
    }

    #[test]
    fn accepts_models_dir() {
        let p = PathBuf::from("C:/Users/U/AppData/Local/deskpet/models");
        assert!(looks_safe_to_delete(&p));
    }

    #[test]
    fn accepts_logs_dir() {
        let p = PathBuf::from("C:/Users/U/AppData/Roaming/deskpet/logs");
        assert!(looks_safe_to_delete(&p));
    }

    // ── 2026-05-21: data-dir setting validator + copy helpers ──

    #[test]
    fn validate_target_path_rejects_empty() {
        assert!(validate_target_path("").is_err());
        assert!(validate_target_path("   ").is_err());
    }

    #[test]
    fn validate_target_path_rejects_relative() {
        // Relative paths would resolve against an unstable CWD —
        // bar them before the env var lands and breaks future launches.
        assert!(validate_target_path("deskpet\\data").is_err());
        assert!(validate_target_path("./data").is_err());
    }

    #[test]
    fn validate_target_path_rejects_shell_metachars() {
        // Defence against argument injection — these would let an
        // attacker break out of the PowerShell single-quoted literal.
        for bad in ["F:\\a'b", "F:\\a\"b", "F:\\a`b", "F:\\a;rm", "F:\\a|x", "F:\\a$x"] {
            assert!(
                validate_target_path(bad).is_err(),
                "should reject: {bad}"
            );
        }
    }

    #[test]
    fn validate_target_path_rejects_drive_root() {
        // `C:\` parses to a single component. We need at least 2.
        assert!(validate_target_path("C:\\").is_err());
    }

    #[test]
    fn validate_target_path_accepts_normal_path() {
        let ok = validate_target_path("F:\\deskpet\\data").unwrap();
        assert_eq!(ok, PathBuf::from("F:\\deskpet\\data"));
    }

    #[test]
    fn copy_dir_recursive_handles_nested_tree() {
        // Build a small fixture under temp and verify size accounting.
        let tmp = std::env::temp_dir().join(format!(
            "deskpet-copy-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let src = tmp.join("src");
        let dst = tmp.join("dst");
        std::fs::create_dir_all(src.join("a")).unwrap();
        std::fs::write(src.join("root.txt"), b"hello").unwrap();
        std::fs::write(src.join("a").join("nested.txt"), b"world!").unwrap();

        let copied = copy_dir_recursive(&src, &dst).unwrap();
        assert_eq!(copied, 5 + 6); // "hello" + "world!"
        assert!(dst.join("root.txt").exists());
        assert!(dst.join("a").join("nested.txt").exists());

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn dir_size_bytes_sums_all_files() {
        let tmp = std::env::temp_dir().join(format!(
            "deskpet-size-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(tmp.join("sub")).unwrap();
        std::fs::write(tmp.join("a.bin"), vec![0u8; 1000]).unwrap();
        std::fs::write(tmp.join("sub").join("b.bin"), vec![0u8; 2500]).unwrap();
        assert_eq!(dir_size_bytes(&tmp).unwrap(), 3500);
        std::fs::remove_dir_all(&tmp).ok();
    }
}
