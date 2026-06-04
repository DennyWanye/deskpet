// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-T2-B v2 — Slash command UI vitest.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

import { _testing } from "../InputBar";
import { SlashDropdown, type SlashCommand } from "../SlashDropdown";
import { ArgHintBar } from "../ArgHintBar";

// Auto cleanup（替代 @testing-library/jest-dom/vitest 自动 cleanup）
afterEach(() => cleanup());

// ─── _testing helpers ─────────────────────────────

describe("filterCommands", () => {
  const all: SlashCommand[] = [
    { name: "help", description: "help" },
    { name: "goal", description: "goal" },
    { name: "ppt-generate", description: "ppt" },
    { name: "deep-research", description: "research" },
  ];

  it("empty query returns all", () => {
    expect(_testing.filterCommands(all, "")).toEqual(all);
  });

  it("prefix match wins", () => {
    const r = _testing.filterCommands(all, "p");
    expect(r[0].name).toBe("ppt-generate");
  });

  it("substring match included after prefix", () => {
    const r = _testing.filterCommands(all, "ear");
    expect(r.map((c) => c.name)).toContain("deep-research");
  });

  it("no match returns empty", () => {
    expect(_testing.filterCommands(all, "xyz")).toEqual([]);
  });

  it("case insensitive", () => {
    const r = _testing.filterCommands(all, "HELP");
    expect(r[0].name).toBe("help");
  });
});

describe("pushHistory", () => {
  beforeEach(() => _testing.clearHistory());

  it("ignores non-slash entries", () => {
    _testing.pushHistory("hello world");
    expect(_testing.getHistory()).toEqual([]);
  });

  it("stores / entries", () => {
    _testing.pushHistory("/help");
    expect(_testing.getHistory()).toEqual(["/help"]);
  });

  it("dedupes consecutive same entry", () => {
    _testing.pushHistory("/help");
    _testing.pushHistory("/help");
    expect(_testing.getHistory()).toEqual(["/help"]);
  });

  it("non-consecutive same entry allowed", () => {
    _testing.pushHistory("/help");
    _testing.pushHistory("/goal x");
    _testing.pushHistory("/help");
    expect(_testing.getHistory()).toEqual(["/help", "/goal x", "/help"]);
  });

  it("caps at HISTORY_MAX (50)", () => {
    for (let i = 0; i < 60; i++) _testing.pushHistory(`/cmd${i}`);
    expect(_testing.getHistory()).toHaveLength(_testing.HISTORY_MAX);
    expect(_testing.getHistory()[0]).toBe("/cmd10");
  });
});

// ─── SlashDropdown rendering ────────────────────────

describe("SlashDropdown", () => {
  const cmds: SlashCommand[] = [
    { name: "help", description: "show help" },
    {
      name: "goal",
      description: "set goal",
      args_schema: [
        { name: "text", type: "string", description: "", required: false },
      ],
    },
  ];

  it("renders nothing when empty", () => {
    const { container } = render(
      <SlashDropdown candidates={[]} selectedIdx={0} onAccept={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders candidates with names + descriptions", () => {
    render(
      <SlashDropdown candidates={cmds} selectedIdx={0} onAccept={() => {}} />,
    );
    expect(screen.queryByTestId("slash-dropdown")).not.toBeNull();
    expect(screen.queryByText(/show help/)).not.toBeNull();
    expect(screen.queryByText(/set goal/)).not.toBeNull();
  });

  it("highlights selectedIdx", () => {
    render(
      <SlashDropdown candidates={cmds} selectedIdx={1} onAccept={() => {}} />,
    );
    const goalRow = screen.getByTestId("slash-item-goal");
    expect(goalRow.getAttribute("aria-selected")).toBe("true");
    const helpRow = screen.getByTestId("slash-item-help");
    expect(helpRow.getAttribute("aria-selected")).toBe("false");
  });

  it("calls onAccept on mousedown", () => {
    const onAccept = vi.fn();
    render(
      <SlashDropdown candidates={cmds} selectedIdx={0} onAccept={onAccept} />,
    );
    fireEvent.mouseDown(screen.getByTestId("slash-item-goal"));
    expect(onAccept).toHaveBeenCalledWith(1);
  });

  it("renders arg schema hint inline", () => {
    render(
      <SlashDropdown candidates={cmds} selectedIdx={0} onAccept={() => {}} />,
    );
    expect(screen.queryByText(/\[text\]/)).not.toBeNull();
  });
});

// ─── ArgHintBar rendering ──────────────────────────

describe("ArgHintBar", () => {
  it("renders nothing for empty schema", () => {
    const { container } = render(
      <ArgHintBar commandName="help" argSchema={[]} currentArgIndex={0} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows command name + all args", () => {
    render(
      <ArgHintBar
        commandName="copy"
        argSchema={[
          { name: "src", type: "path", description: "源", required: true },
          { name: "dst", type: "path", description: "目标", required: true },
        ]}
        currentArgIndex={0}
      />,
    );
    expect(screen.queryByText("/copy")).not.toBeNull();
    expect(screen.queryByText("<src>")).not.toBeNull();
    expect(screen.queryByText("<dst>")).not.toBeNull();
  });

  it("strikes through filled args + bolds current", () => {
    render(
      <ArgHintBar
        commandName="copy"
        argSchema={[
          { name: "src", type: "path", description: "", required: true },
          { name: "dst", type: "path", description: "", required: true },
        ]}
        currentArgIndex={1}
      />,
    );
    const src = screen.getByTestId("arg-src");
    const dst = screen.getByTestId("arg-dst");
    expect(src.style.textDecoration).toBe("line-through");
    expect(dst.style.fontWeight).toBe("600");
  });

  it("optional args show with brackets", () => {
    render(
      <ArgHintBar
        commandName="goal"
        argSchema={[
          { name: "text", type: "string", description: "", required: false },
        ]}
        currentArgIndex={0}
      />,
    );
    expect(screen.queryByText("[text]")).not.toBeNull();
  });
});
