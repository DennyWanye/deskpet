// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Phase 1.1.6（context-1m-rearch）— ModelContextCard ws 线格式 + 编辑解析。
 *
 * vitest env 是 node（见 vitest.config.ts），按项目既有约定（同
 * SettingsProviders.test.tsx）只测纯函数 / ws message builder，不 mount
 * React 树。
 *
 * 覆盖：
 *  - model_context_get / model_context_set 线格式与后端 p4_ipc 契约对齐
 *  - parseModelContextEdits 白名单 + 范围校验（window>0 / compact 0–1）
 */
import { describe, it, expect } from "vitest";

import {
  buildModelContextGetMessage,
  buildModelContextSetMessage,
  parseModelContextEdits,
} from "./ModelContextCard";

describe("ModelContextCard — ws message builders", () => {
  it("test_get_message_shape — model_context_get 携带 model", () => {
    const m = buildModelContextGetMessage("deepseek-v4-pro");
    expect(m).toEqual({
      type: "model_context_get",
      payload: { model: "deepseek-v4-pro" },
    });
  });

  it("test_set_message_global_scope — 不带 project_root", () => {
    const m = buildModelContextSetMessage("global", "deepseek-v4-pro", {
      context_window: 750000,
    });
    expect(m).toEqual({
      type: "model_context_set",
      payload: {
        scope: "global",
        model: "deepseek-v4-pro",
        fields: { context_window: 750000 },
      },
    });
    expect("project_root" in m.payload).toBe(false);
  });

  it("test_set_message_project_scope — 携带 project_root", () => {
    const m = buildModelContextSetMessage(
      "project",
      "deepseek-v4-pro",
      { context_window: 1000000 },
      "G:/proj",
    );
    expect(m.payload).toEqual({
      scope: "project",
      model: "deepseek-v4-pro",
      fields: { context_window: 1000000 },
      project_root: "G:/proj",
    });
  });
});

describe("ModelContextCard — parseModelContextEdits", () => {
  it("test_valid_window_and_compact", () => {
    expect(parseModelContextEdits("800000", "0.75")).toEqual({
      context_window: 800000,
      compact_at_pct: 0.75,
    });
  });

  it("test_window_rounded_compact_in_range", () => {
    expect(parseModelContextEdits("800000.7", "0.5")).toEqual({
      context_window: 800001,
      compact_at_pct: 0.5,
    });
  });

  it("test_rejects_nonpositive_window", () => {
    // window<=0 被丢弃，只剩 compact
    expect(parseModelContextEdits("0", "0.8")).toEqual({
      compact_at_pct: 0.8,
    });
    expect(parseModelContextEdits("-5", "0.8")).toEqual({
      compact_at_pct: 0.8,
    });
  });

  it("test_rejects_compact_out_of_0_1", () => {
    expect(parseModelContextEdits("500000", "1.5")).toEqual({
      context_window: 500000,
    });
    expect(parseModelContextEdits("500000", "0")).toEqual({
      context_window: 500000,
    });
  });

  it("test_no_valid_edits_returns_null", () => {
    expect(parseModelContextEdits("abc", "xyz")).toBeNull();
    expect(parseModelContextEdits("", "")).toBeNull();
    expect(parseModelContextEdits("-1", "2")).toBeNull();
  });
});
