// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * TDD T6-3 — relay 错误码 → 友好提示。
 */
import { describe, expect, it } from "vitest";

import {
  friendlyChatErrorMessage,
  INSUFFICIENT_BALANCE_TEXT,
  RELAY_KEY_INVALID_TEXT,
} from "./relayErrorText";

describe("T6-3 · friendlyChatErrorMessage", () => {
  it("insufficient_balance → friendly 余额不足 message with 充值 hint", () => {
    const msg = friendlyChatErrorMessage({
      error_class: "insufficient_balance",
      detail: "LLM HTTP 402 Payment Required: ...",
    });
    expect(msg).toBe(INSUFFICIENT_BALANCE_TEXT);
    expect(msg).toContain("余额不足");
    expect(msg).toContain("充值");
    // raw HTTP error must NOT leak into the user-facing text
    expect(msg).not.toContain("402");
  });

  it("relay_key_invalid → friendly reconnect message", () => {
    const msg = friendlyChatErrorMessage({ error_class: "relay_key_invalid" });
    expect(msg).toBe(RELAY_KEY_INVALID_TEXT);
    expect(msg).toContain("凭证");
  });

  it("no error_class → falls back to reason/detail join", () => {
    expect(
      friendlyChatErrorMessage({ reason: "llm_error", detail: "boom" }),
    ).toBe("boom — llm_error");
  });

  it("empty payload → 'unknown'", () => {
    expect(friendlyChatErrorMessage({})).toBe("unknown");
  });

  it("unknown error_class → falls back to generic join", () => {
    expect(
      friendlyChatErrorMessage({ error_class: "weird", detail: "d" }),
    ).toBe("d");
  });
});
