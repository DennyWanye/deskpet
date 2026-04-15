//! P2-1-S3: Cloud LLM API key storage via the OS credential store.
//!
//! We treat Windows Credential Manager / macOS Keychain / Secret Service as
//! the source of truth for the cloud `api_key`. Every user-visible surface
//! that needs the key (Tauri → Python env injection, SettingsPanel save flow)
//! funnels through the four commands below. Design constraints:
//!
//! - Single key per install. Multi-profile ("dashscope work" + "aliyun personal")
//!   is Phase 3; exposing a slot/name now would create a migration we'd have
//!   to regret later.
//! - Commands are intentionally thin. They don't log key material, don't
//!   attempt any caching, and surface `String` errors (keyring's `Error`
//!   doesn't cross the Tauri IPC boundary cleanly).
//! - `delete` and `get_missing` are idempotent — the UI's "清除" button
//!   shouldn't explode just because the user already cleared it once.
//!
//! Tests: see `tests/` below (compiled with `cargo test`). Real keychain
//! I/O isn't exercised in CI — GitHub runners have no Credential Manager
//! provisioned and the Linux D-Bus backend isn't installed.

use keyring::Entry;

const SERVICE: &str = "deskpet-cloud-llm";
const USERNAME: &str = "default";

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, USERNAME).map_err(|e| format!("keyring entry init failed: {e}"))
}

#[tauri::command]
pub fn set_cloud_api_key(key: String) -> Result<(), String> {
    if key.trim().is_empty() {
        return Err("api key must not be empty".into());
    }
    entry()?
        .set_password(&key)
        .map_err(|e| format!("set: {e}"))
}

#[tauri::command]
pub fn get_cloud_api_key() -> Result<Option<String>, String> {
    match entry()?.get_password() {
        Ok(k) => Ok(Some(k)),
        // NoEntry is the "not configured yet" shape, not an error.
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("get: {e}")),
    }
}

#[tauri::command]
pub fn delete_cloud_api_key() -> Result<(), String> {
    match entry()?.delete_credential() {
        Ok(_) => Ok(()),
        // Idempotent — deleting a never-set key is fine.
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("delete: {e}")),
    }
}

#[tauri::command]
pub fn has_cloud_api_key() -> Result<bool, String> {
    Ok(get_cloud_api_key()?.is_some())
}

#[cfg(test)]
mod tests {
    //! These tests exercise the guard rails only (input validation,
    //! NoEntry → Ok(None) mapping by argument). They do NOT touch a real
    //! credential store — see the module doc comment.

    use super::*;

    #[test]
    fn set_rejects_empty_string() {
        assert!(set_cloud_api_key("".into()).is_err());
    }

    #[test]
    fn set_rejects_whitespace_only() {
        assert!(set_cloud_api_key("   \t\n".into()).is_err());
    }

    #[test]
    fn service_and_username_constants_are_stable() {
        // Canary: changing these strings without a migration would orphan
        // every previously-saved user key. If you need to rename, add a
        // compatibility read from the old name first.
        assert_eq!(SERVICE, "deskpet-cloud-llm");
        assert_eq!(USERNAME, "default");
    }
}
