// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S2: tests for splitThinkBlocks — the parser that handles
 * <think>...</think> chain-of-thought leaks in visible content.
 *
 * Re-exported from MessageBubble so we can test in isolation without
 * dragging in React. Make sure the export stays in sync; if you delete
 * splitThinkBlocks from MessageBubble, delete this file too.
 */
import { describe, it, expect } from "vitest";

import { splitThinkBlocks } from "./MessageBubble";

describe("splitThinkBlocks", () => {
  it("returns single normal segment for plain text", () => {
    expect(splitThinkBlocks("hello world")).toEqual([
      { kind: "normal", text: "hello world" },
    ]);
  });

  it("returns empty array for empty string", () => {
    expect(splitThinkBlocks("")).toEqual([]);
  });

  it("splits well-formed think + visible", () => {
    const out = splitThinkBlocks("<think>thinking…</think>here is the answer");
    expect(out).toEqual([
      { kind: "think", text: "thinking…", closed: true },
      { kind: "normal", text: "here is the answer" },
    ]);
  });

  it("preserves text before <think>", () => {
    const out = splitThinkBlocks("preamble <think>foo</think> tail");
    expect(out).toEqual([
      { kind: "normal", text: "preamble " },
      { kind: "think", text: "foo", closed: true },
      { kind: "normal", text: " tail" },
    ]);
  });

  it("treats unclosed <think> as still-streaming reasoning", () => {
    const out = splitThinkBlocks("<think>Still empty! But it worked once. Maybe the issue");
    expect(out).toEqual([
      { kind: "think", text: "Still empty! But it worked once. Maybe the issue", closed: false },
    ]);
  });

  it("handles unclosed think after visible content", () => {
    const out = splitThinkBlocks("here we go <think>and now thinking…");
    expect(out).toEqual([
      { kind: "normal", text: "here we go " },
      { kind: "think", text: "and now thinking…", closed: false },
    ]);
  });

  it("handles multiple think blocks", () => {
    const out = splitThinkBlocks(
      "<think>one</think>between<think>two</think>after",
    );
    expect(out).toEqual([
      { kind: "think", text: "one", closed: true },
      { kind: "normal", text: "between" },
      { kind: "think", text: "two", closed: true },
      { kind: "normal", text: "after" },
    ]);
  });

  it("regression: the actual bug case from 2026-05-10 deepseek-v4-pro", () => {
    // Exact shape we observed: model dumped its reasoning into visible
    // content, prefixed with <think>, never closed it, then end_turn'd.
    // The frontend must NOT show a streaming cursor in the main bubble
    // — the only "active" thing is the open think.
    const out = splitThinkBlocks(
      "<think>Still empty! But it worked once. Maybe the issue is that Go code contains special XML characters like &, <, > that need to be escaped or CDATA-wrapped. But the path shouldn't have those…\nWait, let me look at the successful call again:",
    );
    expect(out.length).toBe(1);
    expect(out[0].kind).toBe("think");
    if (out[0].kind === "think") {
      expect(out[0].closed).toBe(false);
    }
  });
});
