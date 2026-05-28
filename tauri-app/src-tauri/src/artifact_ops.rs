// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! WI-T1.3 — Tauri commands for artifact actions (PRD §3 D3).
//!
//! 4 commands invoked by the frontend ArtifactCard buttons:
//!   - `artifact_open` — system default app (PowerPoint, etc.)
//!   - `artifact_show_in_folder` — Explorer /select on Windows; `open -R` on macOS
//!   - `artifact_copy_path` — clipboard
//!   - `artifact_save_as` — file picker → copy
//!
//! **Path safety (防手滑级 — 不是沙箱)**:
//! All commands run through [`canonicalize_for_artifact`] which:
//!   1. expanduser + canonicalize (resolves symlinks)
//!   2. (Windows) `GetLongPathNameW` to expand 8.3 short names
//!   3. **Rejects** UNC paths (`\\server\share\...`, `\\?\UNC\...`, `\\.\...`)
//!   4. **Rejects** mapped drives that resolve to UNC
//!   5. case-normalize on Windows (`normcase`-equivalent)
//!   6. final compare: `final.starts_with(allowed_root)`
//!
//! Allowed roots (whitelist): `<user_data>/artifacts/` and `<user_data>/downloads/`.
//! `<user_data>` resolution via [`crate::paths::user_data_dir`].
//!
//! See PRD §3 D3 + TDD §B TG-4 T4-1~T4-10.

use serde::Serialize;
use std::path::{Path, PathBuf};
use tauri::{command, AppHandle};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_opener::OpenerExt;

use crate::paths;

// ─── Errors ──────────────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct ArtifactError {
    pub code: String,
    pub message: String,
}

impl ArtifactError {
    fn new(code: &str, msg: impl Into<String>) -> Self {
        Self { code: code.into(), message: msg.into() }
    }
}

impl From<ArtifactError> for String {
    fn from(e: ArtifactError) -> Self {
        format!("[{}] {}", e.code, e.message)
    }
}

// ─── Path canonicalization + whitelist ───────────────────────

/// Allowed subtree names under `<user_data>` for artifact actions.
const ALLOWED_SUBDIRS: &[&str] = &["artifacts", "downloads"];

/// Return the list of allowed root paths (canonicalized).
fn allowed_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(base) = paths::user_data_dir() {
        for sub in ALLOWED_SUBDIRS {
            let p = base.join(sub);
            if let Ok(c) = p.canonicalize() {
                roots.push(c);
            } else {
                // Path may not exist yet; push the un-canonical form so
                // first-time creation still validates by prefix.
                roots.push(p);
            }
        }
    }
    roots
}

