// P2-1-S8 — Watch ControlChannel messages for budget_exceeded flags and
// surface a toast-style notification.
//
// We subscribe via ControlChannel.onMessage (not a raw WebSocket
// addEventListener) so we stay agnostic of the underlying socket and
// automatically recover across reconnects.
import { useEffect } from "react";
import type { ControlChannel } from "../ws/ControlChannel";

export type BudgetToastFn = (msg: string) => void;

export function useBudgetToast(
  getChannel: () => ControlChannel | null,
  showToast: BudgetToastFn,
) {
  useEffect(() => {
    const channel = getChannel();
    if (!channel) return;
    const unsubscribe = channel.onMessage((msg) => {
      if (
        msg.type === "chat_response" &&
        msg.payload?.budget_exceeded === true
      ) {
        const reason = msg.payload.budget_reason
          ? `（${msg.payload.budget_reason}）`
          : "";
        showToast(`今日云端预算已用尽，已降级到本地模型。${reason}`);
      }
    });
    return () => {
      unsubscribe();
    };
  }, [getChannel, showToast]);
}
