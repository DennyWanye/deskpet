/**
 * RelayAuthAdapter — paid-edition implementation of `AuthAdapter` that
 * talks to the relay station (chinzy.com).
 *
 * Lives in the OSS repo for now (Week 2 of the relay integration plan).
 * The boundary between OSS and closed-source paid is currently the
 * Vite env flag `VITE_AUTH_EDITION="relay"` — OSS builds default to
 * `manual` and never instantiate this class. When the closed-source
 * repo splits out we'll move only this file + its test; the OS
 * keyring + device-id Rust commands stay in the OSS repo because
 * they're harmless on their own.
 *
 * Authentication model (per DESKPET-INTEGRATION-GUIDE.md):
 *
 *   register → returns access_token + activation.token (email infra
 *              not yet live, see §10 followup #2) → auto-activate via
 *              /v1/auth/activate (PR #16) with /api-direct/auth/activate
 *              fallback
 *   login    → access (1h) + refresh (30d, rotated on every /refresh)
 *   401      → call /v1/auth/refresh once; on success retry original
 *              request; on failure emit logout event and propagate
 *   logout   → POST /v1/auth/logout (revokes refresh) + wipe local state
 *
 * Device key model:
 *
 *   - GET /v1/providers issues a fresh `tsk_xxx` and invalidates the
 *     previous one for this deviceId. We MUST avoid scheduled refresh
 *     because that would just race ourselves.
 *   - Strategy: fetch once on login; cache; on 401 from LLM call,
 *     re-fetch providers (rotates key) and retry.
 *
 * Persistence:
 *
 *   - access_token  → OS keyring slot `deskpet-relay/access_token`
 *   - refresh_token → OS keyring slot `deskpet-relay/refresh_token`
 *   - device_key    → OS keyring slot `deskpet-relay/device_key`
 *   - device_id     → plain file `<user_data>/device_id` (not secret)
 *   - user object   → kept in memory only; re-derived from /v1/me on
 *                     startup when an access_token survives a restart
 *
 * Concurrency:
 *
 *   We dedupe in-flight refresh and listProviders calls so a burst of
 *   401s doesn't fire three refreshes (which would mutually invalidate
 *   each other). See `inflightRefresh` / `inflightProviders` below.
 *
 * NOT implemented in this commit:
 *
 *   - Forgot-password UI (relay followup #3 blocks this)
 *   - Username login (relay only supports email per §1.1 of guide)
 *   - OAuth third-party login (relay followup #3 blocks this)
 *   - Custom error UI for QUOTA_EXHAUSTED (upstream RelayApiError is
 *     thrown; UI layer turns it into the modal in a later commit)
 */
import {
  type AuthAdapter,
  type AuthEvent,
  type LoginCredentials,
  type Provider,
  type ProviderModel,
  type RegisterCredentials,
  type UsageSummary,
  type User,
} from "./types";
import { RelayApiError, type RelayErrorCode } from "./RelayApiError";
import * as relayBindings from "../bindings/relay";

/**
 * Subset of the Tauri-bindings surface we depend on. Extracting this
 * as a type makes the unit test trivially mockable — vitest replaces
 * the module via `vi.mock`, but having the shape spelled out also
 * pays for itself when we later want a Node/jsdom test harness that
 * doesn't load `@tauri-apps/api/core`.
 */
export interface RelayBindings {
  setRelayAccessToken(token: string): Promise<void>;
  getRelayAccessToken(): Promise<string | null>;
  setRelayRefreshToken(token: string): Promise<void>;
  getRelayRefreshToken(): Promise<string | null>;
  setRelayDeviceKey(key: string): Promise<void>;
  getRelayDeviceKey(): Promise<string | null>;
  clearAllRelaySecrets(): Promise<void>;
  getOrCreateDeviceId(): Promise<string>;
  getDefaultDeviceName(): Promise<string>;
}

