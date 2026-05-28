// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-01 (beta-100) — first-run onboarding IPC bindings.
 *
 * Thin typed wrappers over the Rust `onboarding_status` /
 * `onboarding_complete` commands (see `src-tauri/src/onboarding.rs`).
 * The onboarding marker lives in a file under the user data dir so the
 * decision can be made before the Python backend is up.
 */
import { invoke } from "@tauri-apps/api/core";

export interface OnboardingStatus {
  /** "needs_onboarding" | "done" */
  status: string;
  /** Version recorded when onboarding finished (empty when not done). */
  completed_version: string;
}

/**
 * Ask Rust whether the first-run wizard should be shown.
 *
 * Conservative on failure: if Rust can't resolve the data dir it
 * returns `done`, so a quirk in path resolution never traps the user
 * in a wizard loop. Callers may still wrap this in try/catch and treat
 * a thrown error as `done` for the same reason.
 */
export async function onboardingStatus(): Promise<OnboardingStatus> {
  return invoke<OnboardingStatus>("onboarding_status");
}

/**
 * Persist the "onboarding finished" marker. Called when the wizard is
 * completed OR skipped — both count as "don't show again".
 */
export async function onboardingComplete(version: string): Promise<void> {
  await invoke("onboarding_complete", { version });
}
