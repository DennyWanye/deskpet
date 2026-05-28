// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * RelayEdition — the entire relay-edition UI surface, wrapped so App.tsx
 * only conditionally mounts ONE component.
 *
 * Why this exists:
 *   - OSS default (`VITE_AUTH_EDITION=manual`) must not see a single
 *     byte of relay UI logic. Putting all of it behind an `id === "relay"`
 *     gate in App.tsx would scatter conditionals across the render tree;
 *     instead App.tsx just renders `<RelayEdition adapter={…} />` when
 *     the active adapter is RelayAuthAdapter, and this file owns the rest.
 *   - The closed-source paid build can swap this file out wholesale
 *     without touching App.tsx — useful when we add billing dashboards
 *     / OAuth / device drawers later.
 *
 * What it does:
 *   1. On mount, calls `adapter.restoreSession()` once.
 *   2. While the user isn't authenticated, renders `<RelayAuthModal />`
 *      as a centered overlay over the pet window.
 *   3. Once authenticated, renders a small floating pill ("👤 账户")
 *      in the top-left. Clicking it opens `<AccountSettingsPanel />`
 *      as another modal.
 *   4. Listens to login/logout events on the adapter to keep its own
 *      "is authed" state in sync with whatever the adapter says.
 *
 * The whole component is a no-op except for an initial useEffect when
 * the adapter has already restored the session and the pill is closed,
 * so it has near-zero render cost.
 */
import React, { useCallback, useEffect, useState } from "react";

import { AccountSettingsPanel } from "./AccountSettingsPanel";
import type { RelayAuthAdapter } from "./RelayAuthAdapter";
import { RelayAuthModal } from "./RelayAuthModal";
import { relayProviderBridge, type BridgeStatus } from "./relayProviderBridge";

interface RelayEditionProps {
  adapter: RelayAuthAdapter;
  /** Optional brand string for the login modal header. Defaults to
   *  "Token Relay". Closed-source paid build sets its own. */
  brandName?: string;
  /** 2026-05-26: 暴露 "打开账户面板" 的触发器给外部（Toolbar）。挂载时
   * `current` 被写入 setter，卸载时清空。Toolbar 通过这个 ref 触发，pill
   * 不再由 RelayEdition 自渲染（避免跟 Toolbar 视觉冲突）。 */
  openAccountRef?: React.MutableRefObject<(() => void) | null>;
}

