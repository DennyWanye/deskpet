/**
 * P2-1-S3: SettingsPanel —— 云端账号 / 路由策略 / 今日使用（占位）。
 *
 * Three sections, all controlled-input. Save flow:
 *   1. If user typed a new apiKey, invoke Rust `set_cloud_api_key` (keyring).
 *   2. Persist strategy / budget to backend config: deferred — S6 owns the
 *      backend-side strategy switching, S8 owns the daily-budget ledger.
 *      For S3 we just keep them in component state so the UI is honest
 *      about what works right now.
 *
 * 测试连接 path uses the control WS (already authenticated via shared
 * secret) so the apiKey never touches an HTTP endpoint and never lands
 * in a network log.
 *
 * The 今日使用 section reads from `fetchDailyBudget`, which round-trips
 * through the control WS to the BillingLedger (S8). The DailyBudgetStatus
 * contract (snake_case fields) is frozen in types/messages.ts.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteCloudApiKey,
  hasCloudApiKey,
  setCloudApiKey,
} from "../bindings/secrets";
import { updateCloudConfig } from "../bindings/config";
import type {
  DailyBudgetStatus,
  IncomingMessage,
  ProviderTestConnectionRequest,
  ProviderTestConnectionResult,
} from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";

const DEFAULT_BASE_URL =
  "https://dashscope.aliyuncs.com/compatible-mode/v1";
const DEFAULT_MODEL = "qwen3.6-plus";

// P2-1 前端持久化 key；P2-2 会迁移到 config.toml 服务端写，届时移除这三个。
const LS_BASE_URL = "deskpet.cloud.baseUrl";
const LS_MODEL = "deskpet.cloud.model";
const LS_STRATEGY = "deskpet.router.strategy";

type Strategy = "local_first" | "cloud_first" | "cost_aware" | "latency_aware";

const DEFAULT_STRATEGY: Strategy = "cloud_first";
const VALID_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>([
  "local_first",
  "cloud_first",
  "cost_aware",
  "latency_aware",
]);

/**
 * P2-1 前端持久化：读取 localStorage，读不到/异常时回退默认值。
 * P2-2 迁移到 config.toml 服务端写后，此辅助会被删除。
 */
function readLS(key: string, fallback: string): string {
  try {
    const v = localStorage.getItem(key);
    return v !== null && v !== "" ? v : fallback;
  } catch {
    return fallback;
  }
}

function readStrategyLS(): Strategy {
  try {
    const v = localStorage.getItem(LS_STRATEGY);
    if (v && VALID_STRATEGIES.has(v as Strategy)) return v as Strategy;
  } catch {
    /* noop */
  }
  return DEFAULT_STRATEGY;
}

const STRATEGY_LABELS: Record<Strategy, string> = {
  local_first: "local_first（本地优先）",
  cloud_first: "cloud_first（云端优先）",
  cost_aware: "cost_aware（成本最优）",
  latency_aware: "latency_aware（延迟最优）",
};

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
  /** Accessor so the component can both `send` and subscribe without
   * recreating subscriptions every parent render. */
  getChannel: () => ControlChannel | null;
  /** The most recent incoming control message — we narrow to our reply
   * type inside an effect. Piggybacking the existing App-level state
   * avoids an extra onMessage listener that'd need manual teardown. */
  lastMessage: IncomingMessage | null;
  secret: string;
  onConfigChanged?: () => void;
}

/**
 * Send a `budget_status` request on the control channel and resolve with the
 * next `budget_status` reply (or reject after `timeoutMs`).
 *
 * P2-1-S8: replaced S3's hardcoded stub with the real control-WS roundtrip.
 * The DailyBudgetStatus contract (snake_case fields) is the cross-slice
 * import point locked in spec §1.3.
 */
export async function fetchDailyBudget(
  channel: ControlChannel,
  timeoutMs = 3000,
): Promise<DailyBudgetStatus> {
  return new Promise<DailyBudgetStatus>((resolve, reject) => {
    const timer = setTimeout(() => {
      unsub();
      reject(new Error("budget_status timeout"));
    }, timeoutMs);
    const unsub = channel.onMessage((msg: IncomingMessage) => {
      if (msg.type === "budget_status") {
        clearTimeout(timer);
        unsub();
        resolve(msg.payload);
      }
    });
    channel.send({ type: "budget_status" });
  });
}

