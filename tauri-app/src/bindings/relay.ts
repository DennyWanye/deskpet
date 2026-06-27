// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * W2 (relay integration): typed wrappers around the Rust `secrets::*`
 * relay slots + `device::*` commands.
 *
 * Mirrors the discipline established by `bindings/secrets.ts`: UI code
 * MUST go through this module rather than calling `invoke("...")`
 * directly. Concentrating the IPC surface in one place means the
 * closed-source RelayAuthAdapter has a single, stable seam to talk
 * to the OS-credential layer, and the OSS build can mock these by
 * stubbing one module instead of intercepting `invoke` globally.
 */
import { invoke } from "@tauri-apps/api/core";

// ── Access token ───────────────────────────────────────────────────

export async function setRelayAccessToken(token: string): Promise<void> {
  await invoke("set_relay_access_token", { token });
}

export async function getRelayAccessToken(): Promise<string | null> {
  const out = await invoke<string | null>("get_relay_access_token");
  return out ?? null;
}

export async function deleteRelayAccessToken(): Promise<void> {
  await invoke("delete_relay_access_token");
}

// ── Refresh token ──────────────────────────────────────────────────

export async function setRelayRefreshToken(token: string): Promise<void> {
  await invoke("set_relay_refresh_token", { token });
}

export async function getRelayRefreshToken(): Promise<string | null> {
  const out = await invoke<string | null>("get_relay_refresh_token");
  return out ?? null;
}

export async function deleteRelayRefreshToken(): Promise<void> {
  await invoke("delete_relay_refresh_token");
}

// ── Device key (tsk_xxx) ───────────────────────────────────────────

export async function setRelayDeviceKey(key: string): Promise<void> {
  await invoke("set_relay_device_key", { key });
}

export async function getRelayDeviceKey(): Promise<string | null> {
  const out = await invoke<string | null>("get_relay_device_key");
  return out ?? null;
}

export async function deleteRelayDeviceKey(): Promise<void> {
  await invoke("delete_relay_device_key");
}

/**
 * Atomic logout helper — clears all three slots in one IPC. Best-effort
 * on the Rust side: if any single slot deletion fails the others still
 * run, but the promise rejects so the UI can surface the failure.
 */
export async function clearAllRelaySecrets(): Promise<void> {
  await invoke("clear_all_relay_secrets");
}

// ── Device identity ────────────────────────────────────────────────

/**
 * Read the persisted device id from `<user_data_dir>/device_id`, or
 * generate + persist a fresh UUIDv4 on first call. Stable across
 * launches; resets only when `purge_user_data` wipes %AppData%.
 */
export async function getOrCreateDeviceId(): Promise<string> {
  return await invoke<string>("get_or_create_device_id");
}

/**
 * Human-readable device label shown in the relay's /console/devices
 * page. Currently `DeskPet/<os>` — UI may wrap with a user-editable
 * name later. No persistence: pure derivation from compile-time
 * target_os, safe to call repeatedly.
 */
export async function getDefaultDeviceName(): Promise<string> {
  return await invoke<string>("get_default_device_name");
}
