// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

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

// W2 (relay integration): separate service namespace for relay-station
// credentials. Three independent slots so login/logout can clear all
// three atomically without touching the legacy cloud-LLM key.
const RELAY_SERVICE: &str = "deskpet-relay";
const RELAY_ACCESS: &str = "access_token";
const RELAY_REFRESH: &str = "refresh_token";
const RELAY_DEVICE_KEY: &str = "device_key";

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, USERNAME).map_err(|e| format!("keyring entry init failed: {e}"))
}

fn relay_entry(slot: &str) -> Result<Entry, String> {
    Entry::new(RELAY_SERVICE, slot)
        .map_err(|e| format!("relay keyring entry init failed ({slot}): {e}"))
}

/// Generic get/set/delete helpers parameterised by slot. Kept inline rather
/// than turning into a public trait — three slots is below the threshold
/// where abstraction pays for itself.
fn relay_set(slot: &str, value: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err(format!("relay {slot} must not be empty"));
    }
    relay_entry(slot)?
        .set_password(value)
        .map_err(|e| format!("relay set {slot}: {e}"))
}

fn relay_get(slot: &str) -> Result<Option<String>, String> {
    match relay_entry(slot)?.get_password() {
        Ok(v) => Ok(Some(v)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("relay get {slot}: {e}")),
    }
}

fn relay_delete(slot: &str) -> Result<(), String> {
    match relay_entry(slot)?.delete_credential() {
        Ok(_) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("relay delete {slot}: {e}")),
    }
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

// ──────────────────────────────────────────────────────────────
// W2: relay-station credential slots
//
// Three independent slots (access_token / refresh_token / device_key)
// — never combined into one JSON blob because the OS credential
// manager is the security boundary, and we want a granular audit
// trail. The frontend `RelayAuthAdapter` is the sole caller and
// invokes these IPC commands directly.
//
// Empty-string rejection mirrors the legacy `set_cloud_api_key`
// behaviour: keyring backends on some platforms tolerate empty
// secrets, but we treat them as a programmer error.
// ──────────────────────────────────────────────────────────────

#[tauri::command]
pub fn set_relay_access_token(token: String) -> Result<(), String> {
    relay_set(RELAY_ACCESS, &token)
}

#[tauri::command]
pub fn get_relay_access_token() -> Result<Option<String>, String> {
    relay_get(RELAY_ACCESS)
}

#[tauri::command]
pub fn delete_relay_access_token() -> Result<(), String> {
    relay_delete(RELAY_ACCESS)
}

#[tauri::command]
pub fn set_relay_refresh_token(token: String) -> Result<(), String> {
    relay_set(RELAY_REFRESH, &token)
}

#[tauri::command]
pub fn get_relay_refresh_token() -> Result<Option<String>, String> {
    relay_get(RELAY_REFRESH)
}

#[tauri::command]
pub fn delete_relay_refresh_token() -> Result<(), String> {
    relay_delete(RELAY_REFRESH)
}

#[tauri::command]
pub fn set_relay_device_key(key: String) -> Result<(), String> {
    relay_set(RELAY_DEVICE_KEY, &key)
}

#[tauri::command]
pub fn get_relay_device_key() -> Result<Option<String>, String> {
    relay_get(RELAY_DEVICE_KEY)
}

#[tauri::command]
pub fn delete_relay_device_key() -> Result<(), String> {
    relay_delete(RELAY_DEVICE_KEY)
}

/// One-shot logout helper: clear all three slots in a single IPC call so
/// the frontend doesn't need to chain three awaits. Any individual
/// delete failure is captured but the others still run — best-effort
/// cleanup so we don't leave one stale slot behind because another
/// platform-specific edge case failed first.
#[tauri::command]
pub fn clear_all_relay_secrets() -> Result<(), String> {
    let mut errs: Vec<String> = Vec::new();
    if let Err(e) = relay_delete(RELAY_ACCESS) {
        errs.push(e);
    }
    if let Err(e) = relay_delete(RELAY_REFRESH) {
        errs.push(e);
    }
    if let Err(e) = relay_delete(RELAY_DEVICE_KEY) {
        errs.push(e);
    }
    if errs.is_empty() {
        Ok(())
    } else {
        Err(errs.join("; "))
    }
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

    // ── W2: relay credential slot canaries ─────────────────────
    //
    // Same rationale as the legacy cloud-LLM canary above: renaming
    // either the service or any slot string without a migration would
    // strand every existing user's relay session and force them to
    // re-login. If you genuinely need to rename, add a fallback read
    // path against the old strings first, ship one release, then drop.

    #[test]
    fn relay_service_constant_is_stable() {
        assert_eq!(RELAY_SERVICE, "deskpet-relay");
    }

    #[test]
    fn relay_slot_constants_are_stable() {
        assert_eq!(RELAY_ACCESS, "access_token");
        assert_eq!(RELAY_REFRESH, "refresh_token");
        assert_eq!(RELAY_DEVICE_KEY, "device_key");
    }

    #[test]
    fn relay_set_rejects_empty_token() {
        // Same defensive guard as set_cloud_api_key; protects us against
        // a UI bug accidentally writing an empty string after a network
        // error left the response body unparsed.
        assert!(set_relay_access_token("".into()).is_err());
        assert!(set_relay_refresh_token("   ".into()).is_err());
        assert!(set_relay_device_key("\t\n".into()).is_err());
    }
}
