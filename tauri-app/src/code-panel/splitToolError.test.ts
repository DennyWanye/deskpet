/**
 * P5-S2 Phase 5.1 — splitToolError 解析器测试。
 *
 * 后端 P5-S2 Phase 0 给所有 os_tools 的错误返回值统一成
 *   { ok: false, error: "...", hint: "...", examples: [...] }
 * 前端 ToolResultCard 收到这种 result 时要把 hint 高亮提取出来给
 * 用户看（金黄色边框 + "💡 hint" 行），方便人类看懂为什么失败。
 *
 * splitToolError 是个纯函数：吃一段已经 JSON.parse 过的 result（或裸字符串），
 * 返回 { body: string, hint: string | null, examples: unknown[] | null }。
 * 测试在 vitest node 环境下跑（不需要 DOM）。
 */
import { describe, it, expect } from "vitest";

import { splitToolError } from "./MessageBubble";

describe("splitToolError", () => {
  it("plain text result → no hint", () => {
    const out = splitToolError("just a plain string");
    expect(out.hint).toBeNull();
    expect(out.body).toBe("just a plain string");
    expect(out.examples).toBeNull();
  });

  it("JSON without hint → no hint, body is pretty-printed", () => {
    const raw = JSON.stringify({ ok: true, value: 42 });
    const out = splitToolError(raw);
    expect(out.hint).toBeNull();
    // body should be pretty-printed JSON
    expect(out.body).toContain('"ok": true');
    expect(out.body).toContain('"value": 42');
  });

  it("error result with hint extracts hint", () => {
    const raw = JSON.stringify({
      ok: false,
      error: "missing required parameter: command",
      hint: "command 字段必填，例如 ls -la",
      examples: [{ command: "ls -la", cwd: "." }],
    });
    const out = splitToolError(raw);
    expect(out.hint).toBe("command 字段必填，例如 ls -la");
    expect(out.examples).toEqual([{ command: "ls -la", cwd: "." }]);
    // body still readable JSON of the rest
    expect(out.body).toContain('"error"');
  });

  it("hint must be a non-empty string — empty hint treated as null", () => {
    const raw = JSON.stringify({ ok: false, error: "x", hint: "" });
    const out = splitToolError(raw);
    expect(out.hint).toBeNull();
  });

  it("hint of wrong type (not a string) → null", () => {
    const raw = JSON.stringify({ ok: false, error: "x", hint: 123 });
    const out = splitToolError(raw);
    expect(out.hint).toBeNull();
  });

  it("examples missing or wrong type → null", () => {
    const raw = JSON.stringify({ ok: false, error: "x", hint: "h" });
    const out = splitToolError(raw);
    expect(out.examples).toBeNull();
    const raw2 = JSON.stringify({
      ok: false,
      error: "x",
      hint: "h",
      examples: "not an array",
    });
    const out2 = splitToolError(raw2);
    expect(out2.examples).toBeNull();
  });

  it("malformed JSON falls through to plain body", () => {
    const out = splitToolError("{not json");
    expect(out.hint).toBeNull();
    expect(out.body).toBe("{not json");
  });

  it("hint with whitespace only counts as empty", () => {
    const raw = JSON.stringify({ ok: false, error: "x", hint: "   \n " });
    const out = splitToolError(raw);
    expect(out.hint).toBeNull();
  });
});
