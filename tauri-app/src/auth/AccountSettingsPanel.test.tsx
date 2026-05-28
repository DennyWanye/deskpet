// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * AccountSettingsPanel — pure-helper coverage.
 *
 * Same convention as RelayAuthModal.test.tsx: testable logic extracted
 * into named helpers, vitest pins their contracts. The component itself
 * is verified via the windows-mcp launch smoke + manual scrutiny.
 */
import { describe, expect, it } from "vitest";

import {
  formatCny,
  formatResetDate,
  validatePasswordChange,
} from "./AccountSettingsPanel";

describe("formatCny", () => {
  it("formats a real balance into ¥X.YY", () => {
    expect(formatCny(71317)).toBe("¥713.17");
  });

  it("formats zero", () => {
    expect(formatCny(0)).toBe("¥0.00");
  });

  it("formats a single-yuan balance with leading 0", () => {
    expect(formatCny(50)).toBe("¥0.50");
  });

  it("formats a huge balance", () => {
    expect(formatCny(123456789)).toBe("¥1234567.89");
  });

  it("returns em-dash for null / undefined / NaN", () => {
    expect(formatCny(null)).toBe("—");
    expect(formatCny(undefined)).toBe("—");
    expect(formatCny(NaN)).toBe("—");
  });

  it("handles non-integer fen without exploding (relay never sends them)", () => {
    // Relay always sends integer `amount_minor`, but defend against
    // float drift in case some middleware ever serialises through
    // JSON.parse with locale tweaks. `toFixed(2)` is the contract;
    // 99.5 / 100 == 0.995, which IEEE-754 stores as 0.994999...,
    // so toFixed(2) returns "0.99". We lock in that behaviour rather
    // than the mathematical .995 → 1.00 expectation, because the
    // production input is always an integer anyway. If we ever need
    // mathematical rounding, switch to Math.round(amount_minor) / 100.
    expect(formatCny(99.5)).toBe("¥0.99");
  });
});

describe("formatResetDate", () => {
  it("renders ISO timestamps as YYYY-MM-DD (UTC)", () => {
    expect(formatResetDate("2026-06-01T00:00:00.000Z")).toBe("2026-06-01");
  });

  it("returns em-dash for null / undefined", () => {
    expect(formatResetDate(null)).toBe("—");
    expect(formatResetDate(undefined)).toBe("—");
  });

  it("returns the original string when it isn't a parseable date", () => {
    // We surface garbage rather than silently erasing it — easier to
    // notice a contract drift than to debug a missing field.
    expect(formatResetDate("not-a-date")).toBe("not-a-date");
  });

  it("zero-pads month + day", () => {
    expect(formatResetDate("2026-01-05T00:00:00.000Z")).toBe("2026-01-05");
  });
});

describe("validatePasswordChange", () => {
  const valid = {
    current: "currentPassword",
    next: "newPassword123",
    confirm: "newPassword123",
  };

  it("accepts a well-formed draft", () => {
    expect(validatePasswordChange(valid)).toEqual({});
  });

  it("rejects empty current password", () => {
    const errs = validatePasswordChange({ ...valid, current: "" });
    expect(errs.current).toMatch(/请输入当前密码/);
  });

  it("rejects empty new password", () => {
    const errs = validatePasswordChange({ ...valid, next: "" });
    expect(errs.next).toMatch(/请输入新密码/);
  });

  it("rejects new password < 8 chars", () => {
    const errs = validatePasswordChange({ ...valid, next: "short", confirm: "short" });
    expect(errs.next).toMatch(/至少 8 位/);
  });

  it("rejects new == current", () => {
    // Relay v1.3 §3.5b enforces this server-side; we mirror so the
    // user gets feedback before a roundtrip.
    const errs = validatePasswordChange({
      current: "samePassword",
      next: "samePassword",
      confirm: "samePassword",
    });
    expect(errs.next).toMatch(/不能与当前密码相同/);
  });

  it("rejects empty confirm", () => {
    const errs = validatePasswordChange({ ...valid, confirm: "" });
    expect(errs.confirm).toMatch(/请再次输入新密码/);
  });

  it("rejects mismatched confirm", () => {
    const errs = validatePasswordChange({ ...valid, confirm: "different456" });
    expect(errs.confirm).toMatch(/不一致/);
  });

  it("aggregates multiple errors (empty everything)", () => {
    const errs = validatePasswordChange({
      current: "",
      next: "",
      confirm: "",
    });
    expect(Object.keys(errs).sort()).toEqual(
      ["confirm", "current", "next"].sort(),
    );
  });
});
