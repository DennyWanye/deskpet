// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Translate backend chat_v2_error relay classes into user-facing text.
 */
import { RECHARGE_URL } from "./relayConfig";

export interface ChatErrorPayload {
  reason?: string;
  detail?: string;
  error?: string;
  error_class?: string;
}

export const INSUFFICIENT_BALANCE_TEXT =
  `账号余额不足，请充值：${RECHARGE_URL}`;

export const RELAY_KEY_INVALID_TEXT =
  "连接凭证已失效，正在尝试自动重新配置；若持续失败，请退出登录后重新登录。";

export function friendlyChatErrorMessage(payload: ChatErrorPayload): string {
  switch (payload.error_class) {
    case "insufficient_balance":
      return INSUFFICIENT_BALANCE_TEXT;
    case "relay_key_invalid":
      return RELAY_KEY_INVALID_TEXT;
    default: {
      const parts = [payload.error, payload.detail, payload.reason].filter(
        Boolean,
      );
      return parts.length > 0 ? parts.join(" — ") : "unknown";
    }
  }
}
