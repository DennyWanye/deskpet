/**
 * RelayAuthModal — combined login + register modal for the relay edition.
 *
 * Single component with a `mode` toggle to avoid the awkward "modal
 * inside modal" pattern when users want to switch from login to register
 * mid-flow. Style matches AddProviderModal so the modal vocabulary
 * stays consistent across the app.
 *
 * What it does NOT do (yet):
 *   - Forgot password — relay v1.3 has no public endpoint for it. The
 *     button is rendered but disabled with a tooltip.
 *   - Email verification — v1.3 made registration immediately ACTIVE,
 *     so no "check your inbox" state is needed. Legacy PENDING accounts
 *     are still handled inside RelayAuthAdapter.register().
 *   - OAuth third-party login — relay followup #3.
 *
 * Wiring:
 *   - Parent passes `adapter`, `open`, `onClose`. On successful login
 *     or register, this component calls `onClose(true)` so parent can
 *     react (e.g. close modal + refresh provider list).
 *   - All error mapping goes through `messageForCode()` — single
 *     translation table for relay error codes → user-facing Chinese.
 */
import React, { useEffect, useState } from "react";

import type { RelayAuthAdapter } from "./RelayAuthAdapter";
import { RelayApiError, type RelayErrorCode } from "./RelayApiError";

export type RelayAuthMode = "login" | "register";

interface RelayAuthModalProps {
  open: boolean;
  /** Concrete adapter — parent is expected to have `getAuthAdapter()`
   *  return a `RelayAuthAdapter` when this modal renders. Typing as
   *  the concrete class instead of `AuthAdapter` lets us assume the
   *  contract (e.g. login throws RelayApiError, not NotSupportedError). */
  adapter: RelayAuthAdapter;
  /** Initial mode the modal opens in. Defaults to "login". */
  initialMode?: RelayAuthMode;
  /** Called when the modal closes. `success=true` means the user is
   *  now authenticated; parent should refresh anything that depends
   *  on auth state (e.g. provider list). */
  onClose(success: boolean): void;
  /** Brand strings — keep the inner component edition-agnostic so the
   *  closed-source paid build can theme this modal without forking. */
  brandName?: string;
  /** Optional URL placeholders shown next to the ToS checkbox. */
  termsUrl?: string;
  privacyUrl?: string;
}

const DEFAULT_TERMS = "https://chinzy.com/legal/terms";
const DEFAULT_PRIVACY = "https://chinzy.com/legal/privacy";

// ── Validation ────────────────────────────────────────────────────

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface FieldErrors {
  email?: string;
  password?: string;
  confirm?: string;
  terms?: string;
  /** Top-of-form error string (server-side errors land here). */
  form?: string;
  /** When the relay returns a 429, we surface the Retry-After seconds
   *  so the UI can show a countdown / disabled state. */
  retryAfter?: number;
}

/** Pure validator — exported for vitest, called from the component. */
export function validateAuthForm(input: {
  mode: RelayAuthMode;
  email: string;
  password: string;
  confirm: string;
  termsAccepted: boolean;
}): FieldErrors {
  const errors: FieldErrors = {};
  if (!input.email.trim()) {
    errors.email = "请输入邮箱";
  } else if (!EMAIL_RE.test(input.email.trim())) {
    errors.email = "邮箱格式不正确";
  }
  if (!input.password) {
    errors.password = "请输入密码";
  } else if (input.password.length < 8) {
    errors.password = "密码至少 8 位";
  }
  if (input.mode === "register") {
    if (!input.confirm) {
      errors.confirm = "请再次输入密码";
    } else if (input.confirm !== input.password) {
      errors.confirm = "两次输入的密码不一致";
    }
    if (!input.termsAccepted) {
      errors.terms = "请先同意用户协议与隐私政策";
    }
  }
  return errors;
}

/**
 * Map relay error codes to user-facing Chinese messages. Exported for
 * vitest and so the closed-source paid build can override individual
 * strings (currently no override hook — just re-export and replace
 * in that build's module-resolution layer).
 */
