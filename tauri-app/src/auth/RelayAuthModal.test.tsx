// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * RelayAuthModal — pure helper coverage.
 *
 * Project convention (see AddProviderModal.test.tsx, SettingsProviders.test.tsx)
 * is to extract the testable logic into named pure functions and unit-test
 * those directly. The DOM rendering itself is verified manually + via the
 * windows-mcp launch smoke test; this file pins the validation rules and
 * the error-code → user-message mapping so they don't drift silently.
 */
import { describe, expect, it } from "vitest";

import { messageForCode, validateAuthForm } from "./RelayAuthModal";

// ── validateAuthForm: login ──────────────────────────────────────

describe("validateAuthForm (login mode)", () => {
  const base = {
    mode: "login" as const,
    email: "alice@example.com",
    password: "min8chars",
    confirm: "",
    termsAccepted: false,
  };

  it("accepts a well-formed login draft", () => {
    expect(validateAuthForm(base)).toEqual({});
  });

  it("rejects empty email", () => {
    const errs = validateAuthForm({ ...base, email: "" });
    expect(errs.email).toMatch(/请输入邮箱/);
  });

  it("rejects whitespace-only email", () => {
    const errs = validateAuthForm({ ...base, email: "   " });
    expect(errs.email).toMatch(/请输入邮箱/);
  });

  it("rejects malformed email (no @)", () => {
    const errs = validateAuthForm({ ...base, email: "not-an-email" });
    expect(errs.email).toMatch(/格式不正确/);
  });

  it("rejects malformed email (no TLD)", () => {
    const errs = validateAuthForm({ ...base, email: "alice@example" });
    expect(errs.email).toMatch(/格式不正确/);
  });

  it("rejects missing password", () => {
    const errs = validateAuthForm({ ...base, password: "" });
    expect(errs.password).toMatch(/请输入密码/);
  });

  it("rejects short password (< 8)", () => {
    // Mirrors relay v1.3 §3.1: password min length 8. We enforce it
    // client-side too so the user gets immediate feedback without a
    // round-trip + a 422 VALIDATION envelope.
    const errs = validateAuthForm({ ...base, password: "short" });
    expect(errs.password).toMatch(/至少 8 位/);
  });

  it("login mode ignores confirm + terms fields", () => {
    // The confirm + terms inputs aren't rendered in login mode; even if
    // some upstream wiring leaks values, validation must NOT block.
    const errs = validateAuthForm({
      ...base,
      confirm: "totally different",
      termsAccepted: false,
    });
    expect(errs.confirm).toBeUndefined();
    expect(errs.terms).toBeUndefined();
  });
});

// ── validateAuthForm: register ───────────────────────────────────

describe("validateAuthForm (register mode)", () => {
  const base = {
    mode: "register" as const,
    email: "newuser@example.com",
    password: "min8chars",
    confirm: "min8chars",
    termsAccepted: true,
  };

  it("accepts a well-formed register draft", () => {
    expect(validateAuthForm(base)).toEqual({});
  });

  it("rejects empty confirm", () => {
    const errs = validateAuthForm({ ...base, confirm: "" });
    expect(errs.confirm).toMatch(/请再次输入密码/);
  });

  it("rejects mismatched confirm", () => {
    const errs = validateAuthForm({ ...base, confirm: "different123" });
    expect(errs.confirm).toMatch(/不一致/);
  });

  it("rejects unchecked terms", () => {
    const errs = validateAuthForm({ ...base, termsAccepted: false });
    expect(errs.terms).toMatch(/请先同意/);
  });

  it("aggregates multiple errors (email + password + confirm + terms)", () => {
    const errs = validateAuthForm({
      mode: "register",
      email: "",
      password: "",
      confirm: "",
      termsAccepted: false,
    });
    expect(Object.keys(errs).sort()).toEqual(
      ["confirm", "email", "password", "terms"].sort(),
    );
  });
});

// ── messageForCode ───────────────────────────────────────────────

describe("messageForCode", () => {
  it("maps INVALID_CREDENTIALS to a recognisable Chinese message", () => {
    expect(messageForCode("INVALID_CREDENTIALS")).toMatch(/邮箱或密码错误/);
  });

  it("maps EMAIL_TAKEN explicitly (UI puts this next to the email field)", () => {
    expect(messageForCode("EMAIL_TAKEN")).toMatch(/已被注册/);
  });

  it("maps RATE_LIMITED to a 'try again later' message", () => {
    expect(messageForCode("RATE_LIMITED")).toMatch(/过于频繁/);
  });

  it("maps NETWORK_ERROR to a network-flavoured message", () => {
    expect(messageForCode("NETWORK_ERROR")).toMatch(/网络/);
  });

  it("maps UPSTREAM_* to a 'service unavailable' message", () => {
    expect(messageForCode("UPSTREAM_ERROR")).toMatch(/服务暂时不可用/);
    expect(messageForCode("UPSTREAM_UNAVAILABLE")).toMatch(/服务暂时不可用/);
  });

  it("maps DEVICE_KEY_MISSING to a device-key flavoured message", () => {
    // Unlikely to land in login/register UX, but if a refresh flow
    // somehow surfaces it here we want a message, not the raw code.
    expect(messageForCode("DEVICE_KEY_MISSING")).toMatch(/设备密钥/);
  });

  it("UNKNOWN falls back to provided string when given", () => {
    expect(messageForCode("UNKNOWN", "服务器返回了奇怪的内容")).toBe(
      "服务器返回了奇怪的内容",
    );
  });

  it("UNKNOWN without fallback returns a generic 'try again' string", () => {
    expect(messageForCode("UNKNOWN")).toMatch(/出错了/);
  });
});
