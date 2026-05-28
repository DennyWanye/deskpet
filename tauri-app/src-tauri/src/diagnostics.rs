// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! WI-02 (beta-100) — diagnostic feedback bundle.
//!
//! The "反馈" button in the toolbar calls `build_diagnostic_bundle`.
//! We gather crash reports, recent logs and the anonymous metrics file
//! into a staging dir, write a redacted `meta.json` + the user's note,
//! then zip it with PowerShell's `Compress-Archive` (no new Rust dep).
//!
//! **Privacy contract — enforced here and by tests:**
//! - The bundle NEVER contains the API key. `llm_runtime.json` is NOT
//!   copied verbatim; only a redacted `{base_url, model}` pair goes
//!   into `meta.json`.
//! - `meta.json` is built field-by-field from a fixed allow-list — there
//!   is no "copy the whole config" path.
//! - OS credential store is never read.
//!
//! Failure philosophy: a missing source dir is recorded as `"missing"`
//! in `meta.json` and skipped — we never abort the whole bundle just
//! because one input is absent.

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::{command, AppHandle};

use crate::paths;

#[derive(Debug, Serialize)]
pub struct DiagnosticBundle {
    pub zip_path: String,
    pub size_bytes: u64,
    /// Per-source collection status, e.g. {"crash_reports": "ok", ...}.
    pub collected: std::collections::BTreeMap<String, String>,
}

fn timestamp() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Recursively copy `src` dir into `dst` dir. Best-effort: returns the
/// number of files copied, swallows per-file errors.
fn copy_dir(src: &Path, dst: &Path) -> usize {
    let mut n = 0;
    if std::fs::create_dir_all(dst).is_err() {
        return 0;
    }
    let entries = match std::fs::read_dir(src) {
        Ok(e) => e,
        Err(_) => return 0,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        let target = dst.join(&name);
        if path.is_dir() {
            n += copy_dir(&path, &target);
        } else if std::fs::copy(&path, &target).is_ok() {
            n += 1;
        }
    }
    n
}

/// Copy the N most-recently-modified files from `src` into `dst`.
fn copy_recent_files(src: &Path, dst: &Path, keep: usize) -> usize {
    if std::fs::create_dir_all(dst).is_err() {
        return 0;
    }
    let mut files: Vec<(PathBuf, std::time::SystemTime)> = match std::fs::read_dir(src) {
        Ok(rd) => rd
            .flatten()
            .filter(|e| e.path().is_file())
            .filter_map(|e| {
                let m = e.metadata().ok()?.modified().ok()?;
                Some((e.path(), m))
            })
            .collect(),
        Err(_) => return 0,
    };
    files.sort_by(|a, b| b.1.cmp(&a.1)); // newest first
    let mut n = 0;
    for (path, _) in files.into_iter().take(keep) {
        if let Some(fname) = path.file_name() {
            if std::fs::copy(&path, dst.join(fname)).is_ok() {
                n += 1;
            }
        }
    }
    n
}

/// Read `llm_runtime.json` and return ONLY the non-secret fields.
/// The `api_key` field is dropped — never copied into the bundle.
fn redacted_provider_info(data_dir: &Path) -> serde_json::Value {
    let runtime = data_dir.join("llm_runtime.json");
    let parsed = std::fs::read_to_string(&runtime)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok());
    match parsed {
        Some(v) => serde_json::json!({
            "base_url": v.get("base_url").and_then(|x| x.as_str()).unwrap_or(""),
            "model": v.get("model").and_then(|x| x.as_str()).unwrap_or(""),
            // NB: api_key intentionally absent — privacy contract.
            "has_api_key": v.get("api_key")
                .and_then(|x| x.as_str())
                .map(|s| !s.is_empty())
                .unwrap_or(false),
        }),
        None => serde_json::json!({"base_url": "", "model": "", "has_api_key": false}),
    }
}

fn dir_file_size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

