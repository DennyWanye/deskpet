/**
 * P2-1: push cloud config changes to a running backend.
 *
 * P4-S21 #1 — was a direct `fetch("http://127.0.0.1:8100/config/cloud")`,
 * which webview blocks in release builds: the production webview origin
 * is `https://tauri.localhost`, and browsers refuse https→http fetches
 * (mixed-content). We now route through a Rust IPC command that proxies
 * to the backend on our behalf — Rust holds the SHARED_SECRET, frontend
 * doesn't need to touch it.
 */

import { invoke } from "@tauri-apps/api/core";

export interface CloudConfigUpdate {
  base_url: string;
  model: string;
  api_key?: string;   // empty/absent = keep current
  strategy?: string;  // empty/absent = keep current
  /** WI-R2: false → api_key applied to the live provider but NOT
   *  persisted to llm_runtime.json (relay rotating `tsk_xxx` key).
   *  Absent/true → existing behaviour (key persisted). */
  persist_key?: boolean;
}

export interface CloudConfigResult {
  ok: boolean;
  cloud_configured: boolean;
  base_url: string;
  model: string;
  has_api_key: boolean;
  strategy: string;
}

/**
 * Push cloud config to the running backend via Rust IPC. Throws on
 * Rust-side error (the underlying HTTP error message comes through as
 * the rejection string).
 */
export async function updateCloudConfig(
  _secret: string,  // kept for legacy callers; ignored — Rust knows the secret
  update: CloudConfigUpdate,
): Promise<CloudConfigResult> {
  return invoke<CloudConfigResult>("update_cloud_config", { update });
}
