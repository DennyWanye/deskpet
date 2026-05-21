/**
 * AccountSettingsPanel — relay-edition account management view.
 *
 * Shown in the Settings panel under an "账户" tab when the active
 * AuthAdapter is RelayAuthAdapter. Surfaces:
 *
 *   - User email + plan
 *   - Wallet balance (CN¥, derived from `balance.amount_minor / 100`)
 *   - This-month usage + reset date
 *   - Rate-limit ceiling
 *   - "修改密码"  — inline form, calls adapter.changePassword()
 *   - "设备管理" — external link to chinzy.com/console/devices
 *   - "退出登录" — calls adapter.logout(), parent should close panel
 *
 * Design constraints:
 *
 *   - No CSS imports — inline styles mirror AddProviderModal vocabulary.
 *   - Loading skeleton while /v1/usage/summary is in-flight, NOT a
 *     spinner — skeletons make the layout settle into its final shape
 *     immediately, which feels faster.
 *   - Network errors during getUsage() degrade gracefully: we still
 *     render the user info from `adapter.currentUser()` because that's
 *     already in memory.
 *   - "修改密码" deliberately stays in the same modal — opening a nested
 *     modal would obscure the panel and lose context.
 */
import React, { useEffect, useState } from "react";

import type { RelayAuthAdapter } from "./RelayAuthAdapter";
import { RelayApiError } from "./RelayApiError";
import { messageForCode } from "./RelayAuthModal";
import type { UsageSummary } from "./types";

interface AccountSettingsPanelProps {
  adapter: RelayAuthAdapter;
  /** Optional override for the device-management external URL. Defaults
   *  to the chinzy.com console page. Mostly here so the closed-source
   *  paid build can rebrand without forking this file. */
  deviceConsoleUrl?: string;
  /** Called when the user successfully logs out — parent typically
   *  closes the settings panel and shows the login modal. */
  onLoggedOut?(): void;
}

const DEFAULT_CONSOLE_URL = "https://chinzy.com/console/devices";

// ── Pure helpers (exported for vitest) ──────────────────────────

/**
 * Format `amount_minor` (CN¥ fen) as a human-readable string with the
 * yuan symbol and 2-decimal precision. Returns "—" for null/undefined
 * so the UI doesn't render "¥undefined".
 *
 *   formatCny(71317)  → "¥713.17"
 *   formatCny(0)      → "¥0.00"
 *   formatCny(null)   → "—"
 */
export function formatCny(amount_minor: number | null | undefined): string {
  if (amount_minor == null || !Number.isFinite(amount_minor)) return "—";
  const yuan = amount_minor / 100;
  return `¥${yuan.toFixed(2)}`;
}

/**
 * Render the period reset date in YYYY-MM-DD. Localised dates have too
 * many edge cases (12h/24h, am/pm) for a fixed-width readout — keep it
 * unambiguous and ISO-ish.
 */
export function formatResetDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * Validate a password-change submission. Mirrors the server-side rules
 * from relay v1.3 §3.5b: new password ≥ 8 chars, must differ from
 * current. Returning the literal error strings (not just booleans)
 * keeps the UI declarative — the component just renders whatever's
 * non-null.
 */
export interface ChangePasswordErrors {
  current?: string;
  next?: string;
  confirm?: string;
}

export function validatePasswordChange(input: {
  current: string;
  next: string;
  confirm: string;
}): ChangePasswordErrors {
  const errors: ChangePasswordErrors = {};
  if (!input.current) errors.current = "请输入当前密码";
  if (!input.next) {
    errors.next = "请输入新密码";
  } else if (input.next.length < 8) {
    errors.next = "新密码至少 8 位";
  } else if (input.next === input.current) {
    errors.next = "新密码不能与当前密码相同";
  }
  if (!input.confirm) {
    errors.confirm = "请再次输入新密码";
  } else if (input.confirm !== input.next) {
    errors.confirm = "两次输入的新密码不一致";
  }
  return errors;
}

// ── Component ─────────────────────────────────────────────────────

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; usage: UsageSummary | null }
  | { kind: "error"; message: string };

