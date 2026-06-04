// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * TDD T5 — relay 充值链接常量。
 *
 * 注：vitest 跑在 node 环境（无 DOM），组件渲染（"去充值"按钮可见、点击
 * 打开外链）由人工测试 R5 覆盖。此处只锁常量契约。
 */
import { describe, expect, it } from "vitest";

import { RECHARGE_URL, DEVICE_CONSOLE_URL, PREFERRED_MODEL } from "./relayConfig";

describe("T5-3 · RECHARGE_URL", () => {
  it("is an https URL pointing at the relay domain", () => {
    expect(RECHARGE_URL).toMatch(/^https:\/\//);
    expect(RECHARGE_URL).toContain("chinzy.com");
  });
});

describe("relayConfig constants", () => {
  it("DEVICE_CONSOLE_URL is https + relay domain", () => {
    expect(DEVICE_CONSOLE_URL).toMatch(/^https:\/\/chinzy\.com/);
  });

  it("PREFERRED_MODEL is the beta default model", () => {
    expect(PREFERRED_MODEL).toBe("gpt-5.5");
  });
});
