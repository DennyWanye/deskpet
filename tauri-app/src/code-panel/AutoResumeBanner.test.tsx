// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S2 Phase 5.2 — AutoResumeBanner 行为测试。
 *
 * 实测无 DOM 环境（vitest node），所以只测：
 *   1. 纯函数 buildAutoResumeBannerText(attempts, max) — 决定 banner 显示文案
 *   2. ws.ts dispatch 三个新事件后 sessionsStore 的状态：
 *      - auto_resume_started → auto_resume_attempts = N
 *      - auto_resume_succeeded → auto_resume_attempts = 0
 *      - auto_resume_exhausted → auto_resume_attempts = 0 + 推一条 error message
 *
 * 这两个一起就保证了 banner 组件 + 数据流的正确：
 * banner 本身只是把 store 字段映射成 JSX，纯函数 + store 是行为根因。
 */
import { describe, it, expect, beforeEach } from "vitest";

import {
  buildAutoResumeBannerText,
  shouldShowAutoResumeBanner,
} from "./AutoResumeBanner";
import { __test_dispatch } from "./ws";
import { useSessionsStore } from "../stores/sessionsStore";

describe("buildAutoResumeBannerText", () => {
  it("returns default banner text for first attempt", () => {
    expect(buildAutoResumeBannerText(1, 2)).toBe("🔄 agent 自愈中... (尝试 1/2)");
  });

  it("returns banner text for last attempt", () => {
    expect(buildAutoResumeBannerText(2, 2)).toBe("🔄 agent 自愈中... (尝试 2/2)");
  });

  it("handles attempts > max gracefully (still labels them)", () => {
    expect(buildAutoResumeBannerText(3, 2)).toBe("🔄 agent 自愈中... (尝试 3/2)");
  });
});

describe("shouldShowAutoResumeBanner", () => {
  it("attempts > 0 + inflight true → show", () => {
    expect(shouldShowAutoResumeBanner(1, true)).toBe(true);
    expect(shouldShowAutoResumeBanner(2, true)).toBe(true);
  });

  it("attempts = 0 → hide regardless of inflight", () => {
    expect(shouldShowAutoResumeBanner(0, true)).toBe(false);
    expect(shouldShowAutoResumeBanner(0, false)).toBe(false);
  });

  it("inflight false → hide even if attempts > 0", () => {
    // After auto_resume_succeeded backend sets inflight=false but attempts may
    // not have reset yet in same render cycle; we hide either way.
    expect(shouldShowAutoResumeBanner(1, false)).toBe(false);
  });
});

// ---- ws dispatch integration ------------------------------------------------

function resetStore() {
  // Reset zustand store state to a clean default for each test
  useSessionsStore.setState((s) => ({
    ...s,
    sessions: { default: { ...s.sessions.default, auto_resume_attempts: 0, inflight: false, status: "idle" as const, messages: [] } },
    active_sid: "default",
  }));
}

describe("ws.dispatch auto-resume events", () => {
  beforeEach(() => {
    resetStore();
  });

  it("auto_resume_started bumps auto_resume_attempts on the named session", () => {
    // ensure session exists
    useSessionsStore.getState().ensure("s1");
    __test_dispatch({
      type: "auto_resume_started",
      payload: { session_id: "s1", attempt: 1, hint_preview: "the hint" },
    });
    const sess = useSessionsStore.getState().sessions["s1"];
    expect(sess?.auto_resume_attempts).toBe(1);
    expect(sess?.inflight).toBe(true);
  });

  it("auto_resume_started followed by attempt 2 increments attempts", () => {
    useSessionsStore.getState().ensure("s2");
    __test_dispatch({
      type: "auto_resume_started",
      payload: { session_id: "s2", attempt: 1, hint_preview: "h" },
    });
    __test_dispatch({
      type: "auto_resume_started",
      payload: { session_id: "s2", attempt: 2, hint_preview: "h2" },
    });
    expect(useSessionsStore.getState().sessions["s2"]?.auto_resume_attempts).toBe(2);
  });

  it("auto_resume_succeeded resets auto_resume_attempts to 0", () => {
    useSessionsStore.getState().ensure("s3");
    __test_dispatch({
      type: "auto_resume_started",
      payload: { session_id: "s3", attempt: 1, hint_preview: "h" },
    });
    expect(useSessionsStore.getState().sessions["s3"]?.auto_resume_attempts).toBe(1);
    __test_dispatch({
      type: "auto_resume_succeeded",
      payload: { session_id: "s3" },
    });
    const sess = useSessionsStore.getState().sessions["s3"];
    expect(sess?.auto_resume_attempts).toBe(0);
  });

  it("auto_resume_exhausted resets attempts and pushes an error message", () => {
    useSessionsStore.getState().ensure("s4");
    __test_dispatch({
      type: "auto_resume_started",
      payload: { session_id: "s4", attempt: 1, hint_preview: "h" },
    });
    __test_dispatch({
      type: "auto_resume_started",
      payload: { session_id: "s4", attempt: 2, hint_preview: "h" },
    });
    __test_dispatch({
      type: "auto_resume_exhausted",
      payload: {
        session_id: "s4",
        final_error: "permanent_tool_error",
        attempts: 2,
      },
    });
    const sess = useSessionsStore.getState().sessions["s4"];
    expect(sess?.auto_resume_attempts).toBe(0);
    expect(sess?.status).toBe("error");
    expect(sess?.inflight).toBe(false);
    // an error message should be visible to user
    const errs = (sess?.messages ?? []).filter((m) => m.role === "error");
    expect(errs.length).toBeGreaterThan(0);
    expect(errs[errs.length - 1].text).toContain("permanent_tool_error");
  });
});
