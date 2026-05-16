/**
 * Tests for petText — the companion pet-window display filter.
 * Covers the exact junk classes observed leaking into the pet bubble
 * on 2026-05-16 (tool-call commands, tool results, errors, <think>).
 */
import { describe, it, expect } from "vitest";
import { isTraceLine, stripThink, forPet } from "./petText";

describe("isTraceLine", () => {
  it("flags tool-call command lines", () => {
    expect(isTraceLine('🔧 调用 write_file({"path":"a.py"})')).toBe(true);
  });
  it("flags tool-result lines (✅/❌)", () => {
    expect(isTraceLine("✅ write_file 结果")).toBe(true);
    expect(isTraceLine("❌ run_shell 结果")).toBe(true);
  });
  it("flags error banner lines", () => {
    expect(isTraceLine("⚠ LLM HTTP 400 — llm_error")).toBe(true);
  });
  it("does NOT flag normal conversational text", () => {
    expect(isTraceLine("嗨嗨～我是你的桌面宠物 DeskPet")).toBe(false);
    expect(isTraceLine("好的，我帮你看一下结果如何")).toBe(false); // 含"结果"但非 trace
  });
});

describe("stripThink", () => {
  it("removes a closed think block, keeps the answer", () => {
    expect(stripThink("<think>user greets me</think>嗨嗨～你好!")).toBe(
      "嗨嗨～你好!",
    );
  });
  it("removes an unclosed (still-streaming) think block", () => {
    expect(stripThink("正文<think>still reasoning and no close")).toBe("正文");
  });
  it("returns empty for think-only content", () => {
    expect(stripThink("<think>only reasoning here</think>")).toBe("");
  });
  it("passes through plain text untouched", () => {
    expect(stripThink("我能帮你做的事情还挺多的")).toBe(
      "我能帮你做的事情还挺多的",
    );
  });
});

describe("forPet", () => {
  it("returns null for trace lines (tool calls hidden from pet)", () => {
    expect(forPet('🔧 调用 mcp_filesystem_create_directory({"path":"vpn-cli"})')).toBeNull();
    expect(forPet("✅ write_file 结果")).toBeNull();
    expect(forPet("⚠ context_budget_block")).toBeNull();
  });
  it("returns null for think-only content", () => {
    expect(forPet("<think>The user is greeting me</think>")).toBeNull();
  });
  it("returns clean text with think stripped (the 2026-05-16 leak)", () => {
    expect(
      forPet(
        "<think>The user is greeting me, asking what I can do.</think>嗨嗨～我是你的桌面宠物 DeskPet，专门陪你工作的小助手!",
      ),
    ).toBe("嗨嗨～我是你的桌面宠物 DeskPet，专门陪你工作的小助手!");
  });
  it("returns plain assistant text unchanged", () => {
    expect(forPet("我没有图像生成能力。你可以用外部工具…")).toBe(
      "我没有图像生成能力。你可以用外部工具…",
    );
  });
  it("returns null for empty / nullish input", () => {
    expect(forPet("")).toBeNull();
    expect(forPet(null)).toBeNull();
    expect(forPet(undefined)).toBeNull();
  });
});
