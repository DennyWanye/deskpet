// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * NullAuthAdapter — 完全匿名，不认账户。
 *
 * 用途：
 *  - OSS 启动还没决定走 Manual 还是 Relay 时的默认占位
 *  - 测试 / E2E 里需要"完全跳过 auth"时
 *
 * 行为：
 *  - login/register → throws NotSupportedError (这个 adapter 没有账户)
 *  - listProviders → []
 *  - getUsage → null
 *  - currentUser → null
 *
 * 调用方决定怎么处理 NotSupportedError —— 一般是 UI 隐藏登录入口、
 * 仅展示"手填 provider"路径或引导切换到 ManualAuthAdapter。
 */
import {
  type AuthAdapter,
  type AuthEvent,
  type LoginCredentials,
  type Provider,
  type RegisterCredentials,
  type UsageSummary,
  type User,
  NotSupportedError,
} from "./types";

export class NullAuthAdapter implements AuthAdapter {
  readonly id = "null";
  readonly displayName = "匿名（无账户）";

  private listeners = new Set<(e: AuthEvent) => void>();

  isAuthenticated(): boolean {
    return false;
  }

  currentUser(): User | null {
    return null;
  }

  async login(_credentials: LoginCredentials): Promise<User> {
    throw new NotSupportedError("login", this.id);
  }

  async register(_credentials: RegisterCredentials): Promise<User> {
    throw new NotSupportedError("register", this.id);
  }

  async logout(): Promise<void> {
    /* no-op — there is no session to clear */
  }

  async listProviders(): Promise<Provider[]> {
    return [];
  }

  async getUsage(): Promise<UsageSummary | null> {
    return null;
  }

  onEvent(handler: (e: AuthEvent) => void): () => void {
    this.listeners.add(handler);
    return () => {
      this.listeners.delete(handler);
    };
  }
}