export function RelayEdition({ adapter, brandName, openAccountRef }: RelayEditionProps) {
  const [authed, setAuthed] = useState(adapter.isAuthenticated());
  const [bootDone, setBootDone] = useState(false);
  const [showAccount, setShowAccount] = useState(false);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatus>(
    relayProviderBridge.status(),
  );

  // WI-R2: kick a /v1/providers fetch. Its `providers-updated` event
  // drives relayProviderBridge → backend LLM endpoint. Fire-and-forget;
  // failures surface via the bridge status banner.
  const refreshProviders = useCallback(() => {
    adapter.listProviders().catch((err) => {
      console.warn("[RelayEdition] listProviders failed:", err);
    });
  }, [adapter]);

  // 1. Boot: restore session once. Guarded by `bootDone` so HMR /
  //    StrictMode-double-mount doesn't kick a second /v1/me call.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const restored = await adapter.restoreSession();
        // WI-R2: already-logged-in cold start → push provider to backend.
        if (restored && !cancelled) refreshProviders();
      } finally {
        if (!cancelled) {
          setAuthed(adapter.isAuthenticated());
          setBootDone(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [adapter, refreshProviders]);

  // 2. Stay in sync with adapter lifecycle events (logout from
  //    AccountSettingsPanel, /refresh failure on a background ws
  //    call, etc).
  useEffect(() => {
    const unsub = adapter.onEvent((e) => {
      if (e.type === "login") {
        setAuthed(true);
        // WI-R2: fresh login → fetch providers → bridge to backend.
        refreshProviders();
      }
      if (e.type === "logout") {
        setAuthed(false);
        setShowAccount(false);
      }
      // WI-R2: every /v1/providers result (incl. key rotation) flows
      // through the bridge to the backend LLM endpoint.
      if (e.type === "providers-updated") {
        void relayProviderBridge.apply(e.providers);
      }
    });
    return unsub;
  }, [adapter, refreshProviders]);

  // 3. WI-R2: surface bridge failures — a login that succeeds but whose
  //    provider push fails would otherwise look "logged in" yet every
  //    chat 401s. Show a retry banner instead of failing silently.
  useEffect(() => {
    return relayProviderBridge.onStatus(setBridgeStatus);
  }, []);

  // 2026-05-26: 自动重试 bridge error。最常见原因 —— 前端比 backend 早
  // 启动 ~10s，第一次推 provider 时 backend 还没监听 8100 端口。退避重试
  // 3/6/12/24s（max 4 次），每次成功就停。用户不再需要手动点"点此重试"。
  useEffect(() => {
    if (bridgeStatus !== "error" || !authed) return;
    let attempt = 0;
    const delays = [3000, 6000, 12000, 24000];
    let cancel = false;
    let timer: number | null = null;
    const tick = () => {
      if (cancel || attempt >= delays.length) return;
      const d = delays[attempt++];
      timer = window.setTimeout(() => {
        if (cancel) return;
        console.log(`[RelayEdition] bridge auto-retry attempt ${attempt}/${delays.length}`);
        refreshProviders();
        // refreshProviders 异步；如果还是 error，本 useEffect 不会再触发
        // （bridgeStatus 没变），但内部 setStatus("applying") 会变 ok/error 后
        // 再次进入 effect 重启序列 —— 所以这里也启下一个 timer 作为保险。
        tick();
      }, d);
    };
    tick();
    return () => {
      cancel = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [bridgeStatus, authed, refreshProviders]);

  // 2026-05-26: 把"打开账户面板"的方法暴露给外部 ref（App→Toolbar）。
  useEffect(() => {
    if (!openAccountRef) return;
    openAccountRef.current = () => setShowAccount(true);
    return () => {
      openAccountRef.current = null;
    };
  }, [openAccountRef]);

  // Don't flash the login modal during the initial restoreSession
  // round-trip — it's the worst possible first impression for a user
  // who's actually already logged in.
  if (!bootDone) return null;

  if (!authed) {
    return (
      <RelayAuthModal
        open={true}
        adapter={adapter}
        brandName={brandName}
        onClose={(success) => {
          if (success) setAuthed(true);
          // If not success: keep the modal open. The user shouldn't be
          // able to dismiss the login flow in the relay edition because
          // there's no other way for them to use the app.
        }}
      />
    );
  }

  return (
    <>
      {/* 2026-05-26: pill 按钮挪到 Toolbar 作为 Group 0 第一个图标，
          视觉跟其它面板入口统一。RelayEdition 只渲染 modal + banner。 */}
      {bridgeStatus === "error" && (
        <div data-testid="relay-bridge-error" style={bridgeErrorStyle}>
          <span>模型配置失败</span>
          <button
            type="button"
            data-testid="relay-bridge-retry"
            onClick={refreshProviders}
            style={bridgeRetryBtnStyle}
          >
            点此重试
          </button>
        </div>
      )}
      {showAccount && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="账户设置"
          style={overlayStyle}
          onClick={(e) => {
            if (e.target === e.currentTarget) setShowAccount(false);
          }}
        >
          <div style={cardStyle}>
            <header style={cardHeaderStyle}>
              <h3 style={{ margin: 0, fontSize: 14 }}>账户设置</h3>
              <button
                type="button"
                onClick={() => setShowAccount(false)}
                style={closeBtnStyle}
                aria-label="关闭"
              >
                ✕
              </button>
            </header>
            <AccountSettingsPanel
              adapter={adapter}
              onLoggedOut={() => {
                setShowAccount(false);
                // authed already flips via the logout event listener
                // above — no manual setAuthed(false) needed.
              }}
            />
          </div>
        </div>
      )}
    </>
  );
}

// ── inline styles ─────────────────────────────────────────────────

const bridgeErrorStyle: React.CSSProperties = {
  position: "fixed",
  top: 42,
  left: 8,
  zIndex: 1100,
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "4px 8px",
  borderRadius: 6,
  background: "rgba(185, 28, 28, 0.92)",
  color: "white",
  fontSize: 11,
  boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
};

const bridgeRetryBtnStyle: React.CSSProperties = {
  background: "rgba(255,255,255,0.18)",
  border: "1px solid rgba(255,255,255,0.35)",
  borderRadius: 4,
  color: "white",
  fontSize: 11,
  padding: "1px 6px",
  cursor: "pointer",
};

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.5)",
  display: "grid",
  placeItems: "center",
  padding: 8,
  zIndex: 1300,
};

const cardStyle: React.CSSProperties = {
  background: "white",
  borderRadius: 8,
  width: "min(94vw, 420px)",
  maxHeight: "92vh",
  overflowY: "auto",
  boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
  color: "#111",
};

const cardHeaderStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "10px 14px",
  borderBottom: "1px solid #e5e7eb",
};

const closeBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  fontSize: 14,
  cursor: "pointer",
};
