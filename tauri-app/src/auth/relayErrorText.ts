// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * relayErrorText — WI-R5 / T6-3：把后端 `chat_v2_error` 的结构化 relay
 * 错误码翻译成对用户友好的中文提示。
 *
 * 后端 `LLMProviderError.error_class`（见 `llm/relay_errors.py`）经
 * agent_loop `ErrorEvent.error_class` → main.py `chat_v2_error` payload
 * 一路传到前端。此处把它渲染成聊天/错误条上的人话:
 *   - insufficient_balance → "余额不足" + 去充值指引
 *   - relay_key_invalid    → "连接凭证失效" + 自动重连指引
 *   - 其它 → 退回原始 reason/detail 拼接
 *
 * 归属:relay 闭源资产。
 */
import { RECHARGE_URL } from "./relayConfig";

export interface ChatErrorPayload {
  reason?: string;
  detail?: string;
  error?: string;
  error_class?: string;
}

/** 余额不足的友好提示（含充值入口）。导出供测试与 UI 复用。 */
export const INSUFFICIENT_BALANCE_TEXT =
  `余额不足，已暂停回复。请点击左上角 👤 账户面板 → "去充值"，` +
  `或前往中转站充值页：${RECHARGE_URL}`;

/** key 失效的友好提示。 */
export const RELAY_KEY_INVALID_TEXT =
  "连接凭证已失效，正在尝试自动重新配置；若持续失败请退出登录后重新登录。";

/**
 * 把 `chat_v2_error` payload 翻成用户可读的一行提示。
 */
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
