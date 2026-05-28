// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * relayConfig — relay-edition constants (WI-R4 / WI-R2).
 *
 *归属：relay 闭源资产。OSS / manual edition 不引用本文件。分仓时随
 * `RelayAuthAdapter` 一并移入闭源仓。
 */

/** 中转站充值页 —— "去充值" 按钮跳转目标（外部浏览器打开）。 */
export const RECHARGE_URL = "https://your-llm-relay.example.com/console/billing";

/** 中转站多设备管理控制台。 */
export const DEVICE_CONSOLE_URL = "https://your-llm-relay.example.com/console/devices";

/**
 * 内测默认首选模型别名。`relayProviderBridge` 在 provider 的 models 列表里
 * 优先挑这个；挑不到则回退到 models[0]。与 config.toml 默认值一致。
 */
export const PREFERRED_MODEL = "gpt-5.5";
