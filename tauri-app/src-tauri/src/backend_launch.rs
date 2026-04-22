//! P3-S3 — Backend path resolver (Supervisor 自管 backend 路径).
//!
//! The Rust supervisor is now the single source of truth for where
//! the Python backend lives. Before this slice the frontend hard-coded
//! `G:/projects/deskpet/backend/.venv/...` into the `start_backend`
//! invoke — fine for the author's dev box, catastrophic for any
//! packaged release.
//!
//! Resolution priority (first match wins):
//! 1. **Bundled**: `<bundle_root>/backend/deskpet-backend.exe` exists
//!    → run the PyInstaller frozen exe directly.
//! 2. **Dev via env**: `DESKPET_BACKEND_DIR` set → run
//!    `python main.py` under that dir. `DESKPET_PYTHON` overrides
//!    the interpreter; otherwise fall back to `<dir>/.venv/Scripts/python.exe`.
//! 3. **Dev fallback**: `<DESKPET_DEV_ROOT>/backend` exists (build.rs
//!    injects `DESKPET_DEV_ROOT = CARGO_MANIFEST_DIR/../..`) → same
//!    shape as (2) but paths are compile-time constants.
//! 4. Otherwise `Err(ResolveError::NoBackendFound)` — caller is
//!    expected to surface `format_user_message(...)` in a dialog.
//!
//! The core function `resolve_with(bundle_root, env_lookup)` is
//! dependency-injected so tests drive every priority branch without
//! mutating real environment variables (which would be racy under
//! `cargo test`'s default parallel runner).

use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
#[cfg_attr(test, derive(PartialEq, Eq))]
pub enum BackendLaunch {
    /// Packaged build: run the frozen `deskpet-backend.exe` directly.
    /// No Python interpreter needed.
    Bundled { exe: PathBuf },
    /// Source checkout: `python main.py` with a cwd.
    Dev { python: PathBuf, backend_dir: PathBuf },
}

#[derive(Debug)]
#[cfg_attr(test, derive(PartialEq, Eq))]
pub enum ResolveError {
    /// None of the priority tiers matched; `tried` lists what we
    /// looked at (for log / dialog).
    NoBackendFound { tried: Vec<String> },
    /// User set `DESKPET_PYTHON` but the file isn't there.
    DevPythonMissing(PathBuf),
    /// Bundle layout found the exe path but the file itself is gone
    /// (e.g. installer corrupted). Reserved for P3-S5 when the bundled
    /// exe path becomes a hard requirement on release builds.
    #[allow(dead_code)]
    BundleExeMissing(PathBuf),
}

/// User-facing dialog message (Chinese). Pure function.
pub fn format_user_message(err: &ResolveError) -> String {
    match err {
        ResolveError::NoBackendFound { tried } => format!(
            "DeskPet 找不到 Python backend。\n\n\
             尝试过的位置：\n{}\n\n\
             如果你是开发者，请设置 DESKPET_BACKEND_DIR 环境变量指向仓库的 backend/ 目录，\
             或把代码检出到 CARGO_MANIFEST_DIR/../../backend。\n\
             如果你是普通用户，这说明安装包损坏，请重新安装。",
            tried.iter().map(|s| format!("  • {s}")).collect::<Vec<_>>().join("\n"),
        ),
        ResolveError::DevPythonMissing(p) => format!(
            "DESKPET_PYTHON 指向的解释器不存在：\n{}\n\n\
             请检查环境变量或重新创建 .venv。",
            p.display(),
        ),
        ResolveError::BundleExeMissing(p) => format!(
            "DeskPet 安装损坏：找不到 backend 可执行文件。\n\n\
             期望位置：\n{}\n\n\
             请卸载后重新安装。",
            p.display(),
        ),
    }
}

/// Trait object–style env lookup so tests can inject a fake map
/// without touching `std::env::var`. Return `None` for unset.
pub type EnvLookup<'a> = &'a dyn Fn(&str) -> Option<String>;

/// Check whether a path points to an existing file. Wrapped into a
/// trait-ish function so tests can override it (see `resolve_with_fs`).
#[allow(dead_code)] // wired to resolve() which is cfg(not(test))
fn default_exists(p: &Path) -> bool {
    p.is_file()
}

/// Core resolver.
///
/// `bundle_root` is `app.path().resource_dir()` at runtime; in tests
/// we pass a `TempDir` path (or `None` to simulate "no bundle").
/// `env_lookup` is an injected closure.
#[allow(dead_code)] // wired to resolve() which is cfg(not(test))
pub fn resolve_with(
    bundle_root: Option<&Path>,
    env_lookup: EnvLookup<'_>,
) -> Result<BackendLaunch, ResolveError> {
    resolve_with_fs(bundle_root, env_lookup, default_exists)
}

