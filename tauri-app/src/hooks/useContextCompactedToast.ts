// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// WI-1B-2 压缩可观测 — 监听 ControlChannel 上的 `context_compacted` 消息,
// 浮一条 toast「已压缩,省 N token」(N = tokens_in - tokens_out)。
//
// 后端仅在 features.ctx_observability flag ON 时才发此消息(flag OFF = 字节级
// BC,后端根本不发),所以前端无需额外门控 —— 收到就显示。
//
// 仿 useBudgetToast: 订阅 ControlChannel.onMessage(非裸 WebSocket),自动跨
// 重连恢复。
import { useEffect } from "react";
import type { ControlChannel } from "../ws/ControlChannel";

export type ContextCompactedToastFn = (msg: string) => void;

export function useContextCompactedToast(
  getChannel: () => ControlChannel | null,
  showToast: ContextCompactedToastFn,
) {
  useEffect(() => {
    const channel = getChannel();
    if (!channel) return;
    const unsubscribe = channel.onMessage((msg) => {
      if (msg.type !== "context_compacted") return;
      const tokensIn = Number(msg.payload?.tokens_in ?? 0);
      const tokensOut = Number(msg.payload?.tokens_out ?? 0);
      const saved = Math.max(0, tokensIn - tokensOut);
      showToast(`已压缩，省 ${saved} token`);
    });
    return () => {
      unsubscribe();
    };
  }, [getChannel, showToast]);
}
