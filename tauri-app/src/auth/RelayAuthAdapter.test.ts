/**
 * RelayAuthAdapter unit tests.
 *
 * Strategy: inject mocks via the constructor's `fetchImpl` + `bindings`
 * options — no `vi.mock` of the bindings module needed. Each test
 * builds its own adapter so cases stay independent.
 *
 * We deliberately do NOT cover the "real Tauri IPC" surface here —
 * `bindings/relay.ts` is a thin invoke wrapper and is exercised by
 * the Rust unit tests + manual E2E. The interesting logic lives in
 * the adapter's refresh / retry / dedup / activation-fallback flows.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RelayAuthAdapter, type RelayBindings } from "./RelayAuthAdapter";
import { RelayApiError } from "./RelayApiError";
import type { AuthEvent, Provider } from "./types";

// ── helpers ──────────────────────────────────────────────────────

/**
 * Build a stub `bindings` object with all methods spied. Each method
 * gets its OWN `vi.fn()` — otherwise vitest's `toHaveBeenLastCalledWith`
 * sees calls from sibling methods (e.g. set-access and set-refresh
 * both pointing at the same noop spy) and assertions reflect whichever
 * sibling was called last.
 */
function makeBindings(overrides: Partial<RelayBindings> = {}): RelayBindings {
  return {
    setRelayAccessToken: vi.fn(async () => undefined),
    getRelayAccessToken: vi.fn(async () => null),
    setRelayRefreshToken: vi.fn(async () => undefined),
    getRelayRefreshToken: vi.fn(async () => null),
    setRelayDeviceKey: vi.fn(async () => undefined),
    getRelayDeviceKey: vi.fn(async () => null),
    clearAllRelaySecrets: vi.fn(async () => undefined),
    getOrCreateDeviceId: vi.fn(async () => "dev-id-stub"),
    getDefaultDeviceName: vi.fn(async () => "DeskPet/Test"),
    ...overrides,
  };
}

/**
 * Build a `Response`-like object for the fetch mock. Using a real
 * Response would require `whatwg-fetch` in jsdom — overkill for what
 * is essentially "headers + json body".
 */
function mkResponse(opts: {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}): Response {
  const status = opts.status ?? 200;
  const headers = new Headers(opts.headers ?? {});
  return {
    ok: status >= 200 && status < 300,
    status,
    headers,
    json: async () => opts.body ?? {},
    text: async () =>
      typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body ?? {}),
  } as Response;
}

/**
 * Sugar for a typed fetch mock that returns a queue of pre-built
 * responses in order. Throws if called more times than supplied —
 * surfaces "test forgot to enqueue another response" loud and early.
 */
function queueFetch(responses: Response[]): typeof fetch {
  let i = 0;
  return (async () => {
    if (i >= responses.length) {
      throw new Error(
        `fetch mock exhausted: ${i} calls made, only ${responses.length} responses queued`,
      );
    }
    return responses[i++];
  }) as typeof fetch;
}

const SAMPLE_USER = {
  id: "user_abc",
  email: "alice@example.com",
  role: "USER",
  plan: "prepaid",
  created_at: "2026-05-20T00:00:00Z",
};

const SAMPLE_TOKENS = {
  access_token: "access_v1",
  refresh_token: "refresh_v1",
  token_type: "Bearer",
  expires_in: 3600,
};

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── 1. login happy path ──────────────────────────────────────────