export function SettingsPanel({
  open,
  onClose,
  getChannel,
  lastMessage,
  secret,
  onConfigChanged,
}: SettingsPanelProps) {
  // ----- Cloud account section -----------------------------------------------
  // Lazy init from localStorage so refresh 不丢用户编辑的 baseUrl/model。
  // P2-2 迁移到 config.toml 服务端写后，initializer 会换成 props 注入。
  const [baseUrl, setBaseUrl] = useState<string>(() =>
    readLS(LS_BASE_URL, DEFAULT_BASE_URL),
  );
  const [model, setModel] = useState<string>(() =>
    readLS(LS_MODEL, DEFAULT_MODEL),
  );
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [hasKey, setHasKey] = useState(false);
  const [testStatus, setTestStatus] = useState<
    "idle" | "pending" | "ok" | "fail"
  >("idle");
  const [testMessage, setTestMessage] = useState<string>("");

  // ----- Routing strategy section --------------------------------------------
  const [strategy, setStrategy] = useState<Strategy>(() => readStrategyLS());

  // ----- Daily budget section ------------------------------------------------
  const [budget, setBudget] = useState<DailyBudgetStatus | null>(null);
  const [budgetError, setBudgetError] = useState<string | null>(null);

  // ----- Save / error state --------------------------------------------------
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Track the pending test so late-arriving replies don't overwrite a
  // newer state (e.g. user clicked test twice quickly).
  const pendingTestRef = useRef(0);

  // Refresh has-key status + daily budget every time the panel opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    void (async () => {
      try {
        const saved = await hasCloudApiKey();
        if (!cancelled) setHasKey(saved);
      } catch (e) {
        // keyring unavailable (rare — e.g. Linux w/o Secret Service).
        // Non-fatal: the input still works for *this* session.
        if (!cancelled) {
          setHasKey(false);
          console.warn("[SettingsPanel] hasCloudApiKey failed:", e);
        }
      }
      try {
        const ch = getChannel();
        if (!ch) throw new Error("控制通道未连接");
        const b = await fetchDailyBudget(ch);
        if (!cancelled) {
          setBudget(b);
          setBudgetError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setBudget(null);
          setBudgetError(String(e));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, getChannel]);

  // Listen for provider_test_connection_result on the shared control channel.
  useEffect(() => {
    if (!lastMessage) return;
    if (lastMessage.type !== "provider_test_connection_result") return;
    const r = lastMessage as ProviderTestConnectionResult;
    // Only accept if we actually have a test in flight.
    if (pendingTestRef.current === 0) return;
    pendingTestRef.current = 0;
    if (r.payload.ok) {
      setTestStatus("ok");
      setTestMessage(
        `连接成功${r.payload.tested_url ? ` (${r.payload.tested_url})` : ""}`,
      );
    } else {
      setTestStatus("fail");
      setTestMessage(`失败: ${r.payload.error ?? "unknown"}`);
    }
  }, [lastMessage]);

  const handleTestConnection = useCallback(() => {
    const channel = getChannel();
    if (!channel || channel.state !== "connected") {
      setTestStatus("fail");
      setTestMessage("控制通道未连接");
      return;
    }
    if (!apiKeyInput.trim()) {
      // Can't send the *saved* key from Rust through the WS without
      // exposing it to JS; forcing the user to retype keeps the key
      // off the renderer process at rest.
      setTestStatus("fail");
      setTestMessage("请先输入 apiKey 再测试（不会读取已保存的 key）");
      return;
    }
    setTestStatus("pending");
    setTestMessage("测试中…");
    pendingTestRef.current += 1;
    const req: ProviderTestConnectionRequest = {
      type: "provider_test_connection",
      payload: {
        base_url: baseUrl,
        api_key: apiKeyInput,
        model,
      },
    };
    channel.send(req);
  }, [apiKeyInput, baseUrl, model, getChannel]);

  const handleResetDefaults = useCallback(() => {
    setBaseUrl(DEFAULT_BASE_URL);
    setModel(DEFAULT_MODEL);
  }, []);

  const handleClearSaved = useCallback(async () => {
    try {
      await deleteCloudApiKey();
      setHasKey(false);
      setTestStatus("idle");
      setTestMessage("已清除");
    } catch (e) {
      setSaveError(`清除失败: ${String(e)}`);
    }
  }, []);

  const handleSave = useCallback(async () => {
    setSaveError(null);
    setSaving(true);
    try {
      if (apiKeyInput.trim()) {
        await setCloudApiKey(apiKeyInput.trim());
        setApiKeyInput("");
        setHasKey(true);
      }
      // P2-1 前端持久：把 baseUrl/model/strategy 写 localStorage，避免关窗即丢。
      // P2-2 会迁移到 config.toml 服务端写（跨设备 + 可被 backend reload），
      // 届时这三行 setItem 会被替换成 control-WS `update_config` 调用。
      try {
        localStorage.setItem(LS_BASE_URL, baseUrl);
        localStorage.setItem(LS_MODEL, model);
        localStorage.setItem(LS_STRATEGY, strategy);
      } catch (e) {
        // localStorage 配额满或被策略禁用都非致命 —— apiKey 已经进 keyring，
        // 只是下次重开面板会回退到硬编码默认值。
        console.warn("[SettingsPanel] persist prefs failed:", e);
      }
      // Push to running backend (hot-swap cloud provider + strategy).
      try {
        await updateCloudConfig(secret, {
          base_url: baseUrl,
          model,
          api_key: apiKeyInput.trim() || undefined,
          strategy,
        });
      } catch (e) {
        // Non-fatal: keyring + localStorage already saved. Backend will
        // pick up the new key on next restart via env var injection.
        console.warn("[SettingsPanel] backend hot-swap failed:", e);
        setSaveError(`已保存到本地，但后端热更新失败: ${String(e)}`);
        return; // Don't close panel so user sees the error
      }
      onConfigChanged?.();
      onClose();
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  }, [apiKeyInput, baseUrl, model, strategy, onClose, secret]);

  const handleRefreshBudget = useCallback(async () => {
    try {
      const ch = getChannel();
      if (!ch) throw new Error("控制通道未连接");
      const b = await fetchDailyBudget(ch);
      setBudget(b);
      setBudgetError(null);
    } catch (e) {
      setBudget(null);
      setBudgetError(String(e));
    }
  }, [getChannel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="设置"
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div style={panelStyle}>
        <header style={headerStyle}>
          <h2 style={{ margin: 0, fontSize: 16 }}>设置</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭设置"
            style={closeBtnStyle}
          >
            ✕
          </button>
        </header>

        {/* ================ 云端账号 ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>云端账号</h3>
          <label style={labelStyle}>
            <span>baseUrl</span>
            <input
              style={inputStyle}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              spellCheck={false}
            />
          </label>
          <label style={labelStyle}>
            <span>model</span>
            <input
              style={inputStyle}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              spellCheck={false}
            />
          </label>
          <label style={labelStyle}>
            <span>apiKey</span>
            <input
              style={inputStyle}
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder={hasKey ? "已配置（输入新值替换）" : "未配置"}
              autoComplete="off"
            />
          </label>
          <div style={btnRowStyle}>
            <button
              type="button"
              onClick={handleTestConnection}
              disabled={testStatus === "pending" || saving}
              style={btnStyle}
            >
              {testStatus === "pending" ? "测试中…" : "测试连接"}
            </button>
            <button type="button" onClick={handleResetDefaults} style={btnStyle}>
              重置默认
            </button>
            {hasKey && (
              <button
                type="button"
                onClick={handleClearSaved}
                style={{ ...btnStyle, color: "#b91c1c" }}
              >
                清除已保存
              </button>
            )}
          </div>
          {testStatus !== "idle" && (
            <div
              role="status"
              style={{
                ...statusStyle,
                color:
                  testStatus === "ok"
                    ? "#047857"
                    : testStatus === "fail"
                      ? "#b91c1c"
                      : "#374151",
              }}
            >
              {testMessage}
            </div>
          )}
        </section>

        {/* ================ 路由策略 ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>路由策略</h3>
          <label style={labelStyle}>
            <span>strategy</span>
            <select
              style={inputStyle}
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as Strategy)}
            >
              {(Object.keys(STRATEGY_LABELS) as Strategy[]).map((s) => (
                <option key={s} value={s}>
                  {STRATEGY_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <p style={hintStyle}>
            S3: UI only — backend strategy switching lands with S6.
          </p>
        </section>

        {/* ================ 今日使用 ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>今日使用</h3>
          {budgetError && (
            <div role="status" style={{ ...statusStyle, color: "#b91c1c" }}>
              {budgetError}
            </div>
          )}
          {budget && (
            <div style={{ display: "grid", gap: 6, fontSize: 13 }}>
              <div>
                已消耗 ¥{budget.spent_today_cny.toFixed(2)} /
                ¥{budget.daily_budget_cny.toFixed(2)}
              </div>
              <div>剩余 ¥{budget.remaining_cny.toFixed(2)}</div>
              <div>
                使用率 {budget.percent_used.toFixed(1)}%
              </div>
              <div
                style={{
                  height: 6,
                  background: "#e5e7eb",
                  borderRadius: 3,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${Math.min(100, Math.max(0, budget.percent_used))}%`,
                    height: "100%",
                    background:
                      budget.percent_used >= 90 ? "#dc2626" : "#10b981",
                    transition: "width 0.2s",
                  }}
                />
              </div>
            </div>
          )}
          <div style={btnRowStyle}>
            <button type="button" onClick={handleRefreshBudget} style={btnStyle}>
              刷新
            </button>
          </div>
          <p style={hintStyle}>
            数据来自 BillingLedger（S8），按 Asia/Shanghai 时区按日累计。
          </p>
        </section>

        {/* ================ Footer ================ */}
        <footer style={footerStyle}>
          {saveError && (
            <span style={{ color: "#b91c1c", fontSize: 12 }}>{saveError}</span>
          )}
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            style={{ ...btnStyle, background: "#2563eb", color: "white" }}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ---- inline styles (kept local so the panel has no CSS imports to wire) ----

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.5)",
  display: "grid",
  placeItems: "center",
  padding: 8,
  zIndex: 1000,
};

const panelStyle: React.CSSProperties = {
  background: "white",
  padding: 18,
  borderRadius: 8,
  width: "min(94vw, 520px)",
  boxSizing: "border-box",
  maxHeight: "92vh",
  overflowY: "auto",
  color: "#111",
  boxShadow: "0 10px 30px rgba(0,0,0,0.25)",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  marginBottom: 12,
};

const closeBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  fontSize: 16,
  cursor: "pointer",
  color: "#6b7280",
};

const sectionStyle: React.CSSProperties = {
  borderTop: "1px solid #e5e7eb",
  paddingTop: 12,
  marginTop: 12,
  display: "grid",
  gap: 8,
};

const h3Style: React.CSSProperties = {
  margin: 0,
  fontSize: 13,
  color: "#374151",
  fontWeight: 600,
};

const labelStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "80px 1fr",
  alignItems: "center",
  gap: 8,
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  padding: "5px 8px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  fontSize: 12,
  fontFamily: "inherit",
  outline: "none",
};

const btnRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

const btnStyle: React.CSSProperties = {
  padding: "5px 12px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  background: "white",
  fontSize: 12,
  cursor: "pointer",
};

const statusStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "4px 8px",
  background: "#f9fafb",
  borderRadius: 4,
};

const hintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#6b7280",
  margin: 0,
};

const footerStyle: React.CSSProperties = {
  borderTop: "1px solid #e5e7eb",
  paddingTop: 12,
  marginTop: 14,
  display: "flex",
  justifyContent: "flex-end",
  alignItems: "center",
  gap: 10,
};
