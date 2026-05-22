/**
 * WI-02 (beta-100) — diagnostic feedback bundle IPC bindings.
 *
 * Wraps the Rust `build_diagnostic_bundle` command
 * (`src-tauri/src/diagnostics.rs`), which gathers crash reports, recent
 * logs and the anonymous metrics file into a redacted zip. The bundle
 * never contains the API key — that guarantee is enforced Rust-side.
 */
import { invoke } from "@tauri-apps/api/core";

export interface DiagnosticBundle {
  /** Absolute path to the generated .zip. */
  zip_path: string;
  /** Size of the zip in bytes. */
  size_bytes: number;
  /** Per-source collection status, e.g. {"crash_reports": "ok:3"}. */
  collected: Record<string, string>;
}

/**
 * Build a diagnostic zip for the given user-written problem note.
 * Rust reveals the file in Explorer as a side effect. Throws on
 * Rust-side failure (e.g. data dir unresolvable).
 */
export async function buildDiagnosticBundle(
  userNote: string,
): Promise<DiagnosticBundle> {
  return invoke<DiagnosticBundle>("build_diagnostic_bundle", { userNote });
}