/** Per-edition tunables. Default to the production relay. */
export interface RelayAdapterOptions {
  /** Base URL without trailing slash, e.g. "https://chinzy.com". */
  baseUrl?: string;
  /** Override for `fetch` — vitest sets this to a mock. */
  fetchImpl?: typeof fetch;
  /** Override for Tauri bindings — vitest sets this to a fake. */
  bindings?: RelayBindings;
}

const DEFAULT_BASE_URL = "https://chinzy.com";

/** Shape returned by the auth endpoints; subset of the full envelope. */
interface AuthTokenResponse {
  access_token: string;
  refresh_token: string;
  token_type?: string;
  expires_in?: number;
  user?: User | null;
  activation?: { token: string; expiresAt: string } | null;
}

/** /v1/providers response shape — matches §3.7 of integration guide. */
interface ProvidersResponse {
  providers: Provider[];
}

export class RelayAuthAdapter implements AuthAdapter {
  readonly id = "relay";
  readonly displayName = "中转账户";

  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly bindings: RelayBindings;

  private accessToken: string | null = null;
  private refreshToken: string | null = null;
  private deviceKey: string | null = null;
  private user: User | null = null;
  private deviceId: string | null = null;
  private deviceName: string | null = null;

  private listeners = new Set<(e: AuthEvent) => void>();

  /**
   * In-flight de-duplication. Two motivations:
   *   1. Burst-of-401s: e.g. UI fires `listProviders` and `getUsage`
   *      simultaneously, both 401, both kick refresh — without dedup
   *      the second refresh would use the freshly-rotated (now-invalid)
   *      old refresh token and fail.
   *   2. listProviders: kicking two concurrent /v1/providers calls
   *      makes them invalidate each other's device key in a race.
   */
  private inflightRefresh: Promise<void> | null = null;
  private inflightProviders: Promise<Provider[]> | null = null;

  constructor(opts: RelayAdapterOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.fetchImpl =
      opts.fetchImpl ?? ((...args) => globalThis.fetch(...args));
    this.bindings = opts.bindings ?? (relayBindings as RelayBindings);
  }

  // ──────────────────────────────────────────────────────────────
  // AuthAdapter contract
  // ──────────────────────────────────────────────────────────────

  isAuthenticated(): boolean {
    return this.accessToken !== null && this.user !== null;
  }

  currentUser(): User | null {
    return this.user;
  }

  /**
   * Re-attach to a previously-persisted session. Called once at app
   * startup by the UI. Returns true if a valid session was restored,
   * false if the user needs to log in again.
   *
   * We deliberately do not call this from the constructor — the
   * constructor must be side-effect-free per the AuthAdapter contract.
   */
  async restoreSession(): Promise<boolean> {
    const access = await this.bindings.getRelayAccessToken();
    const refresh = await this.bindings.getRelayRefreshToken();
    if (!access || !refresh) {
      return false;
    }
    this.accessToken = access;
    this.refreshToken = refresh;
    this.deviceKey = await this.bindings.getRelayDeviceKey();
    try {
      this.user = await this.fetchMe();
      this.emit({ type: "login", user: this.user });
      return true;
    } catch (err) {
      // /v1/me failed even after refresh — session is dead, clean up.
      if (err instanceof RelayApiError && err.code === "INVALID_TOKEN") {
        await this.localLogout();
      }
      return false;
    }
  }

