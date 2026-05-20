/**
 * AuthAdapter — 用户级账户体系的抽象层。
 *
 * 设计目的：让 OSS 主干（匿名 / 手填 provider）和未来的付费版
 * （登录中转站自动配置 provider）共用一个接口，付费版无需 fork OSS
 * 即可加一个第三方 RelayAuthAdapter 实现。
 *
 * 三个实现：
 *  - NullAuthAdapter    匿名，永不"登录"，listProviders() 返回 []
 *  - ManualAuthAdapter  OSS 主路径，用户在 Settings 自填 provider
 *  - RelayAuthAdapter   付费版闭源实现，login → fetch /v1/providers
 *
 * 当前 commit 仅定义接口 + Null/Manual，未接入既有组件。
 * 既有 LLMProviderRegistry / Settings 流程完全不变 —— 零回归。
 */

export interface User {
  /** Stable account identifier (server-issued, e.g. "cmpd..."). */
  id: string;
  email: string;
  /** Optional alias; some backends carry one, some don't. */
  username?: string;
  /** Optional role tag from the server (e.g. "USER", "ADMIN"). */
  role?: string;
  /** Account plan tier ("free" | "pro" | "prepaid" | ...). */
  plan?: string;
  /** ISO 8601 created-at, for "member since" UI. */
  created_at?: string;
}

/** Single LLM model entry inside a provider. */
export interface ProviderModel {
  /** Model alias the provider's `/chat/completions` accepts as `model`. */
  id: string;
  context_window?: number;
  /** Capability flags. Known values (extensible):
   *  chat / streaming / tools / vision / thinking /
   *  reasoning_effort / embeddings / image_generation / video_generation
   */
  capabilities?: string[];
  /** USD-denominated pricing (minor unit = cents per 1M tokens). */
  pricing?: {
    inputPer1MMinor?: number;
    outputPer1MMinor?: number;
  };
  /** v1.1+ CN¥ pricing, pre-converted server-side via `source_rate`. UI
   *  should prefer this over `pricing` when present; falls back to the
   *  USD figure only for legacy responses that haven't been re-serialised
   *  since the v1.1 schema bump. */
  pricing_cny?: {
    inputPer1MMinor?: number;
    outputPer1MMinor?: number;
    currency?: string;
    source_rate?: number;
  };
}

/** A single LLM endpoint the user can call. */
export interface Provider {
  id: string;
  /** Display name shown in UI (e.g. "OpenAI (relay)"). */
  name: string;
  /** OpenAI-compatible base URL — POST /chat/completions on it. */
  base_url: string;
  /** Bearer key for this provider; null/empty = user must fill it. */
  api_key?: string | null;
  models: ProviderModel[];
  openai_compatible: boolean;
  supports_streaming: boolean;
  /** Lower = higher priority during chain walking. */
  priority?: number;
}

/** Account usage summary, mirrors `/v1/usage/summary`. */
export interface UsageSummary {
  plan?: string;
  balance?: { amount_minor: number; currency: string };
  period?: { used_minor: number; unit: string; reset_at: string };
  rate_limit?: { rpm: number | null; tpm: number | null };
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterCredentials {
  email: string;
  password: string;
}

/** Auth lifecycle events the UI may want to react to. */
export type AuthEvent =
  | { type: "login"; user: User }
  | { type: "logout" }
  | { type: "tokens-refreshed" }
  | { type: "providers-updated"; providers: Provider[] }
  | { type: "usage-updated"; usage: UsageSummary };

/**
 * Thrown when an adapter genuinely doesn't support an operation
 * (e.g. NullAuthAdapter.login() — there is no account model).
 * Callers should fall back to a sensible no-op or feature-flag the UI.
 */
export class NotSupportedError extends Error {
  constructor(operation: string, adapter: string) {
    super(`Operation '${operation}' is not supported by adapter '${adapter}'`);
    this.name = "NotSupportedError";
  }
}

/**
 * Core contract every concrete adapter implements.
 *
 * Adapters MUST be:
 *  - side-effect-free at construction (no network on `new X()`)
 *  - tolerant of being called when not authenticated (return null / [])
 *  - synchronous for query methods (isAuthenticated / currentUser);
 *    async only for actual network / IPC calls.
 */
export interface AuthAdapter {
  /** Stable adapter identifier ("null" | "manual" | "relay"). */
  readonly id: string;
  /** Human-readable adapter name for logs / debug UI. */
  readonly displayName: string;

  /** Has a logged-in user right now. */
  isAuthenticated(): boolean;

  /** Current user info, or null if not authenticated. */
  currentUser(): User | null;

  /** Login flow. NullAuthAdapter throws NotSupportedError. */
  login(credentials: LoginCredentials): Promise<User>;

  /** Registration flow. NullAuthAdapter throws NotSupportedError. */
  register(credentials: RegisterCredentials): Promise<User>;

  /** Logout. Always safe to call (no-op when not logged in). */
  logout(): Promise<void>;

  /** Fetch the provider list available to the current account.
   *  Null returns []; Manual returns []; Relay fetches /v1/providers. */
  listProviders(): Promise<Provider[]>;

  /** Account usage / balance.
   *  Null returns null; Manual returns null; Relay fetches /v1/usage. */
  getUsage(): Promise<UsageSummary | null>;

  /** Subscribe to lifecycle events. Returns an unsubscribe fn. */
  onEvent(handler: (e: AuthEvent) => void): () => void;
}

/** Build-time edition selector — paid build sets this to "relay". */
export type AuthEdition = "null" | "manual" | "relay";
