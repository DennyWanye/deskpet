/**
 * ManualAuthAdapter — OSS 主路径，用户自填 provider。
 *
 * 用途：OSS 版默认 adapter。不联网鉴权，"账户"概念就是当前 OS 用户；
 * provider 列表由用户在 Settings 面板手动维护（继续走现有
 * LLMProviderRegistry / settings.json 流程，本 adapter 不重复实现）。
 *
 * 与 NullAuthAdapter 的差别：
 *  - Null 用于"完全跳过 auth"的场景（测试 / 启动占位）
 *  - Manual 用于"有用户但没有远端账户"的场景（OSS 自托管）
 *
 * 关键设计：listProviders() 不在这里返回真实 provider 列表。
 * provider 数据继续由现有 sessionsStore + 后端 ProviderRegistry 拥有；
 * Manual adapter 在这里返回 []，是"我没有远端 provider 源"的语义信号，
 * 调用方据此走"读本地配置"路径。
 *
 * 这样做的好处：本次 commit 不动现有 provider 链路，纯增量。
 * 等 Relay adapter 出来时再统一让调用方走 adapter 入口。
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

export class ManualAuthAdapter implements AuthAdapter {
  readonly id = "manual";
  readonly displayName = "自托管（手填 provider）";

  private listeners = new Set<(e: AuthEvent) => void>();

  isAuthenticated(): boolean {
    // OSS 模式没有远端账户概念，永远视为"已就绪"——本地用户即用户。
    return true;
  }

  currentUser(): User | null {
    // 没有远端账户，但返回一个本地占位符让 UI 有"用户名"可显示。
    return {
      id: "local",
      email: "local@deskpet",
      username: "local",
      plan: "self-hosted",
    };
  }

  async login(_credentials: LoginCredentials): Promise<User> {
    throw new NotSupportedError("login", this.id);
  }

  async register(_credentials: RegisterCredentials): Promise<User> {
    throw new NotSupportedError("register", this.id);
  }

  async logout(): Promise<void> {
    /* no-op — manual mode has no session token to revoke */
  }

  async listProviders(): Promise<Provider[]> {
    // Manual 不拥有 provider 数据源 —— 调用方应继续走现有
    // 后端 LLMProviderRegistry / Settings 面板路径。
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
