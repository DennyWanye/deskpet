//! WI-01 (beta-100) — first-run onboarding state.
//!
//! A brand-new install has no `%AppData%\deskpet\onboarding_done.json`.
//! The frontend asks `onboarding_status` once on mount; if the marker
//! is absent it renders the 3-step `OnboardingWizard`. When the user
//! finishes (or skips) the wizard, `onboarding_complete` writes the
//! marker so the wizard never shows again.
//!
//! Why a file under `%AppData%` and not the backend DB?
//! - The onboarding decision is made *before* the Python backend (and
//!   `state.db`) is guaranteed to be up. The marker must be readable
//!   from Rust alone, at the earliest moment.
//! - It mirrors how `paths.rs` already owns "where user data lives".
//!
//! Failure philosophy: if we cannot read the marker we report
//! `done` (conservative) — better to never re-pester an existing user
//! than to risk a wizard loop. If we cannot *write* the completion
//! marker we return an error so the frontend can surface it.

use std::path::PathBuf;

use serde::Serialize;
use tauri::command;

use crate::paths;

const MARKER_FILE: &str = "onboarding_done.json";

#[derive(Debug, Serialize)]
pub struct OnboardingStatus {
    /// `"needs_onboarding"` | `"done"`
    pub status: String,
    /// Version recorded when onboarding finished (empty when not done).
    pub completed_version: String,
}

fn marker_path() -> Option<PathBuf> {
    paths::user_data_dir().map(|d| d.join(MARKER_FILE))
}

/// Frontend calls this once on mount. Absent marker → needs onboarding.
#[command]
pub fn onboarding_status() -> OnboardingStatus {
    let path = match marker_path() {
        Some(p) => p,
        None => {
            // Can't even resolve %AppData% — treat as done so we don't
            // trap the user in a wizard we can't dismiss.
            return OnboardingStatus {
                status: "done".into(),
                completed_version: String::new(),
            };
        }
    };
    if !path.is_file() {
        return OnboardingStatus {
            status: "needs_onboarding".into(),
            completed_version: String::new(),
        };
    }
    // Marker exists. Try to read the version out; tolerate a corrupt
    // file by still reporting "done" (the marker's mere existence is
    // the source of truth — version is best-effort metadata).
    let version = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| {
            v.get("version")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_default();
    OnboardingStatus {
        status: "done".into(),
        completed_version: version,
    }
}

/// Frontend calls this when the wizard finishes OR is skipped.
/// Writes `{version, completed_at}` to the marker file.
#[command]
pub fn onboarding_complete(version: String) -> Result<(), String> {
    let path = marker_path()
        .ok_or_else(|| "cannot resolve user data dir".to_string())?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("create data dir failed: {e}"))?;
    }
    let completed_at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let body = serde_json::json!({
        "version": version,
        "completed_at": completed_at,
    });
    std::fs::write(
        &path,
        serde_json::to_string_pretty(&body).unwrap_or_else(|_| "{}".into()),
    )
    .map_err(|e| format!("write onboarding marker failed: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_struct_serializes() {
        // Smoke: the Serialize derive produces the expected field names.
        let s = OnboardingStatus {
            status: "needs_onboarding".into(),
            completed_version: String::new(),
        };
        let json = serde_json::to_string(&s).unwrap();
        assert!(json.contains("\"status\""));
        assert!(json.contains("needs_onboarding"));
        assert!(json.contains("completed_version"));
    }

    #[test]
    fn marker_filename_is_stable() {
        assert_eq!(MARKER_FILE, "onboarding_done.json");
    }
}
