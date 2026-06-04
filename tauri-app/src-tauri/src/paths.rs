// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! P3-S8 — Rust-side mirror of `backend/paths.py`.
//!
//! Both the Tauri supervisor and the Python backend need to agree on
//! *where* user data lives (`%AppData%\deskpet\`) so the UI can open
//! log / data directories without round-tripping through the backend.
//! We intentionally re-derive the paths here instead of asking the
//! backend: the "open log dir" button must work **even when the backend
//! refuses to start** (that's precisely when the user most needs it).
//!
//! Priority (per env var):
//!   user_data_dir  → `$DESKPET_USER_DATA` || `%AppData%\deskpet`
//!   user_log_dir   → `$DESKPET_USER_LOG`  || `<user_data>\logs`
//!   user_models_dir → `$DESKPET_MODEL_ROOT` || `%LocalAppData%\deskpet\models`
//!
//! The `resolve_*_with` variants accept an env-lookup closure and a
//! pair of base-dir overrides (AppData / LocalAppData) so tests can
//! exercise every branch without mutating real process environment.

use std::path::{Path, PathBuf};

pub type EnvLookup<'a> = &'a dyn Fn(&str) -> Option<String>;

#[derive(Debug, Clone)]
pub struct BaseDirs {
    pub app_data: Option<PathBuf>,      // %AppData% (Roaming)
    pub local_app_data: Option<PathBuf>, // %LocalAppData%
}

impl BaseDirs {
    /// Resolve base dirs from the real OS environment.
    #[cfg(not(test))]
    pub fn from_env() -> Self {
        Self {
            app_data: std::env::var("APPDATA").ok().map(PathBuf::from),
            local_app_data: std::env::var("LOCALAPPDATA").ok().map(PathBuf::from),
        }
    }

    #[cfg(test)]
    pub fn from_env() -> Self {
        // Under cfg(test) we never want to touch real %AppData%.
        Self { app_data: None, local_app_data: None }
    }
}

fn resolve_with(
    env_key: &str,
    env_lookup: EnvLookup<'_>,
    fallback: Option<PathBuf>,
) -> Option<PathBuf> {
    if let Some(v) = env_lookup(env_key).filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    fallback
}

/// Portable-mode userdata dir: when running from a frozen install, data
/// lives in `<install>/userdata/` next to the exe (mirrors Python
/// `backend/paths.py::_portable_userdata_dir`). Returns None in dev mode
/// (current_exe isn't in the install layout) or when no userdata/ exists.
///
/// Layout: `<install>/deskpet.exe` + `<install>/userdata/`; the backend
/// exe sits at `<install>/backend/deskpet-backend.exe`, so we also check
/// one level up when the exe's parent is named "backend".
#[cfg(not(test))]
fn portable_userdata_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let parent = exe.parent()?;
    let mut anchors: Vec<PathBuf> = vec![parent.to_path_buf()];
    if parent.file_name().and_then(|s| s.to_str()) == Some("backend") {
        if let Some(up) = parent.parent() {
            anchors.insert(0, up.to_path_buf());
        }
    }
    for anchor in anchors {
        let ud = anchor.join("userdata");
        if ud.is_dir() {
            return Some(ud);
        }
    }
    None
}

#[cfg(test)]
fn portable_userdata_dir() -> Option<PathBuf> {
    // 测试下不探测真实 current_exe(避免 test runner 旁碰巧的 userdata 干扰
    // 现有单测的 %AppData% 期望)。
    None
}

pub fn user_data_dir_with(base: &BaseDirs, env_lookup: EnvLookup<'_>) -> Option<PathBuf> {
    // 1. DESKPET_USER_DATA_DIR — 与 Python backend/paths.py 统一的 env 名
    //    (历史 bug: Rust 读 DESKPET_USER_DATA、Python 读 DESKPET_USER_DATA_DIR,
    //    名字不一致 → device_id/onboarding 和 config/db 落到不同目录)。
    if let Some(v) = env_lookup("DESKPET_USER_DATA_DIR").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    // 2. 兼容旧 DESKPET_USER_DATA(无 _DIR) — 老用户可能设过,别破坏。
    if let Some(v) = env_lookup("DESKPET_USER_DATA").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    // 3. portable: frozen 安装时 <install>/userdata(与 Python portable 一致)。
    if let Some(p) = portable_userdata_dir() {
        return Some(p);
    }
    // 4. classic: %AppData%\deskpet。
    base.app_data.as_ref().map(|p| p.join("deskpet"))
}