describe("RelayAuthAdapter.login", () => {
  it("posts credentials, persists tokens, emits login event", async () => {
    const bindings = makeBindings();
    const fetchImpl = vi.fn(
      queueFetch([
        mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } }),
      ]),
    );

    const adapter = new RelayAuthAdapter({
      baseUrl: "https://chinzy.test",
      fetchImpl,
      bindings,
    });
    const events: AuthEvent[] = [];
    adapter.onEvent((e) => events.push(e));

    const u = await adapter.login({
      email: "alice@example.com",
      password: "min8chars",
    });

    expect(u).toEqual(SAMPLE_USER);
    expect(adapter.isAuthenticated()).toBe(true);
    expect(adapter.currentUser()).toEqual(SAMPLE_USER);

    // Verify the fetch payload — endpoint, method, and deviceId injection.
    const call = fetchImpl.mock.calls[0];
    expect(call[0]).toBe("https://chinzy.test/v1/auth/login");
    const init = call[1] as RequestInit;
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body as string);
    expect(sent).toMatchObject({
      email: "alice@example.com",
      password: "min8chars",
      deviceId: "dev-id-stub",
      deviceName: "DeskPet/Test",
    });

    // Keyring persistence — both tokens written.
    expect(bindings.setRelayAccessToken).toHaveBeenCalledWith("access_v1");
    expect(bindings.setRelayRefreshToken).toHaveBeenCalledWith("refresh_v1");

    // Event emitted.
    expect(events).toEqual([{ type: "login", user: SAMPLE_USER }]);
  });

  it("wraps 401 INVALID_CREDENTIALS in RelayApiError", async () => {
    const fetchImpl = queueFetch([
      mkResponse({
        status: 401,
        body: {
          code: "INVALID_CREDENTIALS",
          message: "Bad email or password.",
          request_id: "req_xyz",
        },
        headers: { "X-Request-Id": "req_xyz" },
      }),
    ]);
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings: makeBindings() });
    await expect(
      adapter.login({ email: "a@b.c", password: "wrong123" }),
    ).rejects.toMatchObject({
      name: "RelayApiError",
      code: "INVALID_CREDENTIALS",
      status: 401,
      requestId: "req_xyz",
    });
    expect(adapter.isAuthenticated()).toBe(false);
  });

  it("surfaces network failures as NETWORK_ERROR", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings: makeBindings() });
    const err = (await adapter
      .login({ email: "a@b.c", password: "x" })
      .catch((e: unknown) => e)) as RelayApiError;
    expect(err).toBeInstanceOf(RelayApiError);
    expect(err.code).toBe("NETWORK_ERROR");
    expect(err.status).toBe(0);
  });
});

// ── 2. register + auto-activate ─────────────────────────────────

describe("RelayAuthAdapter.register", () => {
  it("auto-activates via /v1/auth/activate when activation.token present", async () => {
    const fetchImpl = vi.fn(
      queueFetch([
        // /v1/auth/register → returns activation.token
        mkResponse({
          body: {
            ...SAMPLE_TOKENS,
            user: null,
            activation: { token: "verify_abc", expiresAt: "2026-05-22T00:00:00Z" },
          },
        }),
        // /v1/auth/activate → returns user
        mkResponse({ body: SAMPLE_USER }),
      ]),
    );

    const adapter = new RelayAuthAdapter({
      baseUrl: "https://chinzy.test",
      fetchImpl,
      bindings: makeBindings(),
    });
    const u = await adapter.register({
      email: "new@example.com",
      password: "min8chars",
    });
    expect(u).toEqual(SAMPLE_USER);

    expect(fetchImpl.mock.calls[0][0]).toBe(
      "https://chinzy.test/v1/auth/register",
    );
    expect(fetchImpl.mock.calls[1][0]).toBe(
      "https://chinzy.test/v1/auth/activate",
    );
    const activateBody = JSON.parse(
      (fetchImpl.mock.calls[1][1] as RequestInit).body as string,
    );
    expect(activateBody).toEqual({ token: "verify_abc" });
  });

  it("falls back to /api-direct/auth/activate when /v1 path 404s", async () => {
    const fetchImpl = vi.fn(
      queueFetch([
        // register
        mkResponse({
          body: {
            ...SAMPLE_TOKENS,
            activation: { token: "verify_abc", expiresAt: "x" },
          },
        }),
        // /v1/auth/activate not yet deployed (pre-PR-#16)
        mkResponse({
          status: 404,
          body: { code: "NOT_FOUND", message: "no such route", request_id: "r" },
        }),
        // legacy path succeeds
        mkResponse({ body: SAMPLE_USER }),
      ]),
    );

    const adapter = new RelayAuthAdapter({
      baseUrl: "https://chinzy.test",
      fetchImpl,
      bindings: makeBindings(),
    });
    const u = await adapter.register({
      email: "new@example.com",
      password: "min8chars",
    });
    expect(u).toEqual(SAMPLE_USER);
    expect(fetchImpl.mock.calls[2][0]).toBe(
      "https://chinzy.test/api-direct/auth/activate",
    );
  });

  it("treats EMAIL_TAKEN (409) as RelayApiError", async () => {
    const fetchImpl = queueFetch([
      mkResponse({
        status: 409,
        body: {
          code: "EMAIL_TAKEN",
          message: "邮箱已注册",
          request_id: "req_a",
        },
      }),
    ]);
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings: makeBindings() });
    await expect(
      adapter.register({ email: "dup@example.com", password: "min8chars" }),
    ).rejects.toMatchObject({ code: "EMAIL_TAKEN", status: 409 });
  });
});