export function messageForCode(code: RelayErrorCode, fallback?: string): string {
  switch (code) {
    case "INVALID_CREDENTIALS":
      return "邮箱或密码错误。";
    case "EMAIL_TAKEN":
      return "该邮箱已被注册，请直接登录或使用其他邮箱。";
    case "VALIDATION":
      return "请检查表单内容是否填写正确。";
    case "RATE_LIMITED":
      return "请求过于频繁，请稍后再试。";
    case "FORBIDDEN":
      return "账号被禁用或受限，请联系客服。";
    case "QUOTA_EXHAUSTED":
      return "账户余额不足。";
    case "INVALID_TOKEN":
    case "EXPIRED_TOKEN":
      return "登录状态已失效，请重新登录。";
    case "NETWORK_ERROR":
      return "网络连接失败，请检查网络后重试。";
    case "UPSTREAM_ERROR":
    case "UPSTREAM_UNAVAILABLE":
      return "中转站服务暂时不可用，请稍后再试。";
    case "INTERNAL":
      return "服务器出错了，请稍后再试。";
    case "NOT_FOUND":
      return "请求的资源不存在。";
    case "DEVICE_KEY_MISSING":
      return "设备密钥不可用，请重试。";
    case "UNKNOWN":
    default:
      return fallback ?? "出错了，请稍后再试。";
  }
}

// ── Component ─────────────────────────────────────────────────────