/// Same as `resolve_with` but with an injectable filesystem check —
/// used by unit tests to simulate presence/absence without touching
/// disk.
pub fn resolve_with_fs(
    bundle_root: Option<&Path>,
    env_lookup: EnvLookup<'_>,
    exists: fn(&Path) -> bool,
) -> Result<BackendLaunch, ResolveError> {
    let mut tried: Vec<String> = Vec::new();

    // Priority 1: bundle
    if let Some(root) = bundle_root {
        let exe = root.join("backend").join("deskpet-backend.exe");
        tried.push(format!("bundle: {}", exe.display()));
        if exists(&exe) {
            return Ok(BackendLaunch::Bundled { exe });
        }
        // If bundle_root was provided but exe is missing, fall through
        // to env/dev — user might be in `cargo run` where resource_dir
        // points at something weird. NoBackendFound at the end still
        // lists this path so they can see it was checked.
    }

    // Priority 2: DESKPET_BACKEND_DIR env
    if let Some(dir_str) = env_lookup("DESKPET_BACKEND_DIR").filter(|s| !s.is_empty()) {
        let backend_dir = PathBuf::from(&dir_str);
        tried.push(format!("env DESKPET_BACKEND_DIR: {}", backend_dir.display()));

        let python = match env_lookup("DESKPET_PYTHON").filter(|s| !s.is_empty()) {
            Some(p) => {
                let pp = PathBuf::from(p);
                if !exists(&pp) {
                    return Err(ResolveError::DevPythonMissing(pp));
                }
                pp
            }
            None => default_venv_python(&backend_dir),
        };
        // We don't hard-require backend_dir/main.py to exist here — spawn
        // will fail loudly if the user pointed somewhere silly. But the
        // python file we DO check, because a missing interpreter gives
        // a less-obvious error at spawn time.
        return Ok(BackendLaunch::Dev { python, backend_dir });
    }

    // Priority 3: dev fallback via compile-time root
    if let Some(dev_root) = option_env!("DESKPET_DEV_ROOT") {
        let backend_dir = PathBuf::from(dev_root).join("backend");
        tried.push(format!("DESKPET_DEV_ROOT: {}", backend_dir.display()));
        if exists(&backend_dir.join("main.py")) {
            let python = default_venv_python(&backend_dir);
            return Ok(BackendLaunch::Dev { python, backend_dir });
        }
    } else {
        tried.push("DESKPET_DEV_ROOT: (not injected at build time)".into());
    }

    Err(ResolveError::NoBackendFound { tried })
}

/// Default to `<backend_dir>/.venv/Scripts/python.exe` when
/// `DESKPET_PYTHON` isn't set.
fn default_venv_python(backend_dir: &Path) -> PathBuf {
    backend_dir.join(".venv").join("Scripts").join("python.exe")
}

// ----------------------------------------------------------------------
// Tauri-facing wrapper — not exercised by cargo test (needs AppHandle).
// ----------------------------------------------------------------------

#[cfg(not(test))]
pub fn resolve(app: &tauri::AppHandle) -> Result<BackendLaunch, ResolveError> {
    use tauri::Manager;
    let bundle_root = app.path().resource_dir().ok();
    let env_lookup = |k: &str| std::env::var(k).ok();
    resolve_with(bundle_root.as_deref(), &env_lookup)
}

/// Test-only stub so `process_manager` compiles under `cargo test`
/// without dragging an AppHandle through. Always returns NoBackendFound
/// with an explanatory path so if it ever leaks into a real code path
/// the error is obvious.
#[cfg(test)]
pub fn resolve(_app: &tauri::AppHandle) -> Result<BackendLaunch, ResolveError> {
    Err(ResolveError::NoBackendFound {
        tried: vec!["test-stub: resolve() unavailable under cfg(test)".into()],
    })
}

