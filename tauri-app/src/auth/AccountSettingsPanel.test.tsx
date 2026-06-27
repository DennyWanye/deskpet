// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * AccountSettingsPanel - pure-helper + targeted render coverage.
 *
 * Same convention as RelayAuthModal.test.tsx: testable logic extracted
 * into named helpers, vitest pins their contracts. The component itself
 * gets narrow render coverage only for account metadata that can regress
 * without launching the full settings panel.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  AccountSettingsPanel,
  formatMoney,
  formatResetDate,
  validatePasswordChange,
} from "./AccountSettingsPanel";
import type { RelayAuthAdapter } from "./RelayAuthAdapter";
import type { User } from "./types";

afterEach(() => {
  cleanup();
});

function makeAdapter(user: User | null): RelayAuthAdapter {
  return {
    currentUser: () => user,
    getUsage: async () => null,
    onEvent: () => () => {},
    logout: async () => {},
  } as unknown as RelayAuthAdapter;
}

describe("formatMoney", () => {
  it("formats USD cents with a dollar symbol", () => {
    expect(formatMoney(75614, "USD")).toBe("$756.14");
  });

  it("formats USD zero", () => {
    expect(formatMoney(0, "USD")).toBe("$0.00");
  });

  it("defaults missing currency to USD", () => {
    expect(formatMoney(50)).toBe("$0.50");
  });

  it("formats CNY with a yuan symbol", () => {
    expect(formatMoney(71317, "CNY")).toBe("¥713.17");
  });

  it("returns em-dash for null / undefined / NaN", () => {
    expect(formatMoney(null, "USD")).toBe("—");
    expect(formatMoney(undefined, "USD")).toBe("—");
    expect(formatMoney(NaN, "USD")).toBe("—");
  });

  it("does not leak the yuan symbol into USD formatting", () => {
    expect(formatMoney(75614, "USD")).not.toContain("¥");
  });

  it("formats a huge balance", () => {
    expect(formatMoney(123456789, "USD")).toBe("$1234567.89");
  });

  it("handles non-integer minor units without exploding", () => {
    // Relay always sends integer `amount_minor`, but defend against
    // float drift in case some middleware ever serialises through
    // JSON.parse with locale tweaks. `toFixed(2)` is the contract.
    expect(formatMoney(99.5, "USD")).toBe("$0.99");
  });
});

describe("AccountSettingsPanel account badge", () => {
  const baseUser: User = {
    id: "user_123456",
    email: "paid@example.com",
    plan: "prepaid",
  };

  it("renders the test-account badge only for explicit test accounts", () => {
    render(
      <AccountSettingsPanel
        adapter={makeAdapter({ ...baseUser, is_test_account: true })}
      />,
    );

    expect(screen.getByText("paid@example.com")).toBeTruthy();
    expect(screen.getByText("测试账号")).toBeTruthy();
  });

  it("does not render the test-account badge by default", () => {
    render(<AccountSettingsPanel adapter={makeAdapter(baseUser)} />);

    expect(screen.getByText("paid@example.com")).toBeTruthy();
    expect(screen.queryByText("测试账号")).toBeNull();
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
    // We surface garbage rather than silently erasing it; easier to
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
    const errs = validatePasswordChange({
      ...valid,
      next: "short",
      confirm: "short",
    });
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
