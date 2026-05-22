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

import { nextStepAllowed } from "./OnboardingWizard";

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
// nextStepAllowed — pure
// ----------------------------------------------------------------------

describe("nextStepAllowed", () => {
  it("step 1 can always advance (intro only)", () => {
    expect(nextStepAllowed(1, "idle")).toBe(true);
    expect(nextStepAllowed(1, "failed")).toBe(true);
  });

  it("step 2 blocked until connection test passes", () => {
    expect(nextStepAllowed(2, "idle")).toBe(false);
    expect(nextStepAllowed(2, "testing")).toBe(false);
    expect(nextStepAllowed(2, "failed")).toBe(false);
    expect(nextStepAllowed(2, "ok")).toBe(true);
  });

  it("step 3 has no next (last step)", () => {
    expect(nextStepAllowed(3, "ok")).toBe(false);
    expect(nextStepAllowed(3, "idle")).toBe(false);
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
