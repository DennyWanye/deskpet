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

use std::path::Path;

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
}
