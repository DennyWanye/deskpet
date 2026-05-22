/**
 * WI-02 (beta-100) — FeedbackPanel pure-logic + bindings tests.
 *
 * node environment → no DOM. We test the extracted `isFeedbackNoteValid`
 * gate and the `buildDiagnosticBundle` binding with a mocked Tauri
 * `invoke`. The panel's click behaviour + the "no api_key in bundle"
 * guarantee are covered by the windows-mcp manual test (路径 A4) and the
 * Rust `diagnostics::tests` respectively.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import { isFeedbackNoteValid, MIN_NOTE_CHARS } from "./FeedbackPanel";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { buildDiagnosticBundle } from "../bindings/diagnostics";

beforeEach(() => {
  invokeMock.mockReset();
});

// ----------------------------------------------------------------------
// isFeedbackNoteValid — pure
// ----------------------------------------------------------------------

describe("isFeedbackNoteValid", () => {
  it("rejects notes shorter than the minimum", () => {
    expect(isFeedbackNoteValid("")).toBe(false);
    expect(isFeedbackNoteValid("太短了")).toBe(false);
    expect(isFeedbackNoteValid("123456789")).toBe(false); // 9 chars
  });

  it("accepts notes at or above the minimum", () => {
    expect(isFeedbackNoteValid("1234567890")).toBe(true); // exactly 10
    expect(isFeedbackNoteValid("点击保存后桌宠完全没有反应")).toBe(true);
  });

  it("trims whitespace before measuring", () => {
    expect(isFeedbackNoteValid("   short   ")).toBe(false);
    expect(isFeedbackNoteValid("  " + "x".repeat(MIN_NOTE_CHARS) + "  ")).toBe(true);
  });
});

// ----------------------------------------------------------------------
// buildDiagnosticBundle binding
// ----------------------------------------------------------------------

describe("buildDiagnosticBundle", () => {
  it("invokes build_diagnostic_bundle with the note", async () => {
    invokeMock.mockResolvedValue({
      zip_path: "C:\\Temp\\deskpet-feedback-1.zip",
      size_bytes: 4096,
      collected: { crash_reports: "ok:2", logs: "ok:3", metrics: "ok" },
    });
    const r = await buildDiagnosticBundle("点击保存后桌宠没反应");
    expect(invokeMock).toHaveBeenCalledWith("build_diagnostic_bundle", {
      userNote: "点击保存后桌宠没反应",
    });
    expect(r.zip_path).toContain(".zip");
    expect(r.collected.crash_reports).toBe("ok:2");
  });

  it("propagates a Rust-side failure", async () => {
    invokeMock.mockRejectedValue("Compress-Archive failed");
    await expect(buildDiagnosticBundle("note text here")).rejects.toBeTruthy();
  });

  it("surfaces a missing-source status without failing", async () => {
    invokeMock.mockResolvedValue({
      zip_path: "C:\\Temp\\x.zip",
      size_bytes: 512,
      collected: { crash_reports: "missing", logs: "ok:1", metrics: "missing" },
    });
    const r = await buildDiagnosticBundle("some problem description");
    expect(r.collected.crash_reports).toBe("missing");
    expect(r.collected.logs).toBe("ok:1");
  });
});
