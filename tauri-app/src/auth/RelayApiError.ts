/**
 * RelayApiError — typed wrapper around the relay station's error envelope.
 *
 * Per `plans/DESKPET-INTEGRATION-GUIDE.md` §4.1, all platform-layer errors
 * (auth / validation / rate limit / quota) come back as:
 *   { code: "MACHINE_READABLE_CODE", message: "...", request_id: "req_xxx" }
 * plus an `X-Request-Id` response header.
 *
 * This class normalises that envelope so UI code can switch on `.code`
 * instead of pattern-matching on stringly-typed messages, and so the
 * `Retry-After` header from 429s is reachable without re-parsing
 * headers everywhere.
 *
 * LLM call errors (`/v1/chat/completions`, `/anthropic/v1/messages`) are
 * NOT wrapped in this envelope per §4.3 — the relay forwards upstream
 * responses verbatim. Those use a different error path (OpenAI SDK
 * surfaces them as `APIError`); we do NOT try to coerce them into
 * `RelayApiError`.
 */

/**
 * Closed set of `code` strings the relay uses. Kept as a literal union
 * (not enum) so callers can switch exhaustively without an import.
 *
 * Source: DESKPET-INTEGRATION-GUIDE.md §4.2. Extend cautiously — any
 * new code requires updating UI error mappers in the closed-source
 * paid build.
 */
export type RelayErrorCode =
  | "VALIDATION"
  | "INVALID_TOKEN"
  | "EXPIRED_TOKEN"
  | "INVALID_CREDENTIALS"
  | "QUOTA_EXHAUSTED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "EMAIL_TAKEN"
  | "RATE_LIMITED"
  | "UPSTREAM_ERROR"
  | "UPSTREAM_UNAVAILABLE"
  | "INTERNAL"
  // v1.1+: returned by GET /v1/providers?rotate=false when the current
  // deviceId has no active device key. Client must retry the same call
  // without the query param to mint a fresh key.
  | "DEVICE_KEY_MISSING"
  // Synthetic codes the client invents when the body is missing/malformed.
  // Tagged so UI can tell "server told us X" from "we made this up locally".
  | "NETWORK_ERROR"
  | "UNKNOWN";

/** Shape we parse out of the response body before constructing the error. */
interface RelayErrorEnvelope {
  code?: string;
  message?: string;
  request_id?: string;
  // Some endpoints attach per-field validation hints, e.g.
  // VALIDATION → { fields: { email: "invalid format" } }
  fields?: Record<string, string>;
}

export class RelayApiError extends Error {
  readonly code: RelayErrorCode;
  readonly status: number;
  readonly requestId: string | null;
  /**
   * Parsed `Retry-After` seconds (only set for 429). Null when absent
   * or unparseable; callers treat null as "no hint, use sane default".
   */
  readonly retryAfter: number | null;
  readonly fields: Record<string, string> | null;

  constructor(opts: {
    code: RelayErrorCode;
    message: string;
    status: number;
    requestId: string | null;
    retryAfter: number | null;
    fields?: Record<string, string> | null;
  }) {
    super(opts.message);
    this.name = "RelayApiError";
    this.code = opts.code;
    this.status = opts.status;
    this.requestId = opts.requestId;
    this.retryAfter = opts.retryAfter;
    this.fields = opts.fields ?? null;
  }

  /**
   * Build a RelayApiError from a non-2xx fetch Response. Reads body
   * once via `.json()`; falls back to `.text()` and finally a synthetic
   * UNKNOWN code if the body is empty or not JSON.
   *
   * Marked async because Response.json() is async; callers await it
   * inside their catch / non-ok branch.
   */
  static async fromResponse(res: Response): Promise<RelayApiError> {
    const requestId = res.headers.get("X-Request-Id");
    const retryAfter = parseRetryAfter(res.headers.get("Retry-After"));

    let env: RelayErrorEnvelope = {};
    // We MUST be tolerant here: a 502 from a misbehaving upstream might
    // emit HTML, plaintext, or nothing at all. Any parse failure just
    // means we'll synthesise a code from the HTTP status below.
    try {
      env = (await res.json()) as RelayErrorEnvelope;
    } catch {
      env = {};
    }

    const code = normaliseCode(env.code, res.status);
    const message =
      env.message ??
      defaultMessageForStatus(res.status) ??
      `Relay API error (HTTP ${res.status})`;

    return new RelayApiError({
      code,
      message,
      status: res.status,
      requestId,
      retryAfter,
      fields: env.fields ?? null,
    });
  }