// ----------------------------------------------------------------------
// Tests
// ----------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn env_empty() -> impl Fn(&str) -> Option<String> {
        |_k: &str| None
    }

    fn env_map(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |k: &str| {
            pairs.iter()
                .find(|(kk, _)| *kk == k)
                .map(|(_, v)| v.to_string())
        }
    }

    // --- Priority 1: bundle ------------------------------------------------

    #[test]
    fn bundle_hit_returns_bundled_variant() {
        // Simulate "exe exists" via closure-over-path.
        let bundle = PathBuf::from("C:/fake/resources");
        let expected_exe = bundle.join("backend").join("deskpet-backend.exe");
        fn fake_exists(p: &Path) -> bool {
            p.to_string_lossy().ends_with("deskpet-backend.exe")
        }
        let env = env_empty();
        let out = resolve_with_fs(Some(&bundle), &env, fake_exists).unwrap();
        assert_eq!(out, BackendLaunch::Bundled { exe: expected_exe });
    }

    #[test]
    fn bundle_miss_falls_through_to_env() {
        let bundle = PathBuf::from("C:/fake/resources");
        fn no_file_exists(_p: &Path) -> bool { false }
        let env = env_map(&[("DESKPET_BACKEND_DIR", "D:/myrepo/backend")]);
        // With no_file_exists, python falls back to default path; DEV_PYTHON
        // unset so we don't check python existence.
        let out = resolve_with_fs(Some(&bundle), &env, no_file_exists);
        match out {
            Ok(BackendLaunch::Dev { backend_dir, .. }) => {
                assert_eq!(backend_dir, PathBuf::from("D:/myrepo/backend"));
            }
            other => panic!("expected Dev variant, got {other:?}"),
        }
    }

    // --- Priority 2: env ---------------------------------------------------

    #[test]
    fn env_backend_dir_sets_dev_variant() {
        fn all_exist(_p: &Path) -> bool { true }
        let env = env_map(&[("DESKPET_BACKEND_DIR", "D:/x/backend")]);
        let out = resolve_with_fs(None, &env, all_exist).unwrap();
        match out {
            BackendLaunch::Dev { python, backend_dir } => {
                assert_eq!(backend_dir, PathBuf::from("D:/x/backend"));
                // Default python path = backend_dir/.venv/Scripts/python.exe
                assert_eq!(python, PathBuf::from("D:/x/backend/.venv/Scripts/python.exe"));
            }
            other => panic!("expected Dev, got {other:?}"),
        }
    }

    #[test]
    fn env_python_override_used_when_set_and_exists() {
        fn all_exist(_p: &Path) -> bool { true }
        let env = env_map(&[
            ("DESKPET_BACKEND_DIR", "D:/x/backend"),
            ("DESKPET_PYTHON", "E:/py/python.exe"),
        ]);
        let out = resolve_with_fs(None, &env, all_exist).unwrap();
        match out {
            BackendLaunch::Dev { python, .. } => {
                assert_eq!(python, PathBuf::from("E:/py/python.exe"));
            }
            other => panic!("expected Dev, got {other:?}"),
        }
    }

    #[test]
    fn env_python_missing_file_returns_err() {
        fn nothing_exists(_p: &Path) -> bool { false }
        let env = env_map(&[
            ("DESKPET_BACKEND_DIR", "D:/x/backend"),
            ("DESKPET_PYTHON", "E:/ghost/python.exe"),
        ]);
        let out = resolve_with_fs(None, &env, nothing_exists);
        match out {
            Err(ResolveError::DevPythonMissing(p)) => {
                assert_eq!(p, PathBuf::from("E:/ghost/python.exe"));
            }
            other => panic!("expected DevPythonMissing, got {other:?}"),
        }
    }

    #[test]
    fn empty_env_values_treated_as_unset() {
        // Important on Windows where empty env vars are a real thing.
        fn nothing_exists(_p: &Path) -> bool { false }
        let env = env_map(&[("DESKPET_BACKEND_DIR", "")]);
        let out = resolve_with_fs(None, &env, nothing_exists);
        // Should NOT be Dev (empty treated as unset); should fall through
        // to priority 3 / err.
        assert!(matches!(out, Err(ResolveError::NoBackendFound { .. })),
                "expected NoBackendFound, got {out:?}");
    }

    // --- Priority 3 + failure mode -----------------------------------------

    #[test]
    fn nothing_matches_returns_no_backend_found() {
        fn nothing_exists(_p: &Path) -> bool { false }
        let env = env_empty();
        let out = resolve_with_fs(None, &env, nothing_exists);
        match out {
            Err(ResolveError::NoBackendFound { tried }) => {
                assert!(!tried.is_empty(), "tried list must not be empty");
            }
            other => panic!("expected NoBackendFound, got {other:?}"),
        }
    }

    // --- format_user_message ---------------------------------------------

    #[test]
    fn format_no_backend_lists_tried_paths() {
        let err = ResolveError::NoBackendFound {
            tried: vec!["bundle: X".into(), "env: Y".into()],
        };
        let msg = format_user_message(&err);
        assert!(msg.contains("bundle: X"));
        assert!(msg.contains("env: Y"));
        assert!(msg.contains("backend"));
    }

    #[test]
    fn format_python_missing_mentions_path() {
        let err = ResolveError::DevPythonMissing(PathBuf::from("E:/nope/python.exe"));
        let msg = format_user_message(&err);
        assert!(msg.contains("E:/nope/python.exe") || msg.contains("E:\\nope\\python.exe"));
    }

    #[test]
    fn format_bundle_missing_prompts_reinstall() {
        let err = ResolveError::BundleExeMissing(PathBuf::from("C:/x/backend.exe"));
        let msg = format_user_message(&err);
        assert!(msg.contains("重新安装") || msg.contains("重装"));
    }
}