export function RelayAuthModal({
  open,
  adapter,
  initialMode = "login",
  onClose,
  brandName = "Token Relay",
  termsUrl = DEFAULT_TERMS,
  privacyUrl = DEFAULT_PRIVACY,
}: RelayAuthModalProps) {
  const [mode, setMode] = useState<RelayAuthMode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [terms, setTerms] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  // Reset transient state every time the modal opens — otherwise the
  // last failure stays visible if the user closes + reopens.
  useEffect(() => {
    if (open) {
      setMode(initialMode);
      setSubmitted(false);
      setErrors({});
      setBusy(false);
    }
  }, [open, initialMode]);

  if (!open) return null;

  const swapMode = (next: RelayAuthMode) => {
    setMode(next);
    setSubmitted(false);
    setErrors({});
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    const local = validateAuthForm({
      mode,
      email,
      password,
      confirm,
      termsAccepted: terms,
    });
    setErrors(local);
    if (Object.keys(local).length > 0) return;

    setBusy(true);
    try {
      if (mode === "login") {
        await adapter.login({ email: email.trim(), password });
      } else {
        await adapter.register({ email: email.trim(), password });
      }
      onClose(true);
    } catch (err) {
      const next: FieldErrors = {};
      if (err instanceof RelayApiError) {
        next.form = messageForCode(err.code, err.message);
        if (err.code === "RATE_LIMITED" && err.retryAfter != null) {
          next.retryAfter = err.retryAfter;
        }
        // Move EMAIL_TAKEN onto the email field — UX nicety so the
        // user sees the red text right next to what they need to fix.
        if (err.code === "EMAIL_TAKEN") {
          next.email = next.form;
          next.form = undefined;
        }
      } else {
        next.form =
          err instanceof Error ? err.message : "出错了，请稍后再试。";
      }
      setErrors(next);
    } finally {
      setBusy(false);
    }
  };

  const showConfirm = mode === "register";
  const showTerms = mode === "register";
  const submitLabel = mode === "login" ? "登录" : "注册";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={mode === "login" ? "登录" : "注册"}
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose(false);
      }}
    >
      <form style={modalStyle} onSubmit={handleSubmit}>
        <header
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <h3 style={{ margin: 0, fontSize: 14 }}>
            {brandName} — {mode === "login" ? "登录" : "注册"}
          </h3>
          <button
            type="button"
            disabled={busy}
            onClick={() => onClose(false)}
            style={{
              background: "transparent",
              border: "none",
              fontSize: 14,
              cursor: busy ? "not-allowed" : "pointer",
              opacity: busy ? 0.4 : 1,
            }}
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        <label style={fieldStyle}>
          <span>邮箱</span>
          <input
            data-testid="relay-email-input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={busy}
            placeholder="alice@example.com"
            style={inputStyle}
          />
          {submitted && errors.email && (
            <span style={errStyle}>{errors.email}</span>
          )}
        </label>

        <label style={fieldStyle}>
          <span>密码</span>
          <input
            data-testid="relay-password-input"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
            placeholder="至少 8 位"
            style={inputStyle}
          />
          {submitted && errors.password && (
            <span style={errStyle}>{errors.password}</span>
          )}
        </label>

        {showConfirm && (
          <label style={fieldStyle}>
            <span>确认密码</span>
            <input
              data-testid="relay-confirm-input"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={busy}
              style={inputStyle}
            />
            {submitted && errors.confirm && (
              <span style={errStyle}>{errors.confirm}</span>
            )}
          </label>
        )}

        {showTerms && (
          <label
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 6,
              fontSize: 11,
              lineHeight: 1.4,
            }}
          >
            <input
              data-testid="relay-terms-checkbox"
              type="checkbox"
              checked={terms}
              onChange={(e) => setTerms(e.target.checked)}
              disabled={busy}
              style={{ marginTop: 2 }}
            />
            <span>
              我已阅读并同意
              <a
                href={termsUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={linkStyle}
              >
                《用户协议》
              </a>
              与
              <a
                href={privacyUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={linkStyle}
              >
                《隐私政策》
              </a>
              。
            </span>
          </label>
        )}
        {submitted && errors.terms && (
          <span style={errStyle}>{errors.terms}</span>
        )}

        {errors.form && (
          <div
            role="alert"
            style={{
              ...errStyle,
              background: "#fef2f2",
              padding: "6px 8px",
              borderRadius: 4,
              border: "1px solid #fecaca",
            }}
          >
            {errors.form}
            {errors.retryAfter != null && (
              <> 重试间隔约 {errors.retryAfter} 秒。</>
            )}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          data-testid="relay-submit-button"
          style={{
            ...saveBtn,
            opacity: busy ? 0.6 : 1,
            cursor: busy ? "wait" : "pointer",
          }}
        >
          {busy ? "请稍候..." : submitLabel}
        </button>

        <footer
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            fontSize: 11,
            color: "#4b5563",
          }}
        >
          {mode === "login" ? (
            <>
              <button
                type="button"
                onClick={() => swapMode("register")}
                style={linkButtonStyle}
                disabled={busy}
              >
                还没有账号？注册
              </button>
              <span
                title="暂未开放——中转站邮件基础设施上线后将启用"
                style={{ color: "#9ca3af", cursor: "not-allowed" }}
              >
                忘记密码？
              </span>
            </>
          ) : (
            <button
              type="button"
              onClick={() => swapMode("login")}
              style={linkButtonStyle}
              disabled={busy}
            >
              已有账号？登录
            </button>
          )}
        </footer>
      </form>
    </div>
  );
}

// ── inline styles (mirror AddProviderModal vocabulary) ────────────

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.5)",
  display: "grid",
  placeItems: "center",
  padding: 8,
  zIndex: 1200,
};

const modalStyle: React.CSSProperties = {
  background: "white",
  padding: 16,
  borderRadius: 8,
  width: "min(94vw, 380px)",
  maxHeight: "92vh",
  overflowY: "auto",
  color: "#111",
  boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
  display: "grid",
  gap: 8,
};

const fieldStyle: React.CSSProperties = {
  display: "grid",
  gap: 4,
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  fontSize: 12,
  fontFamily: "inherit",
  outline: "none",
  minWidth: 0,
};

const errStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#b91c1c",
};

const saveBtn: React.CSSProperties = {
  padding: "8px 12px",
  borderRadius: 4,
  border: "none",
  background: "#2563eb",
  color: "white",
  fontSize: 13,
  fontWeight: 500,
  marginTop: 4,
};

const linkButtonStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#2563eb",
  cursor: "pointer",
  padding: 0,
  fontSize: 11,
  textDecoration: "underline",
};

const linkStyle: React.CSSProperties = {
  color: "#2563eb",
  textDecoration: "underline",
};
