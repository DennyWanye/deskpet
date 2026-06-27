// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * auth/ — 用户级账户体系入口。
 *
 * 现状：本次 commit 仅做 scaffold。OSS 主干代码未 wire 进来。
 * 既有的 sessionsStore / 后端 ProviderRegistry / Settings 面板等
 * provider 管理路径**保持原样**。等 Relay adapter 进闭源仓库 +
 * 调用方迁移到 adapter 入口时，再统一切换。
 *
 * 使用：
 *   import { getAuthAdapter } from "@/auth";
 *   const auth = getAuthAdapter();      // 当前 edition 决定的 adapter
 *   const user = auth.currentUser();
 *
 * 切换：构建期通过 Vite import.meta.env.VITE_AUTH_EDITION 决定。
 *   - "null"   → NullAuthAdapter
 *   - "manual" → ManualAuthAdapter (默认)
 *   - "relay"  → 闭源仓库提供，本 OSS 仓库不含
 */
export { NullAuthAdapter } from "./NullAuthAdapter";
export { ManualAuthAdapter } from "./ManualAuthAdapter";
export { RelayAuthAdapter } from "./RelayAuthAdapter";
export { RelayApiError, type RelayErrorCode } from "./RelayApiError";
export {
  type AuthAdapter,
  type AuthEdition,
  type AuthEvent,
  type LoginCredentials,
  type Provider,
  type ProviderModel,
  type RegisterCredentials,
  type UsageSummary,
  type User,
  NotSupportedError,
} from "./types";

import { type AuthAdapter, type AuthEdition } from "./types";
import { ManualAuthAdapter } from "./ManualAuthAdapter";
import { NullAuthAdapter } from "./NullAuthAdapter";
import { RelayAuthAdapter } from "./RelayAuthAdapter";

let _instance: AuthAdapter | null = null;

/** Singleton accessor — selects adapter based on build-time edition. */
export function getAuthAdapter(): AuthAdapter {
  if (_instance) return _instance;
  // Vite injects strings only; default to "manual" for OSS build.
  const edition: AuthEdition =
    ((import.meta as ImportMeta & { env?: Record<string, string> }).env
      ?.VITE_AUTH_EDITION as AuthEdition) ?? "manual";
  _instance = buildAdapter(edition);
  return _instance;
}

/** Construct a fresh adapter (escape hatch for tests). */
export function buildAdapter(edition: AuthEdition): AuthAdapter {
  switch (edition) {
    case "null":
      return new NullAuthAdapter();
    case "manual":
      return new ManualAuthAdapter();
    case "relay": {
      // W2: Relay 实现已并入主线（Week 2 of relay integration plan）。
      // 后续分仓时这一行会从 paid 仓库的 monkey-patch 接管；目前
      // OSS 仓也带，方便在主仓里写测试 + 联调。
      //
      // 2026-05-30 bug fix：RelayAuthAdapter 的 DEFAULT_BASE_URL 是 OSS
      // placeholder ("https://your-llm-relay.example.com")，dev/prod 必须
      // 用 VITE_RELAY_BASE_URL env 注入真实 URL。后端用 relay.example.com 但前端
      // 直接 new 拿到 placeholder → 登录直接 DNS 失败显示"网络连接失败"。
      const baseUrl = (import.meta as { env?: Record<string, string | undefined> })
        .env?.VITE_RELAY_BASE_URL;
      return baseUrl ? new RelayAuthAdapter({ baseUrl }) : new RelayAuthAdapter();
    }
    default: {
      const _exhaust: never = edition;
      throw new Error(`Unknown AuthEdition: ${String(_exhaust)}`);
    }
  }
}

/** Test-only — reset the singleton between vitest cases. */
export function _resetAuthAdapterForTests(): void {
  _instance = null;
}