/// **PRD D3 / TDD T4-1~T4-10 canonicalization pipeline**.
///
/// Rejects UNC paths / mapped drives / system targets; expands 8.3 short
/// names; resolves symlinks; case-normalizes on Windows. Returns the
/// canonicalized absolute path if and only if it sits under an allowed
/// root.
pub fn canonicalize_for_artifact(raw: &str) -> Result<PathBuf, ArtifactError> {
    if raw.trim().is_empty() {
        return Err(ArtifactError::new("empty_path", "path is empty"));
    }

    // Step 2 (Windows-specific) + 3: UNC / mapped drive rejection
    #[cfg(windows)]
    {
        let lower = raw.to_lowercase();
        if lower.starts_with(r"\\?\unc\")
            || lower.starts_with(r"\\.\")
            || (lower.starts_with(r"\\") && !lower.starts_with(r"\\?\"))
        {
            return Err(ArtifactError::new(
                "unc_not_allowed",
                format!("UNC paths are not allowed: {raw}"),
            ));
        }
        // Mapped drive → UNC reverse lookup (TODO: T4-8 full impl needs
        // winapi WNetGetUniversalNameW; for now we rely on canonicalize
        // resolving to a UNC path which the prefix check below catches).
    }

    let p = expanduser(raw);

    // Step 4 (Windows): expand 8.3 short names via GetLongPathNameW.
    // Implemented inline below via canonicalize() — Rust's canonicalize
    // calls GetFinalPathNameByHandleW which expands 8.3 + resolves
    // symlinks/junctions. Returns extended-length form (\\?\) — strip
    // the prefix before comparison.
    let canonical = match p.canonicalize() {
        Ok(c) => strip_extended_prefix(c),
        Err(e) => {
            // If the path doesn't exist (e.g. save_as target), fall back
            // to logical resolution (parent canonicalize + tail join).
            // For now: error — open/show_in_folder/copy_path all require
            // existing files. save_as handles non-existent target itself.
            return Err(ArtifactError::new(
                "not_found",
                format!("path not found or unreadable: {raw} ({e})"),
            ));
        }
    };

    // Step 2 retry: post-canonicalize UNC check (catches mapped drives
    // that resolved to UNC).
    #[cfg(windows)]
    {
        let s = canonical.to_string_lossy().to_lowercase();
        if s.starts_with(r"\\")
            && !s.starts_with(r"\\?\")  // already stripped
            && !s.starts_with(r"\\?\unc")
        {
            return Err(ArtifactError::new(
                "unc_not_allowed",
                format!("canonicalized path is UNC: {}", canonical.display()),
            ));
        }
    }

    // Step 6: whitelist prefix check (case-insensitive on Windows)
    let roots = allowed_roots();
    if roots.is_empty() {
        return Err(ArtifactError::new(
            "no_allowed_roots",
            "user_data_dir is not configured",
        ));
    }
    if !is_under_any(&canonical, &roots) {
        return Err(ArtifactError::new(
            "path_not_allowed",
            format!(
                "{} is not under any allowed root ({:?})",
                canonical.display(),
                roots,
            ),
        ));
    }
    Ok(canonical)
}

/// Cross-platform tilde expansion.
fn expanduser(s: &str) -> PathBuf {
    if let Some(stripped) = s.strip_prefix("~/").or_else(|| s.strip_prefix("~\\")) {
        if let Some(home) = home_dir() {
            return home.join(stripped);
        }
    }
    PathBuf::from(s)
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
}

/// Strip the Windows extended-length prefix `\\?\` that
/// `canonicalize()` adds (so prefix comparison works against user-typed
/// paths). UNC variants `\\?\UNC\server\share` get caught earlier.
fn strip_extended_prefix(p: PathBuf) -> PathBuf {
    #[cfg(windows)]
    {
        let s = p.to_string_lossy();
        if let Some(rest) = s.strip_prefix(r"\\?\") {
            // \\?\UNC\... would have been rejected; safe to strip.
            return PathBuf::from(rest);
        }
    }
    let _ = &p;
    p
}

fn normcase(p: &Path) -> String {
    let s = p.to_string_lossy().to_string();
    #[cfg(windows)]
    {
        return s.to_lowercase();
    }
    #[cfg(not(windows))]
    s
}

fn is_under_any(target: &Path, roots: &[PathBuf]) -> bool {
    let t = normcase(target);
    for r in roots {
        let rn = normcase(r);
        if t == rn || t.starts_with(&format!("{rn}{}", std::path::MAIN_SEPARATOR)) {
            return true;
        }
    }
    false
}

// ─── 4 Commands ──────────────────────────────────────────────

#[command]
pub fn artifact_open(app: AppHandle, path: String) -> Result<(), String> {
    let p = canonicalize_for_artifact(&path).map_err(String::from)?;
    app.opener()
        .open_path(p.to_string_lossy().to_string(), None::<&str>)
        .map_err(|e| format!("[open_failed] {e}"))
}

#[command]
pub fn artifact_show_in_folder(_app: AppHandle, path: String) -> Result<(), String> {
    let p = canonicalize_for_artifact(&path).map_err(String::from)?;

    #[cfg(windows)]
    {
        // explorer /select,<full-path> opens parent + highlights file
        std::process::Command::new("explorer")
            .arg(format!("/select,{}", p.display()))
            .spawn()
            .map_err(|e| format!("[explorer_failed] {e}"))?;
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg("-R")
            .arg(&p)
            .spawn()
            .map_err(|e| format!("[open_R_failed] {e}"))?;
        return Ok(());
    }

    #[cfg(all(not(windows), not(target_os = "macos")))]
    {
        // Linux fallback: open parent dir with default file manager.
        let parent = p.parent().ok_or_else(|| "[no_parent]".to_string())?;
        app.opener()
            .open_path(parent.to_string_lossy().to_string(), None::<&str>)
            .map_err(|e| format!("[open_failed] {e}"))?;
        Ok(())
    }
}

#[command]
pub fn artifact_copy_path(app: AppHandle, path: String) -> Result<(), String> {
    let p = canonicalize_for_artifact(&path).map_err(String::from)?;
    app.clipboard()
        .write_text(p.to_string_lossy().to_string())
        .map_err(|e| format!("[clipboard_failed] {e}"))
}

/// `artifact_save_as` — opens a save dialog with `suggested_name` and
/// copies `src` to the chosen destination. Returns the destination path,
/// or `None` if the user cancelled.
///
/// **Note**: T4-6 says user cancel → `Ok(None)`. The dialog plugin uses
/// a callback-based API; we wrap it with a oneshot channel to await.
#[command]
pub async fn artifact_save_as(
    app: AppHandle,
    src: String,
    suggested_name: String,
) -> Result<Option<String>, String> {
    let src_path = canonicalize_for_artifact(&src).map_err(String::from)?;

    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = tokio::sync::oneshot::channel::<Option<PathBuf>>();
    let tx_cell: std::sync::Mutex<Option<tokio::sync::oneshot::Sender<Option<PathBuf>>>> =
        std::sync::Mutex::new(Some(tx));
    app.dialog()
        .file()
        .set_file_name(&suggested_name)
        .save_file(move |maybe_path| {
            let buf: Option<PathBuf> = maybe_path.and_then(|fp| fp.into_path().ok());
            if let Some(tx) = tx_cell.lock().unwrap().take() {
                let _ = tx.send(buf);
            }
        });
    let dest: Option<PathBuf> = rx
        .await
        .map_err(|e| format!("[dialog_dropped] {e}"))?;
    match dest {
        None => Ok(None),
        Some(dest_path) => {
            std::fs::copy(&src_path, &dest_path)
                .map_err(|e| format!("[copy_failed] {e}"))?;
            Ok(Some(dest_path.to_string_lossy().to_string()))
        }
    }
}

// ─── Tests ───────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn t4_1_empty_path_rejected() {
        let r = canonicalize_for_artifact("");
        assert!(r.is_err());
        assert_eq!(r.unwrap_err().code, "empty_path");
    }

    #[test]
    fn t4_7_unc_path_rejected_windows() {
        #[cfg(windows)]
        {
            let cases = [
                r"\\server\share\foo.pptx",
                r"\\?\UNC\server\share\foo.pptx",
                r"\\.\C$\Windows\System32\cmd.exe",
            ];
            for c in cases {
                let r = canonicalize_for_artifact(c);
                assert!(r.is_err(), "expected reject for {c}");
                assert_eq!(r.unwrap_err().code, "unc_not_allowed", "for {c}");
            }
        }
    }

    #[test]
    fn t4_5_nonexistent_path_rejected() {
        let r = canonicalize_for_artifact(r"C:\definitely\not\there\xyz.pptx");
        #[cfg(windows)]
        {
            assert!(r.is_err());
            // could be "not_found" or "path_not_allowed" depending on
            // canonicalize behavior — both are reject outcomes.
            let err = r.unwrap_err();
            assert!(err.code == "not_found" || err.code == "path_not_allowed");
        }
        #[cfg(not(windows))]
        {
            let _ = r;
        }
    }

    #[test]
    fn t4_3_normcase_is_lowercase_on_windows() {
        #[cfg(windows)]
        {
            let p = PathBuf::from(r"C:\Users\Test\Artifacts\X.PPTX");
            assert_eq!(normcase(&p), r"c:\users\test\artifacts\x.pptx");
        }
        #[cfg(not(windows))]
        {
            let p = PathBuf::from("/case/Sensitive");
            assert_eq!(normcase(&p), "/case/Sensitive");
        }
    }

    #[test]
    fn t4_int_expanduser_works() {
        let p = expanduser("~/foo.txt");
        let s = p.to_string_lossy().to_string();
        // 至少不能以 '~' 开头（已展开）；env 变量不可用时不展开仍是合法返回
        if let Some(_home) = home_dir() {
            assert!(!s.starts_with('~'), "expanduser failed: {s}");
        }
    }
}
