// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import { RECHARGE_URL } from "./relayConfig";
import {
  friendlyChatErrorMessage,
  INSUFFICIENT_BALANCE_TEXT,
  RELAY_KEY_INVALID_TEXT,
} from "./relayErrorText";

describe("friendlyChatErrorMessage", () => {
  it("insufficient_balance shows recharge copy without login guidance", () => {
    const msg = friendlyChatErrorMessage({
      error_class: "insufficient_balance",
      detail: "LLM HTTP 402 Payment Required: ...",
    });

    expect(msg).toBe(INSUFFICIENT_BALANCE_TEXT);
    expect(msg).toContain("账号余额不足，请充值");
    expect(msg).toContain(RECHARGE_URL);
    expect(msg).not.toContain("重新登录");
    expect(msg).not.toContain("402");
  });

  it("relay_key_invalid shows reconnect message", () => {
    const msg = friendlyChatErrorMessage({ error_class: "relay_key_invalid" });

    expect(msg).toBe(RELAY_KEY_INVALID_TEXT);
    expect(msg).toContain("凭证");
  });

  it("no error_class falls back to reason/detail join", () => {
    expect(
      friendlyChatErrorMessage({ reason: "llm_error", detail: "boom" }),
    ).toBe("boom — llm_error");
  });

  it("empty payload returns unknown", () => {
    expect(friendlyChatErrorMessage({})).toBe("unknown");
  });

  it("unknown error_class falls back to generic join", () => {
    expect(
      friendlyChatErrorMessage({ error_class: "weird", detail: "d" }),
    ).toBe("d");
  });
});