// ── 3. refresh-on-401 + dedup ────────────────────────────────────

describe("RelayAuthAdapter refresh-on-401", () => {
  it("refreshes once then retries the original request", async () => {
    const bindings = makeBindings();
    const fetchImpl = vi.fn(
      queueFetch([
        // login
        mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } }),
        // /v1/usage/summary → 401
        mkResponse({
          status: 401,
          body: { code: "EXPIRED_TOKEN", message: "exp", request_id: "r1" },
        }),
        // /v1/auth/refresh → new tokens
        mkResponse({
          body: { access_token: "access_v2", refresh_token: "refresh_v2" },
        }),
        // /v1/usage/summary retry → 200
        mkResponse({
          body: {
            plan: "prepaid",
            balance: { amount_minor: 1000, currency: "CNY" },
          },
        }),
      ]),
    );

    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    await adapter.login({ email: "a@b.c", password: "min8chars" });

    const usage = await adapter.getUsage();
    expect(usage?.balance?.amount_minor).toBe(1000);

    // Refresh must have rotated tokens in keyring.
    expect(bindings.setRelayAccessToken).toHaveBeenLastCalledWith("access_v2");
    expect(bindings.setRelayRefreshToken).toHaveBeenLastCalledWith("refresh_v2");
  });

  it("dedupes concurrent 401s into one refresh", async () => {
    const bindings = makeBindings();
    // After login, two protected calls fire concurrently and both 401.
    // Only ONE /v1/auth/refresh hit should happen.
    let refreshCalls = 0;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/v1/auth/login")) {
        return mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } });
      }
      if (u.endsWith("/v1/auth/refresh")) {
        refreshCalls += 1;
        return mkResponse({
          body: { access_token: "access_v2", refresh_token: "refresh_v2" },
        });
      }
      // Determine call number for the protected endpoints by current
      // access token in header — first call has v1, retry has v2.
      const headers = init?.headers as Record<string, string> | undefined;
      const auth = headers?.Authorization ?? "";
      if (auth.includes("access_v1")) {
        return mkResponse({
          status: 401,
          body: { code: "EXPIRED_TOKEN", message: "exp", request_id: "r" },
        });
      }
      // Successful retry with new token.
      if (u.endsWith("/v1/usage/summary")) {
        return mkResponse({
          body: { plan: "prepaid", balance: { amount_minor: 100, currency: "CNY" } },
        });
      }
      if (u.endsWith("/v1/providers")) {
        return mkResponse({
          body: {
            providers: [
              {
                id: "relay-openai",
                name: "OpenAI",
                base_url: "https://chinzy.test/v1",
                api_key: "tsk_xxx",
                models: [],
                openai_compatible: true,
                supports_streaming: true,
              },
            ],
          },
        });
      }
      throw new Error(`unexpected fetch url: ${u}`);
    }) as unknown as typeof fetch;

    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    await adapter.login({ email: "a@b.c", password: "min8chars" });

    const [usage, providers] = await Promise.all([
      adapter.getUsage(),
      adapter.listProviders(),
    ]);

    expect(refreshCalls).toBe(1);
    expect(usage?.balance?.amount_minor).toBe(100);
    expect(providers.length).toBe(1);
  });

  it("clears state when refresh itself fails", async () => {
    const bindings = makeBindings();
    const fetchImpl = vi.fn(
      queueFetch([
        mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } }),
        // protected call → 401
        mkResponse({
          status: 401,
          body: { code: "EXPIRED_TOKEN", message: "exp", request_id: "r" },
        }),
        // refresh → also 401 (refresh token revoked)
        mkResponse({
          status: 401,
          body: { code: "INVALID_TOKEN", message: "rt invalid", request_id: "r" },
        }),
      ]),
    );

    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    await adapter.login({ email: "a@b.c", password: "min8chars" });

    await expect(adapter.getUsage()).rejects.toMatchObject({
      code: "INVALID_TOKEN",
    });
    // Local state must be wiped — caller can't keep retrying.
    expect(adapter.isAuthenticated()).toBe(false);
    expect(bindings.clearAllRelaySecrets).toHaveBeenCalled();
  });
});

