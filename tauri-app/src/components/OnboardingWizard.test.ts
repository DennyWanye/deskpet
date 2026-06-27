// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-01 (beta-100) — OnboardingWizard pure-logic + bindings tests.
 *
 * vitest runs in a `node` environment (no DOM), so we test the
 * extracted pure decision function `nextStepAllowed` plus the IPC
 * bindings (`onboardingStatus` / `onboardingComplete`) with a mocked
 * Tauri `invoke`. The wizard's actual click behaviour is covered by
 * the windows-mcp manual test (路径 A1).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import { nextStepAllowed, stepsForEdition } from "./OnboardingWizard";

// --- mock Tauri invoke -------------------------------------------------
const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { onboardingStatus, onboardingComplete } from "../bindings/onboarding";

beforeEach(() => {
  invokeMock.mockReset();
});

// ----------------------------------------------------------------------
// T4 — stepsForEdition + nextStepAllowed (WI-R3 step-array refactor)
// ----------------------------------------------------------------------

describe("T4-1/T4-2/T4-5 · stepsForEdition", () => {
  it("relay edition → 2 steps, no connectModel", () => {
    const steps = stepsForEdition("relay");
    expect(steps).toHaveLength(2);
    expect(steps.map((s) => s.id)).toEqual(["welcome", "ready"]);
    expect(steps.some((s) => s.id === "connectModel")).toBe(false);
  });

  it("manual edition → 3 steps incl. connectModel (regression)", () => {
    const steps = stepsForEdition("manual");
    expect(steps).toHaveLength(3);
    expect(steps.map((s) => s.id)).toEqual([
      "welcome",
      "connectModel",
      "ready",
    ]);
  });

  it("undefined edition defaults to the 3-step manual list", () => {
    expect(stepsForEdition()).toHaveLength(3);
  });
});

describe("T4-3/T4-4/T4-6 · nextStepAllowed (array + index)", () => {
  const manual = stepsForEdition("manual");
  const relay = stepsForEdition("relay");

  it("T4-3: relay welcome step always advances (no test gate)", () => {
    expect(nextStepAllowed(relay, 0, "idle")).toBe(true);
  });

  it("T4-4: manual connectModel step blocked until test ok", () => {
    expect(nextStepAllowed(manual, 1, "idle")).toBe(false);
    expect(nextStepAllowed(manual, 1, "testing")).toBe(false);
    expect(nextStepAllowed(manual, 1, "failed")).toBe(false);
    expect(nextStepAllowed(manual, 1, "ok")).toBe(true);
  });

  it("welcome step (manual) always advances", () => {
    expect(nextStepAllowed(manual, 0, "idle")).toBe(true);
  });

  it("T4-6: last step has no next — both editions", () => {
    expect(nextStepAllowed(manual, 2, "ok")).toBe(false);
    expect(nextStepAllowed(relay, 1, "ok")).toBe(false);
  });

  it("out-of-range index → false", () => {
    expect(nextStepAllowed(relay, 9, "ok")).toBe(false);
  });
});

// ----------------------------------------------------------------------
// onboardingStatus binding
// ----------------------------------------------------------------------

describe("onboardingStatus", () => {
  it("invokes onboarding_status and returns the status payload", async () => {
    invokeMock.mockResolvedValue({
      status: "needs_onboarding",
      completed_version: "",
    });
    const r = await onboardingStatus();
    expect(invokeMock).toHaveBeenCalledWith("onboarding_status");
    expect(r.status).toBe("needs_onboarding");
  });

  it("passes through the done status", async () => {
    invokeMock.mockResolvedValue({
      status: "done",
      completed_version: "0.6.0-beta.1",
    });
    const r = await onboardingStatus();
    expect(r.status).toBe("done");
    expect(r.completed_version).toBe("0.6.0-beta.1");
  });
});

// ----------------------------------------------------------------------
// onboardingComplete binding
// ----------------------------------------------------------------------

describe("onboardingComplete", () => {
  it("invokes onboarding_complete with the version arg", async () => {
    invokeMock.mockResolvedValue(undefined);
    await onboardingComplete("0.6.0-beta.2");
    expect(invokeMock).toHaveBeenCalledWith("onboarding_complete", {
      version: "0.6.0-beta.2",
    });
  });

  it("propagates a Rust-side error", async () => {
    invokeMock.mockRejectedValue("write onboarding marker failed");
    await expect(onboardingComplete("v")).rejects.toBeTruthy();
  });
});