export function AccountSettingsPanel({
  adapter,
  deviceConsoleUrl = DEFAULT_CONSOLE_URL,
  onLoggedOut,
}: AccountSettingsPanelProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [user, setUser] = useState(adapter.currentUser());
  const [showPwForm, setShowPwForm] = useState(false);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const usage = await adapter.getUsage();
        if (!mounted) return;
        setState({ kind: "ready", usage });
      } catch (err) {
        if (!mounted) return;
        const msg =
          err instanceof RelayApiError
            ? messageForCode(err.code, err.message)
            : err instanceof Error
              ? err.message
              : "无法加载账户信息";
        setState({ kind: "error", message: msg });
      }
    })();
    return () => {
      mounted = false;
    };
  }, [adapter]);

  useEffect(() => {
    const unsub = adapter.onEvent((e) => {
      if (e.type === "login") setUser(e.user);
      if (e.type === "logout") setUser(null);
      if (e.type === "usage-updated")
        setState({ kind: "ready", usage: e.usage });
    });
    return unsub;
  }, [adapter]);

  if (!user) {
    return (
      <div style={rootStyle}>
        <p style={mutedStyle}>未登录。请先登录中转账户。</p>
      </div>
    );
  }

  const handleLogout = async () => {
    try {
      await adapter.logout();
    } finally {
      onLoggedOut?.();
    }
  };

  return (
    <div style={rootStyle}>
      <header style={headerStyle}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{user.email}</div>
          <div style={mutedStyle}>
            {user.plan ?? "prepaid"}
            {user.id ? ` · #${user.id.slice(-6)}` : ""}
          </div>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          style={secondaryBtnStyle}
          data-testid="account-logout-btn"
        >
          退出登录
        </button>
      </header>

      <section style={sectionStyle}>
        <h4 style={sectionTitleStyle}>账户余额</h4>
        {state.kind === "loading" && <SkeletonRow lines={3} />}
        {state.kind === "error" && (
          <div style={{ ...mutedStyle, color: "#b91c1c" }}>{state.message}</div>
        )}
        {state.kind === "ready" && (
          <UsageDetails usage={state.usage} />
        )}
      </section>

      <section style={sectionStyle}>
        <h4 style={sectionTitleStyle}>账户安全</h4>
        {!showPwForm ? (
          <button
            type="button"
            onClick={() => setShowPwForm(true)}
            style={secondaryBtnStyle}
            data-testid="account-change-password-btn"
          >
            修改密码
          </button>
        ) : (
          <ChangePasswordForm
            adapter={adapter}
            onCancel={() => setShowPwForm(false)}
            onSuccess={() => setShowPwForm(false)}
          />
        )}
      </section>

      <section style={sectionStyle}>
        <h4 style={sectionTitleStyle}>已登录设备</h4>
        <p style={mutedStyle}>
          多设备管理在中转站控制台。打开后可撤销其他设备的访问。
        </p>
        <a
          href={deviceConsoleUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={linkBtnStyle}
        >
          打开设备管理 →
        </a>
      </section>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────

function UsageDetails({ usage }: { usage: UsageSummary | null }) {
  if (!usage) {
    return <div style={mutedStyle}>暂无用量数据。</div>;
  }
  return (
    <dl style={dlStyle}>
      <dt style={dtStyle}>钱包余额</dt>
      <dd style={ddStyle}>
        {formatCny(usage.balance?.amount_minor)}{" "}
        {usage.balance?.currency && usage.balance.currency !== "CNY" && (
          <span style={mutedStyle}>({usage.balance.currency})</span>
        )}
      </dd>

      <dt style={dtStyle}>本月已用</dt>
      <dd style={ddStyle}>{formatCny(usage.period?.used_minor)}</dd>

      <dt style={dtStyle}>下次重置</dt>
      <dd style={ddStyle}>{formatResetDate(usage.period?.reset_at)}</dd>

      <dt style={dtStyle}>速率上限</dt>
      <dd style={ddStyle}>
        {usage.rate_limit?.rpm != null
          ? `${usage.rate_limit.rpm} req/min`
          : "—"}
      </dd>
    </dl>
  );
}

function SkeletonRow({ lines }: { lines: number }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          style={{
            height: 12,
            background: "#f1f5f9",
            borderRadius: 4,
            width: `${60 + ((i * 13) % 40)}%`,
          }}
        />
      ))}
    </div>
  );
}

