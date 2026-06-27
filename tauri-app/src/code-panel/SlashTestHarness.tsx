// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-T2-B v2 真 UI 验证 harness — `#/slashtest` route.
 *
 * 独立渲染 InputBar，让真浏览器 E2E（claude-in-chrome）能真触发 / 命令
 * autocomplete 状态机（dropdown / ↑↓ / Tab / arg-hint / history），无需
 * Tauri invoke 创 session。仅 dev 验证用，不进生产 bundle 路径。
 *
 * sessionId="slashtest" — sessionsStore 里临时塞一个 fake session 让
 * InputBar 的 send 路径不崩（虽然真测只点 dropdown 不真 send）。
 */
import { useEffect } from "react";

import { useSessionsStore } from "../stores/sessionsStore";
import { InputBar } from "./InputBar";

export function SlashTestHarness() {
  useEffect(() => {
    // 塞 fake session 让 InputBar 有 active_sid
    const store = useSessionsStore.getState();
    store.upsert("slashtest", {
      status: "idle",
      inflight: false,
    });
    // active_sid 设为 slashtest（如果 store 支持）
    try {
      (store as unknown as { set_active_sid?: (s: string) => void }).set_active_sid?.(
        "slashtest",
      );
    } catch {
      /* no-op */
    }
  }, []);

  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        background: "#0f1218",
      }}
      data-testid="slash-test-harness"
    >
      <div style={{ padding: 12, color: "#94a3b8", fontSize: 12 }}>
        Slash command UI 真测 harness — 输入 / 触发命令补全
      </div>
      <InputBar sessionId="slashtest" />
    </div>
  );
}
