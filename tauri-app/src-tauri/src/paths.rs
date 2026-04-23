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

pub fn user_data_dir_with(base: &BaseDirs, env_lookup: EnvLookup<'_>) -> Option<PathBuf> {
    resolve_with(
        "DESKPET_USER_DATA",
        env_lookup,
        base.app_data.as_ref().map(|p| p.join("deskpet")),
    )
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