function ChangePasswordForm({
  adapter,
  onCancel,
  onSuccess,
}: {
  adapter: RelayAuthAdapter;
  onCancel(): void;
  onSuccess(): void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [errors, setErrors] = useState<ChangePasswordErrors>({});
  const [formErr, setFormErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    setFormErr(null);
    const local = validatePasswordChange({ current, next, confirm });
    setErrors(local);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    try {
      await adapter.changePassword(current, next);
      setOkMsg(
        "密码已修改。其他设备需要重新登录；当前会话将在 1 小时内自然过期。",
      );
      // Give the user a beat to read the success message before
      // collapsing the form.
      setTimeout(onSuccess, 1800);
    } catch (err) {
      if (err instanceof RelayApiError) {
        setFormErr(messageForCode(err.code, err.message));
      } else if (err instanceof Error) {
        setFormErr(err.message);
      } else {
        setFormErr("修改失败，请稍后再试。");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ display: "grid", gap: 6 }}>
      <label style={fieldStyle}>
        <span>当前密码</span>
        <input
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          disabled={busy}
          style={inputStyle}
          data-testid="account-current-password"
        />
        {submitted && errors.current && (
          <span style={errStyle}>{errors.current}</span>
        )}
      </label>
      <label style={fieldStyle}>
        <span>新密码</span>
        <input
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          disabled={busy}
          style={inputStyle}
          data-testid="account-new-password"
        />
        {submitted && errors.next && (
          <span style={errStyle}>{errors.next}</span>
        )}
      </label>
      <label style={fieldStyle}>
        <span>确认新密码</span>
        <input
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          disabled={busy}
          style={inputStyle}
          data-testid="account-confirm-password"
        />
        {submitted && errors.confirm && (
          <span style={errStyle}>{errors.confirm}</span>
        )}
      </label>
      {formErr && (
        <div role="alert" style={alertStyle}>
          {formErr}
        </div>
      )}
      {okMsg && (
        <div
          role="status"
          style={{
            ...alertStyle,
            background: "#ecfdf5",
            border: "1px solid #a7f3d0",
            color: "#065f46",
          }}
        >
          {okMsg}
        </div>
      )}
      <div style={{ display: "flex", gap: 6 }}>
        <button
          type="submit"
          disabled={busy}
          style={primaryBtnStyle}
          data-testid="account-submit-password"
        >
          {busy ? "正在修改..." : "确认修改"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          style={secondaryBtnStyle}
        >
          取消
        </button>
      </div>
    </form>
  );
}

// ── inline styles ─────────────────────────────────────────────────

const rootStyle: React.CSSProperties = {
  display: "grid",
  gap: 16,
  padding: 12,
  color: "#111",
  fontSize: 12,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 8,
  paddingBottom: 8,
  borderBottom: "1px solid #e5e7eb",
};

const sectionStyle: React.CSSProperties = {
  display: "grid",
  gap: 6,
};

const sectionTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 12,
  fontWeight: 600,
  color: "#374151",
};

const mutedStyle: React.CSSProperties = {
  color: "#6b7280",
  fontSize: 11,
};

const fieldStyle: React.CSSProperties = {
  display: "grid",
  gap: 4,
};

const inputStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  fontSize: 12,
  fontFamily: "inherit",
  outline: "none",
};

const errStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#b91c1c",
};

const alertStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#b91c1c",
  background: "#fef2f2",
  padding: "6px 8px",
  borderRadius: 4,
  border: "1px solid #fecaca",
};

const dlStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "auto 1fr",
  columnGap: 12,
  rowGap: 4,
  margin: 0,
  fontSize: 12,
};

const dtStyle: React.CSSProperties = {
  color: "#6b7280",
};

const ddStyle: React.CSSProperties = {
  margin: 0,
  color: "#111",
  fontVariantNumeric: "tabular-nums",
};

const primaryBtnStyle: React.CSSProperties = {
  padding: "6px 12px",
  borderRadius: 4,
  border: "none",
  background: "#2563eb",
  color: "white",
  fontSize: 12,
  cursor: "pointer",
};

const secondaryBtnStyle: React.CSSProperties = {
  padding: "5px 10px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  background: "white",
  color: "#374151",
  fontSize: 12,
  cursor: "pointer",
};

const linkBtnStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#2563eb",
  textDecoration: "underline",
};
