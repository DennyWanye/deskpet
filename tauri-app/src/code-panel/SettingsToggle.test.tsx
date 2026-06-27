// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S2 Phase 5.3 — auto-resume settings toggle 序列化测试。
 *
 * Toggle 改变时要发的 ws message 形状被 spec 锁死：
 *   { type: "settings_update",
 *     payload: { supervisor: { auto_resume_enabled: bool } } }
 *
 * 我们抽出纯函数 buildAutoResumeSettingsMessage 让 SettingsPanel.tsx 复用，
 * 测试只验证序列化对，不验证 React 组件细节（无 DOM 环境）。
 */
import { describe, it, expect } from "vitest";

import { buildAutoResumeSettingsMessage } from "../components/SettingsPanel";

describe("buildAutoResumeSettingsMessage", () => {
  it("ON → settings_update with auto_resume_enabled=true under supervisor namespace", () => {
    expect(buildAutoResumeSettingsMessage(true)).toEqual({
      type: "settings_update",
      payload: { supervisor: { auto_resume_enabled: true } },
    });
  });

  it("OFF → settings_update with auto_resume_enabled=false", () => {
    expect(buildAutoResumeSettingsMessage(false)).toEqual({
      type: "settings_update",
      payload: { supervisor: { auto_resume_enabled: false } },
    });
  });
});