  /**
   * Build a synthetic NETWORK_ERROR for fetch failures (DNS, TLS,
   * timeout, abort). Distinct from server-returned envelopes so
   * UI can show "网络问题，请检查连接" instead of "凭证错误".
   */
  static network(reason: string): RelayApiError {
    return new RelayApiError({
      code: "NETWORK_ERROR",
      message: reason,
      status: 0,
      requestId: null,
      retryAfter: null,
    });
  }
}

/**
 * Validate a string against the known RelayErrorCode union; if it's
 * something the server invented after this build shipped, downgrade
 * to UNKNOWN rather than letting it leak through as an arbitrary
 * string and break exhaustive switches.
 *
 * Also infers a code from the HTTP status when the body is empty —
 * keeps the type contract intact for upstream 502/503 that some
 * proxies don't decorate with a body.
 */
function normaliseCode(raw: string | undefined, status: number): RelayErrorCode {
  if (raw && KNOWN_CODES.has(raw)) {
    return raw as RelayErrorCode;
  }
  // Status-based inference. Order matters: 401 needs special treatment
  // because the body sometimes distinguishes INVALID_TOKEN from
  // EXPIRED_TOKEN — when it doesn't, INVALID_TOKEN is the safer
  // default (it triggers a re-login UX, vs EXPIRED_TOKEN's silent
  // refresh which would loop if the refresh is also broken).
  switch (status) {
    case 400:
      return "VALIDATION";
    case 401:
      return "INVALID_TOKEN";
    case 402:
      return "QUOTA_EXHAUSTED";
    case 403:
      return "FORBIDDEN";
    case 404:
      return "NOT_FOUND";
    case 409:
      return "EMAIL_TAKEN";
    case 429:
      return "RATE_LIMITED";
    case 502:
      return "UPSTREAM_ERROR";
    case 503:
      return "UPSTREAM_UNAVAILABLE";
    case 500:
      return "INTERNAL";
    default:
      return "UNKNOWN";
  }
}

const KNOWN_CODES: ReadonlySet<string> = new Set([
  "VALIDATION",
  "INVALID_TOKEN",
  "EXPIRED_TOKEN",
  "INVALID_CREDENTIALS",
  "QUOTA_EXHAUSTED",
  "FORBIDDEN",
  "NOT_FOUND",
  "EMAIL_TAKEN",
  "RATE_LIMITED",
  "UPSTREAM_ERROR",
  "UPSTREAM_UNAVAILABLE",
  "INTERNAL",
  "DEVICE_KEY_MISSING",
]);

/**
 * Parse `Retry-After`. RFC 7231 allows either delta-seconds OR an HTTP
 * date; relay's 429 path uses delta-seconds per §5.3. We accept the
 * date form anyway to be forward-compatible — if someone fronts the
 * relay with Cloudflare it might emit the date form for upstream
 * 5xx rate limiting.
 */
function parseRetryAfter(header: string | null): number | null {
  if (!header) return null;
  // Numeric form: "17" → 17.
  const asInt = Number.parseInt(header, 10);
  if (Number.isFinite(asInt) && asInt >= 0 && header.trim() === String(asInt)) {
    return asInt;
  }
  // Date form: convert to delta in seconds, never negative.
  const parsed = Date.parse(header);
  if (Number.isFinite(parsed)) {
    const delta = Math.ceil((parsed - Date.now()) / 1000);
    return delta > 0 ? delta : 0;
  }
  return null;
}

/**
 * Human-readable fallback for the rare case where the relay sends a
 * non-JSON body. Chinese text matches the rest of the UI; the goal is
 * "user sees *something* useful" not "exact server wording".
 */
function defaultMessageForStatus(status: number): string | null {
  if (status >= 500) return "中转站服务暂时不可用，请稍后再试。";
  if (status === 401) return "登录状态已失效，请重新登录。";
  if (status === 402) return "账户余额不足。";
  if (status === 403) return "账户被禁用或权限不足。";
  if (status === 404) return "请求的资源不存在。";
  if (status === 409) return "邮箱已被注册。";
  if (status === 429) return "请求过于频繁，请稍后再试。";
  if (status >= 400) return "请求参数有误。";
  return null;
}