  async login(credentials: LoginCredentials): Promise<User> {
    await this.ensureDeviceIdentity();
    const res = await this.fetchImpl(`${this.baseUrl}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: credentials.email,
        password: credentials.password,
        deviceId: this.deviceId,
        deviceName: this.deviceName,
      }),
    }).catch((e: unknown) => {
      throw RelayApiError.network(stringifyError(e));
    });
    if (!res.ok) {
      throw await RelayApiError.fromResponse(res);
    }
    const data = (await res.json()) as AuthTokenResponse;
    await this.persistTokens(data);
    this.user = data.user ?? (await this.fetchMe());
    this.emit({ type: "login", user: this.user });
    return this.user;
  }

  /**
   * Register + auto-activate in a single call. Per integration guide
   * §3.1, the relay currently returns `activation.token` directly in
   * the register response (email infra not live). When the email
   * pipeline ships, that field disappears and the user has to click
   * a link — we'll branch on its presence here:
   *
   *   - activation.token present → call /v1/auth/activate immediately
   *   - activation absent        → throw a special error so UI can
   *                                show "check your inbox"
   */
  async register(credentials: RegisterCredentials): Promise<User> {
    await this.ensureDeviceIdentity();
    const res = await this.fetchImpl(`${this.baseUrl}/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: credentials.email,
        password: credentials.password,
        deviceId: this.deviceId,
        deviceName: this.deviceName,
      }),
    }).catch((e: unknown) => {
      throw RelayApiError.network(stringifyError(e));
    });
    if (!res.ok) {
      throw await RelayApiError.fromResponse(res);
    }
    const data = (await res.json()) as AuthTokenResponse;
    await this.persistTokens(data);

    if (data.activation?.token) {
      // Auto-activate path. Try /v1/auth/activate first (PR #16
      // post-merge); fall back to the legacy /api-direct path which
      // is still live until the v1 mirror ships.
      const activated = await this.activateAccount(data.activation.token);
      this.user = activated ?? (await this.fetchMe());
    } else {
      // Email-verification path (post-PR-#16, email infra live). The
      // server has issued tokens but /v1/me will refuse them until
      // the user clicks the email link. We return what info we have
      // and let the UI display the "check email" state.
      this.user = data.user ?? {
        id: "pending",
        email: credentials.email,
        plan: "prepaid",
      };
    }
    this.emit({ type: "login", user: this.user });
    return this.user;
  }

  async logout(): Promise<void> {
    if (!this.accessToken) {
      // Defensive: already logged out, just clear local state in case
      // a previous logout was interrupted mid-way.
      await this.localLogout();
      return;
    }
    try {
      await this.fetchImpl(`${this.baseUrl}/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${this.accessToken}` },
      });
    } catch {
      // Even if the network call fails, we MUST clear local state so
      // the user perceives logout as immediate. Worst case: refresh
      // token stays valid server-side for up to 30 days, but the
      // user can no longer use it from this device.
    }
    await this.localLogout();
  }

  async listProviders(): Promise<Provider[]> {
    if (!this.accessToken) return [];
    // Coalesce concurrent calls — every call rotates the device key,
    // so two concurrent calls means the first invocation's key is
    // dead before its caller can use it.
    if (this.inflightProviders) return this.inflightProviders;
    this.inflightProviders = (async () => {
      try {
        const data = await this.authedJson<ProvidersResponse>(
          "GET",
          "/v1/providers",
          undefined,
          {
            "X-Device-Id": this.deviceId ?? "",
            "X-Device-Name": this.deviceName ?? "",
          },
        );
        const providers = data.providers ?? [];
        // Cache the freshly-rotated device key — every provider entry
        // currently shares the same `api_key` per §3.7 "重要语义", so
        // grabbing the first one is sufficient.
        const key = providers.find((p) => p.api_key)?.api_key ?? null;
        if (key) {
          this.deviceKey = key;
          await this.bindings.setRelayDeviceKey(key);
        }
        this.emit({ type: "providers-updated", providers });
        return providers;
      } finally {
        this.inflightProviders = null;
      }
    })();
    return this.inflightProviders;
  }

  async getUsage(): Promise<UsageSummary | null> {
    if (!this.accessToken) return null;
    const data = await this.authedJson<UsageSummary>("GET", "/v1/usage/summary");
    this.emit({ type: "usage-updated", usage: data });
    return data;
  }

  onEvent(handler: (e: AuthEvent) => void): () => void {
    this.listeners.add(handler);
    return () => {
      this.listeners.delete(handler);
    };
  }

  // ──────────────────────────────────────────────────────────────
  // Internal helpers
  // ──────────────────────────────────────────────────────────────

  /**
   * Fetch device id + name from Rust on first need; cache for the
   * lifetime of the adapter instance. The Rust side already persists
   * and de-dupes, so calling more than once is just IPC overhead.
   */
  private async ensureDeviceIdentity(): Promise<void> {
    if (!this.deviceId) {
      this.deviceId = await this.bindings.getOrCreateDeviceId();
    }
    if (!this.deviceName) {
      this.deviceName = await this.bindings.getDefaultDeviceName();
    }
  }

  /**
   * Persist new tokens to keyring + in-memory state. Wraps both writes
   * in try/catch because individual keyring failures (e.g. Linux Secret
   * Service not running) should NOT block the login flow — in-memory
   * tokens are still functional, the user just has to log in again
   * after restart.
   */
  private async persistTokens(data: AuthTokenResponse): Promise<void> {
    this.accessToken = data.access_token;
    this.refreshToken = data.refresh_token;
    try {
      await this.bindings.setRelayAccessToken(data.access_token);
    } catch (e) {
      console.warn("[RelayAuthAdapter] failed to persist access token:", e);
    }
    try {
      await this.bindings.setRelayRefreshToken(data.refresh_token);
    } catch (e) {
      console.warn("[RelayAuthAdapter] failed to persist refresh token:", e);
    }
  }

  /**
   * Activate a newly-registered account. Tries `/v1/auth/activate`
   * (the canonical path after PR #16 merges) first; on 404 falls
   * back to `/api-direct/auth/activate` (the current pre-#16 live
   * endpoint). Returns the activated User envelope on success, or
   * `null` if the relay didn't return a user body — caller should
   * then call `fetchMe()`.
   *
   * Tolerates a wider set of failure modes than the other helpers:
   * a brand-new account whose activation token is stale on arrival
   * (e.g. clock skew at boundary) shouldn't bring the whole register
   * flow down — we still want the access_token persisted so the user
   * can retry. The caller will see the RelayApiError and decide.
   */
  private async activateAccount(token: string): Promise<User | null> {
    const tryPath = async (path: string): Promise<User | null> => {
      const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!res.ok) {
        // 404 here is the "this path isn't deployed yet" signal — propagate
        // a special error the outer try-fallback can recognise.
        throw await RelayApiError.fromResponse(res);
      }
      try {
        return (await res.json()) as User;
      } catch {
        return null;
      }
    };

    try {
      return await tryPath("/v1/auth/activate");
    } catch (err) {
      const isMissing =
        err instanceof RelayApiError &&
        (err.status === 404 || err.code === "NOT_FOUND");
      if (!isMissing) throw err;
      // Fallback to the current pre-PR-#16 endpoint.
      return await tryPath("/api-direct/auth/activate");
    }
  }

  /**
   * GET /v1/me — also used to validate a restored session.
   */
  private async fetchMe(): Promise<User> {
    return await this.authedJson<User>("GET", "/v1/me");
  }

  /**
   * Authed request with automatic refresh-on-401.
   *
   *   1. Send request with current access_token.
   *   2. On 401: take inflightRefresh (dedup), retry once after refresh.
   *   3. On 401 again: throw the original RelayApiError so the UI
   *      surfaces "please log in" rather than looping forever.
   *
   * Generic over response shape; callers pass the expected JSON type.
   */
  private async authedJson<T>(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
    extraHeaders: Record<string, string> = {},
  ): Promise<T> {
    const send = async (): Promise<Response> => {
      const headers: Record<string, string> = {
        ...extraHeaders,
        Authorization: `Bearer ${this.accessToken ?? ""}`,
      };
      if (body !== undefined) {
        headers["Content-Type"] = "application/json";
      }
      return await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    };

    let res: Response;
    try {
      res = await send();
    } catch (e) {
      throw RelayApiError.network(stringifyError(e));
    }

    if (res.status === 401) {
      // Refresh + single retry. Refresh failure is fatal — propagate
      // and let UI handle re-login. Don't keep retrying.
      await this.refreshSession();
      try {
        res = await send();
      } catch (e) {
        throw RelayApiError.network(stringifyError(e));
      }
    }

    if (!res.ok) {
      throw await RelayApiError.fromResponse(res);
    }
    return (await res.json()) as T;
  }

  /**
   * POST /v1/auth/refresh — rotate both tokens. On any failure we
   * give up and force a re-login (refresh tokens don't have an
   * "almost expired" state; either they work or they don't).
   *
   * Dedupes concurrent callers via `inflightRefresh`.
   */
  private async refreshSession(): Promise<void> {
    if (this.inflightRefresh) return this.inflightRefresh;
    this.inflightRefresh = (async () => {
      try {
        if (!this.refreshToken) {
          throw new RelayApiError({
            code: "INVALID_TOKEN",
            message: "No refresh token; user must log in.",
            status: 401,
            requestId: null,
            retryAfter: null,
          });
        }
        const res = await this.fetchImpl(`${this.baseUrl}/v1/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: this.refreshToken }),
        });
        if (!res.ok) {
          // Refresh failed → session is dead. Clear local state so
          // subsequent calls don't keep retrying with a known-bad
          // refresh token (the relay's rotation logic means even one
          // failed refresh permanently invalidates the chain).
          await this.localLogout();
          throw await RelayApiError.fromResponse(res);
        }
        const data = (await res.json()) as AuthTokenResponse;
        await this.persistTokens(data);
        this.emit({ type: "tokens-refreshed" });
      } finally {
        this.inflightRefresh = null;
      }
    })();
    return this.inflightRefresh;
  }

  /**
   * Wipe in-memory + persisted state. Idempotent — safe to call from
   * the logout flow OR from inside refreshSession's failure branch.
   */
  private async localLogout(): Promise<void> {
    this.accessToken = null;
    this.refreshToken = null;
    this.deviceKey = null;
    this.user = null;
    // Don't reset deviceId — it's a stable identifier, not a credential.
    try {
      await this.bindings.clearAllRelaySecrets();
    } catch (e) {
      console.warn("[RelayAuthAdapter] clearAllRelaySecrets failed:", e);
    }
    this.emit({ type: "logout" });
  }

  private emit(event: AuthEvent): void {
    for (const handler of this.listeners) {
      try {
        handler(event);
      } catch (e) {
        // One bad listener mustn't break the others — events are
        // best-effort notifications, not transactional.
        console.error("[RelayAuthAdapter] event handler threw:", e);
      }
    }
  }

  // ──────────────────────────────────────────────────────────────
  // Test introspection — kept package-private via underscore prefix.
  // ──────────────────────────────────────────────────────────────

  /** @internal — vitest reaches into these. Production code MUST NOT. */
  _testGetState(): {
    accessToken: string | null;
    refreshToken: string | null;
    deviceKey: string | null;
    user: User | null;
    deviceId: string | null;
  } {
    return {
      accessToken: this.accessToken,
      refreshToken: this.refreshToken,
      deviceKey: this.deviceKey,
      user: this.user,
      deviceId: this.deviceId,
    };
  }
}

/**
 * Pretty-print whatever the platform threw at us — fetch rejects with
 * TypeError on network failures, AbortError on cancellation, and
 * occasionally with a plain string in older runtimes. RelayApiError.network()
 * needs *something* stringy for the user-visible message.
 */
function stringifyError(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  try {
    return JSON.stringify(e);
  } catch {
    return "网络请求失败";
  }
}

// Re-export for symmetry with the other adapters' import shape.
export type { ProviderModel };
export { type RelayErrorCode };
