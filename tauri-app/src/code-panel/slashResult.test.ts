// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * FEAT-A2 — slash_command_result 渲染分支测试。
 *
 * 此前 ws.ts 无 `slash_command_result` case → 所有 /help /goal /prefs 结果被
 * 静默丢弃。本测试把"分支漏写"从 windows-mcp 阶段提前到代码阶段拦截：
 * tsc 抓不到"缺 case 静默丢"，故这里硬断言 `format_slash_result` 对 8 种
 * result.type **每种都返回非空文本**，且关键字段被渲染进去；外加 default
 * 兜底（未知 type 也不能产空串）。
 *
 * （ws.ts `case "slash_command_result"` 直接 push 这个返回值作为 bubble text，
 * 故非空文本 == push 了非空 message。）
 */
import { describe, it, expect } from "vitest";

import { format_slash_result } from "./ws";

const EIGHT_TYPES = [
  "help",
  "goal_set",
  "goal_status",
  "goal_cleared",
  "skill_result",
  "prefs_list",
  "prefs_cleared",
  "error",
] as const;

// 每种 type 一个有代表性的 sample（对齐后端契约）。
const SAMPLES: Record<string, any> = {
  help: {
    type: "help",
    builtins: [{ name: "prefs", description: "查看偏好记忆" }],
    skills: [{ name: "ppt-generate", description: "生成 PPT" }],
  },
  goal_set: { type: "goal_set", text: "把登录页做完", max_iterations: 20 },
  goal_status: {
    type: "goal_status",
    active: true,
    text: "重构后端",
    iterations_used: 3,
    max_iterations: 20,
    done: false,
  },
  goal_cleared: { type: "goal_cleared", ok: true },
  skill_result: { type: "skill_result", skill: "ppt-generate", output: "done" },
  prefs_list: {
    type: "prefs_list",
    count: 2,
    entries: [
      { text: "你用什么模型", label: "ask", kind: "intent", ts: 1 },
      { text: "生成 PPT", label: "approved", kind: "plan", ts: 2 },
    ],
  },
  prefs_cleared: { type: "prefs_cleared", removed: 3, kind: "intent" },
  error: { type: "error", message: "偏好记忆未启用 (features.preference_memory)" },
};

describe("format_slash_result — 8 种 type 全覆盖", () => {
  for (const t of EIGHT_TYPES) {
    it(`type=${t} → push 非空 message`, () => {
      const out = format_slash_result(SAMPLES[t]);
      expect(typeof out).toBe("string");
      expect(out.trim().length).toBeGreaterThan(0);
    });
  }

  it("help 渲染了 builtin + skill 名", () => {
    const out = format_slash_result(SAMPLES.help);
    expect(out).toContain("prefs");
    expect(out).toContain("ppt-generate");
  });

  it("prefs_list 逐条 {kind}/{label}: {text} + 共 N 条", () => {
    const out = format_slash_result(SAMPLES.prefs_list);
    expect(out).toContain("intent/ask: 你用什么模型");
    expect(out).toContain("plan/approved: 生成 PPT");
    expect(out).toContain("共 2 条");
  });

  it("prefs_list 空 → 提示为空（非空串）", () => {
    const out = format_slash_result({ type: "prefs_list", count: 0, entries: [] });
    expect(out.trim().length).toBeGreaterThan(0);
    expect(out).toContain("空");
  });

  it("prefs_cleared 渲染条数 + kind", () => {
    const out = format_slash_result(SAMPLES.prefs_cleared);
    expect(out).toContain("3");
    expect(out).toContain("intent");
  });

  it("error 渲染 message", () => {
    const out = format_slash_result(SAMPLES.error);
    expect(out).toContain("features.preference_memory");
  });

  it("default 兜底：未知 type 也产非空 message（禁止静默丢）", () => {
    const out = format_slash_result({ type: "totally_unknown_xyz", foo: 1 });
    expect(out.trim().length).toBeGreaterThan(0);
    expect(out).toContain("totally_unknown_xyz");
  });

  it("null/undefined result 也不崩、产非空兜底", () => {
    expect(format_slash_result(null).trim().length).toBeGreaterThan(0);
    expect(format_slash_result(undefined).trim().length).toBeGreaterThan(0);
  });
});
