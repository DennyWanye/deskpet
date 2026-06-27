// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! W2 (relay integration): stable device identifier.
//!
//! The relay station scopes device-key issuance by `X-Device-Id`. Each
//! `GET /v1/providers` call rotates the key for that deviceId only — so
//! we MUST send a stable id across launches, otherwise the user accrues
//! a phantom device every restart and the previous device key is
//! orphaned (but still valid for 1h after rotation, since access_token
//! is HMAC and can't be revoked early).
//!
//! Design:
//!
//! - Stored as a plain UTF-8 file at `<user_data_dir>/device_id`.
//! - Not stored in OS keyring: it's an identifier, not a secret. Putting
//!   it in keyring would mean an OS-credential popup every restart on
//!   Linux Secret Service, which is hostile UX for a non-secret.
//! - Generated as UUIDv4 — we don't need time-sortability, and v4 keeps
//!   the dependency surface minimal (no system-time feature required).
//! - First-use creates the file; subsequent reads return the persisted
//!   value verbatim. If the file becomes unreadable (corrupt / wrong
//!   permissions), we generate a fresh id rather than crashing the app.
//!
//! Lifetime: this id lives for as long as `%AppData%\deskpet\` exists.
//! `purge_user_data` will wipe it along with everything else, after
//! which the user effectively becomes a "new device" from the relay's
//! point of view (intentional — purge is meant to be a clean slate).

use std::fs;

use crate::paths;

const DEVICE_ID_FILE: &str = "device_id";

/// Pure helper for tests: takes the directory directly so we don't have
/// to touch real `%AppData%`. Resolves to the existing id if present,
/// otherwise writes a fresh UUIDv4 and returns it.
pub fn ensure_device_id_in(dir: &std::path::Path) -> Result<String, String> {
    let file = dir.join(DEVICE_ID_FILE);

    // Read-existing fast path. We tolerate IO errors here because a
    // missing or unreadable file just means "generate a new one" — we
    // do not want a stale ACL on the file to brick the entire login
    // flow.
    if let Ok(raw) = fs::read_to_string(&file) {
        let trimmed = raw.trim();
        if is_valid_device_id(trimmed) {
            return Ok(trimmed.to_string());
        }
        // Fall through to regenerate; this also covers the "file existed
        // but contains garbage" case where a user manually edited it.
    }

    // Generate + persist. Persistence failure is *not* fatal — return
    // the freshly-generated id anyway so the current session still has
    // a usable identifier. The relay will see it as a phantom device
    // next launch, which is the same failure mode as not having an id
    // at all, so we prefer "this session works" over "nothing works".
    let fresh = uuid::Uuid::new_v4().to_string();
    if let Err(e) = paths::ensure_dir(dir) {
        eprintln!("[device_id] ensure_dir failed: {e}");
    }
    if let Err(e) = fs::write(&file, &fresh) {
        eprintln!("[device_id] persist failed at {}: {e}", file.display());
    }
    Ok(fresh)
}

/// Defensive validation: the persisted id must be a non-empty string of
/// printable ASCII without whitespace, and ≤ 64 chars. We don't require
/// it to be a literal UUID — if a future release switches to e.g. a
/// platform-specific hardware id, the file is forward-compatible.
fn is_valid_device_id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.chars().all(|c| c.is_ascii_graphic())
}

/// Tauri IPC command — the only public entry point the frontend touches.
/// Cannot be tested directly (needs real `%AppData%`), but defers to
/// `ensure_device_id_in` for the actual logic, which IS tested below.
#[tauri::command]
pub fn get_or_create_device_id() -> Result<String, String> {
    let dir = paths::user_data_dir()
        .ok_or_else(|| "无法确定用户数据目录（%AppData% 不存在？）".to_string())?;
    ensure_device_id_in(&dir)
}

/// Display name for the device — surfaces in the relay's
/// `/console/devices` page so users can spot "which laptop is this".
/// Defaults to `DeskPet/<os>` so it's still recognisable even before
/// any per-machine customization. Pure helper for forward-compat — UI
/// may layer user-editable names on top later.
#[tauri::command]
pub fn get_default_device_name() -> String {
    let os = if cfg!(target_os = "windows") {
        "Windows"
    } else if cfg!(target_os = "macos") {
        "macOS"
    } else if cfg!(target_os = "linux") {
        "Linux"
    } else {
        "Unknown"
    };
    format!("DeskPet/{os}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn tmp_subdir(name: &str) -> PathBuf {
        let mut d = std::env::temp_dir();
        d.push(format!("deskpet-device-id-test-{name}-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn first_call_generates_and_persists() {
        let dir = tmp_subdir("first-call");
        let id = ensure_device_id_in(&dir).unwrap();
        assert!(!id.is_empty());
        let persisted = fs::read_to_string(dir.join("device_id")).unwrap();
        assert_eq!(persisted.trim(), id);
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn second_call_returns_persisted_id() {
        let dir = tmp_subdir("persisted");
        let a = ensure_device_id_in(&dir).unwrap();
        let b = ensure_device_id_in(&dir).unwrap();
        assert_eq!(a, b);
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn corrupt_file_regenerates() {
        let dir = tmp_subdir("corrupt");
        fs::write(dir.join("device_id"), "not a valid id\nwith newline\x00").unwrap();
        let id = ensure_device_id_in(&dir).unwrap();
        assert!(is_valid_device_id(&id));
        // A subsequent read should now see the regenerated id, not garbage.
        let persisted = fs::read_to_string(dir.join("device_id")).unwrap();
        assert_eq!(persisted.trim(), id);
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn empty_file_regenerates() {
        let dir = tmp_subdir("empty");
        fs::write(dir.join("device_id"), "").unwrap();
        let id = ensure_device_id_in(&dir).unwrap();
        assert!(is_valid_device_id(&id));
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn whitespace_only_file_regenerates() {
        let dir = tmp_subdir("ws");
        fs::write(dir.join("device_id"), "   \n\t  ").unwrap();
        let id = ensure_device_id_in(&dir).unwrap();
        assert!(is_valid_device_id(&id));
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn validator_accepts_uuid_v4() {
        assert!(is_valid_device_id(
            "550e8400-e29b-41d4-a716-446655440000"
        ));
    }

    #[test]
    fn validator_rejects_whitespace() {
        assert!(!is_valid_device_id(""));
        assert!(!is_valid_device_id(" "));
        assert!(!is_valid_device_id("a b"));
        assert!(!is_valid_device_id("a\tb"));
    }

    #[test]
    fn validator_rejects_overlong_id() {
        let long = "a".repeat(65);
        assert!(!is_valid_device_id(&long));
    }

    #[test]
    fn default_device_name_includes_os_tag() {
        let n = get_default_device_name();
        assert!(n.starts_with("DeskPet/"));
        assert!(n.len() > "DeskPet/".len());
    }
}