/// Build the diagnostic zip. `user_note` is the free-text problem
/// description from the feedback panel.
#[command]
pub fn build_diagnostic_bundle(
    app: AppHandle,
    user_note: String,
) -> Result<DiagnosticBundle, String> {
    let _ = &app; // version pulled below; keep handle for future use
    let data_dir = paths::user_data_dir()
        .ok_or_else(|| "cannot resolve user data dir".to_string())?;
    let log_dir = paths::user_log_dir()
        .unwrap_or_else(|| data_dir.join("logs"));

    let ts = timestamp();
    let staging = std::env::temp_dir().join(format!("deskpet-feedback-{ts}"));
    std::fs::create_dir_all(&staging)
        .map_err(|e| format!("create staging dir failed: {e}"))?;

    let mut collected: std::collections::BTreeMap<String, String> =
        std::collections::BTreeMap::new();

    // --- crash_reports/ (try a couple of plausible locations) --------
    let crash_candidates = [
        std::env::current_dir().ok().map(|p| p.join("crash_reports")),
        Some(data_dir.join("crash_reports")),
        Some(std::env::temp_dir().join("deskpet_crash_reports")),
    ];
    let mut crash_copied = 0;
    for cand in crash_candidates.into_iter().flatten() {
        if cand.is_dir() {
            crash_copied += copy_dir(&cand, &staging.join("crash_reports"));
        }
    }
    collected.insert(
        "crash_reports".into(),
        if crash_copied > 0 { format!("ok:{crash_copied}") } else { "missing".into() },
    );

    // --- logs/ — most recent 3 files ---------------------------------
    let logs_copied = copy_recent_files(&log_dir, &staging.join("logs"), 3);
    collected.insert(
        "logs".into(),
        if logs_copied > 0 { format!("ok:{logs_copied}") } else { "missing".into() },
    );

    // --- metrics.jsonl (WI-12) --------------------------------------
    let metrics_src = data_dir.join("metrics.jsonl");
    if metrics_src.is_file() && std::fs::copy(&metrics_src, staging.join("metrics.jsonl")).is_ok() {
        collected.insert("metrics".into(), "ok".into());
    } else {
        collected.insert("metrics".into(), "missing".into());
    }

    // --- user note ---------------------------------------------------
    let _ = std::fs::write(staging.join("user_note.txt"), &user_note);

    // --- meta.json (REDACTED, allow-list only) -----------------------
    let state_db_size = dir_file_size(&data_dir.join("data").join("state.db"));
    let app_version = app
        .config()
        .version
        .clone()
        .unwrap_or_else(|| "unknown".to_string());
    let meta = serde_json::json!({
        "app_version": app_version,
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "state_db_bytes": state_db_size,
        "provider": redacted_provider_info(&data_dir),  // api_key dropped
        "generated_at": ts,
        "note_len": user_note.chars().count(),
    });
    std::fs::write(
        staging.join("meta.json"),
        serde_json::to_string_pretty(&meta).unwrap_or_else(|_| "{}".into()),
    )
    .map_err(|e| format!("write meta.json failed: {e}"))?;

    // --- zip via PowerShell Compress-Archive (no new Rust dep) -------
    let zip_path = std::env::temp_dir().join(format!("deskpet-feedback-{ts}.zip"));
    let zip_str = zip_path.to_string_lossy().to_string();
    let staging_glob = format!("{}\\*", staging.to_string_lossy());
    let ps = format!(
        "Compress-Archive -Path '{}' -DestinationPath '{}' -Force",
        staging_glob, zip_str,
    );
    let output = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &ps])
        .output()
        .map_err(|e| format!("compress failed to spawn: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "Compress-Archive failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }

    let size = dir_file_size(&zip_path);

    // Reveal in Explorer (best-effort — failure here doesn't fail the cmd)
    let _ = std::process::Command::new("explorer")
        .args(["/select,", &zip_str])
        .spawn();

    Ok(DiagnosticBundle {
        zip_path: zip_str,
        size_bytes: size,
        collected,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn redacted_provider_drops_api_key() {
        let tmp = std::env::temp_dir().join(format!("dpd-test-{}", timestamp()));
        fs::create_dir_all(&tmp).unwrap();
        fs::write(
            tmp.join("llm_runtime.json"),
            r#"{"base_url":"https://x/v1","model":"gpt-5","api_key":"sk-SECRET-zzz"}"#,
        )
        .unwrap();
        let info = redacted_provider_info(&tmp);
        let s = serde_json::to_string(&info).unwrap();
        // The actual key value must never appear.
        assert!(!s.contains("sk-SECRET-zzz"), "api_key value leaked into meta!");
        // The `api_key` *field* (quoted key name) must be absent — note
        // `has_api_key` is a different, allowed boolean field, so we
        // match the exact quoted token `"api_key"`.
        assert!(!s.contains("\"api_key\""), "api_key field leaked into meta!");
        assert!(s.contains("https://x/v1"));
        assert!(s.contains("\"has_api_key\":true"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn redacted_provider_missing_file_is_safe() {
        let tmp = std::env::temp_dir().join(format!("dpd-test-missing-{}", timestamp()));
        let info = redacted_provider_info(&tmp);
        assert_eq!(info.get("base_url").unwrap().as_str().unwrap(), "");
        assert_eq!(info.get("has_api_key").unwrap().as_bool().unwrap(), false);
    }

    #[test]
    fn copy_recent_keeps_newest_n() {
        let src = std::env::temp_dir().join(format!("dpd-src-{}", timestamp()));
        let dst = std::env::temp_dir().join(format!("dpd-dst-{}", timestamp()));
        fs::create_dir_all(&src).unwrap();
        for i in 0..6 {
            fs::write(src.join(format!("log{i}.txt")), format!("line {i}")).unwrap();
        }
        let n = copy_recent_files(&src, &dst, 3);
        assert_eq!(n, 3);
        let kept = fs::read_dir(&dst).unwrap().count();
        assert_eq!(kept, 3);
        let _ = fs::remove_dir_all(&src);
        let _ = fs::remove_dir_all(&dst);
    }
}