// ── 4. providers + device key rotation ──────────────────────────

describe("RelayAuthAdapter.listProviders", () => {
  const samplePayload = {
    providers: [
      {
        id: "relay-openai",
        name: "OpenAI (relay)",
        base_url: "https://chinzy.test/v1",
        api_key: "tsk_AAA",
        models: [{ id: "gpt-5.2", context_window: 400000, capabilities: ["chat"] }],
        openai_compatible: true,
        supports_streaming: true,
        priority: 1,
      },
    ] as Provider[],
  };

  it("sends X-Device-Id + X-Device-Name and caches device key", async () => {
    const bindings = makeBindings();
    const fetchImpl = vi.fn(
      queueFetch([
        mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } }),
        mkResponse({ body: samplePayload }),
      ]),
    );
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    await adapter.login({ email: "a@b.c", password: "min8chars" });

    const providers = await adapter.listProviders();
    expect(providers).toHaveLength(1);
    expect(providers[0].api_key).toBe("tsk_AAA");

    const providersCall = fetchImpl.mock.calls[1];
    const headers = (providersCall[1] as RequestInit).headers as Record<
      string,
      string
    >;
    expect(headers["X-Device-Id"]).toBe("dev-id-stub");
    expect(headers["X-Device-Name"]).toBe("DeskPet/Test");
    expect(bindings.setRelayDeviceKey).toHaveBeenCalledWith("tsk_AAA");
  });

  it("dedupes concurrent listProviders calls so device key isn't double-rotated", async () => {
    const bindings = makeBindings();
    let providersHit = 0;
    const fetchImpl = vi.fn(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/v1/auth/login")) {
        return mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } });
      }
      if (u.endsWith("/v1/providers")) {
        providersHit += 1;
        return mkResponse({ body: samplePayload });
      }
      throw new Error(`unexpected: ${u}`);
    }) as unknown as typeof fetch;

    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    await adapter.login({ email: "a@b.c", password: "min8chars" });

    const [a, b, c] = await Promise.all([
      adapter.listProviders(),
      adapter.listProviders(),
      adapter.listProviders(),
    ]);
    expect(providersHit).toBe(1);
    expect(a).toEqual(b);
    expect(b).toEqual(c);
  });

  it("returns [] when not authenticated (no error)", async () => {
    const adapter = new RelayAuthAdapter({
      fetchImpl: queueFetch([]),
      bindings: makeBindings(),
    });
    expect(await adapter.listProviders()).toEqual([]);
  });

  it("emits providers-updated event", async () => {
    const bindings = makeBindings();
    const fetchImpl = vi.fn(
      queueFetch([
        mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } }),
        mkResponse({ body: samplePayload }),
      ]),
    );
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    const events: AuthEvent[] = [];
    adapter.onEvent((e) => events.push(e));

    await adapter.login({ email: "a@b.c", password: "min8chars" });
    await adapter.listProviders();
    const last = events[events.length - 1];
    expect(last.type).toBe("providers-updated");
    if (last.type === "providers-updated") {
      expect(last.providers).toHaveLength(1);
    }
  });
});

// ── 5. logout + 429 / retry-after handling ──────────────────────

