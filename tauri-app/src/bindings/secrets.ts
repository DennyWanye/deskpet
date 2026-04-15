/**
 * P2-1-S3: typed wrappers around the Rust `secrets::*` Tauri commands.
 *
 * UI code must go through this module — never call `invoke("set_cloud_api_key")`
 * directly. Two reasons:
 *   1. All call sites share the same argument shape & return-type guarantees.
 *   2. When Phase 3 adds per-profile keys we only refactor this file.
 *
 * Every function returns a Promise; errors propagate as rejections (the
 * Rust commands return `Result<_, String>` which Tauri serialises to a
 * rejected promise with the String as the error).
 */
import { invoke } from "@tauri-apps/api/core";

/**
 * Persist the cloud LLM API key in the OS credential store.
 * Empty/whitespace-only keys are rejected on the Rust side.
 */
export async function setCloudApiKey(key: string): Promise<void> {
  await invoke("set_cloud_api_key", { key });
}

/**
 * Read the cloud LLM API key back. Returns `null` when the user hasn't
 * configured one yet (NoEntry in keyring terms).
 *
 * NOTE: UI should rarely need the raw key — prefer `hasCloudApiKey` for
 * gating. We expose `get` mainly so the SettingsPanel can "repopulate
 * fields" debug helper stays possible.
 */
export async function getCloudApiKey(): Promise<string | null> {
  const out = await invoke<string | null>("get_cloud_api_key");
  return out ?? null;
}

/**
 * Idempotent delete — resolves even if no key was set.
 */
export async function deleteCloudApiKey(): Promise<void> {
  await invoke("delete_cloud_api_key");
}

/**
 * Cheap presence check. SettingsPanel uses this to render "已配置" vs
 * "未配置" placeholder text without ever loading the plaintext key into
 * the renderer.
 */
export async function hasCloudApiKey(): Promise<boolean> {
  return await invoke<boolean>("has_cloud_api_key");
}