pub fn user_log_dir_with(base: &BaseDirs, env_lookup: EnvLookup<'_>) -> Option<PathBuf> {
    if let Some(v) = env_lookup("DESKPET_USER_LOG").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    user_data_dir_with(base, env_lookup).map(|p| p.join("logs"))
}

pub fn user_models_dir_with(base: &BaseDirs, env_lookup: EnvLookup<'_>) -> Option<PathBuf> {
    if let Some(v) = env_lookup("DESKPET_MODEL_ROOT").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    // portable: <install>/userdata/models(与 Python paths.py 一致)。
    if let Some(p) = portable_userdata_dir() {
        return Some(p.join("models"));
    }
    base.local_app_data.as_ref().map(|p| p.join("deskpet").join("models"))
}

// ---- Public convenience wrappers reading real env ----
// Note: BaseDirs::from_env() returns empty under cfg(test), so these
// helpers safely no-op in unit-test binaries instead of leaking into
// the real %AppData%.

fn real_env(k: &str) -> Option<String> { std::env::var(k).ok() }

pub fn user_data_dir() -> Option<PathBuf> {
    user_data_dir_with(&BaseDirs::from_env(), &real_env)
}

pub fn user_log_dir() -> Option<PathBuf> {
    user_log_dir_with(&BaseDirs::from_env(), &real_env)
}

pub fn user_models_dir() -> Option<PathBuf> {
    user_models_dir_with(&BaseDirs::from_env(), &real_env)
}

/// Ensure `path` exists, creating parent dirs as needed. No-op if Some
/// already points to an existing directory. Returns the path back for
/// chaining in the Tauri command handlers.
#[allow(dead_code)]
pub fn ensure_dir(path: &Path) -> std::io::Result<()> {
    if path.is_dir() {
        return Ok(());
    }
    std::fs::create_dir_all(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env_map(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |k: &str| {
            pairs
                .iter()
                .find(|(kk, _)| *kk == k)
                .map(|(_, v)| v.to_string())
        }
    }

    fn env_empty() -> impl Fn(&str) -> Option<String> {
        |_: &str| None
    }

    fn base_win() -> BaseDirs {
        BaseDirs {
            app_data: Some(PathBuf::from("C:/Users/U/AppData/Roaming")),
            local_app_data: Some(PathBuf::from("C:/Users/U/AppData/Local")),
        }
    }

    #[test]
    fn user_data_dir_defaults_to_appdata_deskpet() {
        let env = env_empty();
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Roaming/deskpet"));
    }

    #[test]
    fn user_data_dir_env_override_wins() {
        let env = env_map(&[("DESKPET_USER_DATA", "D:/custom/deskpet")]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("D:/custom/deskpet"));
    }

    #[test]
    fn user_data_dir_prefers_dir_suffix_env() {
        // 统一后: DESKPET_USER_DATA_DIR(与 Python 一致)优先于旧 DESKPET_USER_DATA。
        let env = env_map(&[
            ("DESKPET_USER_DATA_DIR", "D:/new/userdata"),
            ("DESKPET_USER_DATA", "E:/old/deskpet"),
        ]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("D:/new/userdata"));
    }

    #[test]
    fn user_data_dir_falls_back_to_legacy_env() {
        // 只设旧 DESKPET_USER_DATA 时仍兼容(不破坏老用户)。
        let env = env_map(&[("DESKPET_USER_DATA", "E:/old/deskpet")]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("E:/old/deskpet"));
    }

    #[test]
    fn user_data_dir_empty_env_treated_as_unset() {
        let env = env_map(&[("DESKPET_USER_DATA", "")]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Roaming/deskpet"));
    }

    #[test]
    fn user_log_dir_nests_under_user_data() {
        let env = env_empty();
        let out = user_log_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Roaming/deskpet/logs"));
    }

    #[test]
    fn user_log_dir_independent_env_override() {
        let env = env_map(&[("DESKPET_USER_LOG", "E:/logs")]);
        let out = user_log_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("E:/logs"));
    }

    #[test]
    fn user_models_dir_defaults_to_local_app_data() {
        let env = env_empty();
        let out = user_models_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Local/deskpet/models"));
    }

    #[test]
    fn user_models_dir_env_override() {
        let env = env_map(&[("DESKPET_MODEL_ROOT", "F:/models")]);
        let out = user_models_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("F:/models"));
    }

    #[test]
    fn missing_app_data_returns_none() {
        let base = BaseDirs { app_data: None, local_app_data: None };
        let env = env_empty();
        assert!(user_data_dir_with(&base, &env).is_none());
        assert!(user_log_dir_with(&base, &env).is_none());
        assert!(user_models_dir_with(&base, &env).is_none());
    }
}
