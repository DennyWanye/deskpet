// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import { formatUpdaterError } from "./updaterError";

describe("formatUpdaterError", () => {
  it("把 404 / 拉不到 release 翻译成'连不上更新服务器'", () => {
    for (const e of [
      new Error("Network Error: 404 Not Found"),
      new Error("Could not fetch a valid release JSON from the remote"),
      "ReleaseNotFound",
    ]) {
      expect(formatUpdaterError(e)).toContain("更新服务器");
    }
  });

  it("把网络类错误翻译成网络失败提示", () => {
    for (const e of [
      new Error("request timeout"),
      new Error("failed to connect"),
      new Error("dns error"),
    ]) {
      expect(formatUpdaterError(e)).toContain("网络");
    }
  });

  it("签名校验失败给出安全中止文案", () => {
    expect(formatUpdaterError(new Error("signature verify failed"))).toContain(
      "签名",
    );
  });

  it("未知错误回退到带原文的通用文案", () => {
    expect(formatUpdaterError(new Error("boom"))).toBe("检查更新出错：boom");
  });

  it("空错误也有兜底文案，不抛异常", () => {
    expect(formatUpdaterError(null)).toBe("检查更新出错，请稍后重试。");
    expect(formatUpdaterError(undefined)).toBe("检查更新出错，请稍后重试。");
  });
});