describe("RelayAuthAdapter.logout", () => {
  it("calls /v1/auth/logout and clears all keyring slots", async () => {
    const bindings = makeBindings();
    const fetchImpl = vi.fn(
      queueFetch([
        mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } }),
        mkResponse({ body: { revoked: 1 } }),
      ]),
    );
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    await adapter.login({ email: "a@b.c", password: "min8chars" });
    await adapter.logout();

    expect(adapter.isAuthenticated()).toBe(false);
    expect(adapter.currentUser()).toBeNull();
    expect(bindings.clearAllRelaySecrets).toHaveBeenCalled();
    const lastCall = fetchImpl.mock.calls[fetchImpl.mock.calls.length - 1];
    expect(lastCall[0]).toMatch(/\/v1\/auth\/logout$/);
  });

  it("still clears local state even if /logout fails", async () => {
    const bindings = makeBindings();
    let calls = 0;
    const fetchImpl = vi.fn(async (url: string) => {
      calls += 1;
      if (calls === 1) {
        return mkResponse({ body: { ...SAMPLE_TOKENS, user: SAMPLE_USER } });
      }
      throw new TypeError("network down");
    }) as unknown as typeof fetch;

    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });
    await adapter.login({ email: "a@b.c", password: "min8chars" });
    await adapter.logout(); // must not throw
    expect(adapter.isAuthenticated()).toBe(false);
  });
});

describe("RelayApiError retry-after parsing", () => {
  it("surfaces 429 with Retry-After header value", async () => {
    const fetchImpl = queueFetch([
      mkResponse({
        status: 429,
        body: { code: "RATE_LIMITED", message: "slow down", request_id: "r" },
        headers: { "Retry-After": "17", "X-Request-Id": "r" },
      }),
    ]);
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings: makeBindings() });
    const err = (await adapter
      .login({ email: "a@b.c", password: "x" })
      .catch((e: unknown) => e)) as RelayApiError;
    expect(err.code).toBe("RATE_LIMITED");
    expect(err.retryAfter).toBe(17);
  });
});

// ── 6. session restore ───────────────────────────────────────────

describe("RelayAuthAdapter.restoreSession", () => {
  it("returns false when no tokens are persisted", async () => {
    const adapter = new RelayAuthAdapter({
      fetchImpl: queueFetch([]),
      bindings: makeBindings(),
    });
    expect(await adapter.restoreSession()).toBe(false);
    expect(adapter.isAuthenticated()).toBe(false);
  });

  it("restores from persisted tokens and refetches /v1/me", async () => {
    const bindings = makeBindings({
      getRelayAccessToken: vi.fn(async () => "persisted_access"),
      getRelayRefreshToken: vi.fn(async () => "persisted_refresh"),
      getRelayDeviceKey: vi.fn(async () => "tsk_persisted"),
    });
    const fetchImpl = vi.fn(queueFetch([mkResponse({ body: SAMPLE_USER })]));
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings });

    expect(await adapter.restoreSession()).toBe(true);
    expect(adapter.isAuthenticated()).toBe(true);
    expect(adapter.currentUser()?.email).toBe("alice@example.com");

    // Verify the Authorization header used the persisted token.
    const init = fetchImpl.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer persisted_access");
  });
});

// ── 7. error envelope mapping ───────────────────────────────────

describe("RelayApiError envelope mapping", () => {
  it("maps unknown server code to UNKNOWN", async () => {
    const fetchImpl = queueFetch([
      mkResponse({
        status: 418,
        body: { code: "TEAPOT", message: "I am a teapot", request_id: "r" },
      }),
    ]);
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings: makeBindings() });
    const err = (await adapter
      .login({ email: "a@b.c", password: "x" })
      .catch((e: unknown) => e)) as RelayApiError;
    expect(err.code).toBe("UNKNOWN");
    expect(err.status).toBe(418);
  });

  it("infers code from HTTP status when body is empty", async () => {
    const fetchImpl = queueFetch([
      mkResponse({ status: 502, body: null }),
    ]);
    const adapter = new RelayAuthAdapter({ fetchImpl, bindings: makeBindings() });
    const err = (await adapter
      .login({ email: "a@b.c", password: "x" })
      .catch((e: unknown) => e)) as RelayApiError;
    expect(err.code).toBe("UPSTREAM_ERROR");
  });
});
