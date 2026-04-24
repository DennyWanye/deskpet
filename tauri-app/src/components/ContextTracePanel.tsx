import { useCallback, useEffect, useMemo, useState } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  DecisionRecord,
  DecisionsListResponse,
  IncomingMessage,
} from "../types/messages";

type Props = {
  open: boolean;
  onClose: () => void;
  getChannel: () => ControlChannel | null;
};

// ContextTracePanel — P4-S11 §16.5-§16.7: surfaces the decision timeline +
// classifier_path + latency + token budget usage that ContextAssembler
// emits for each turn. Degrades gracefully when ContextAssembler isn't
// registered yet (shows an empty list + the `reason` string).
//
// The chart library budget is 0 here; we render a tiny CSS bar chart
// inline to keep the bundle size in line with the §17.4 1.8GB cap.

const BUDGET_WARN_THRESHOLD = 0.9; // 90% of an assumed context window

export function ContextTracePanel({ open, onClose, getChannel }: Props) {
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [reason, setReason] = useState<string | null>(null);
  const [limit, setLimit] = useState<number>(50);
  const [loading, setLoading] = useState(false);
  const [contextWindow, setContextWindow] = useState<number>(32_000);

  useEffect(() => {
    if (!open) return;
    const ch = getChannel();
    if (!ch) return;
    const unsub = ch.onMessage((msg: IncomingMessage) => {
      if (msg.type === "decisions_list_response") {
        const m = msg as DecisionsListResponse;
        setDecisions(m.payload.decisions);
        setReason(m.payload.reason ?? null);
        setLoading(false);
      }
    });
    return unsub;
  }, [open, getChannel]);

  const refresh = useCallback(() => {
    const ch = getChannel();
    if (!ch) return;
    setLoading(true);
    ch.send({ type: "decisions_list", payload: { limit } });
  }, [getChannel, limit]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  // Newest-first timeline. We *don't* mutate in place because `decisions`
  // is owned by the message-listener effect.
  const ordered = useMemo(() => {
    return [...decisions].sort((a, b) => cmpTimestamp(b.timestamp, a.timestamp));
  }, [decisions]);

  const latest = ordered[0];
  const latestBudgetUsed = latest?.total_tokens ?? 0;
  const latestBudgetPct =
    contextWindow > 0 ? Math.min(1, latestBudgetUsed / contextWindow) : 0;
  const budgetWarn = latestBudgetPct >= BUDGET_WARN_THRESHOLD;

  if (!open) return null;

  return (
    <div
      data-testid="context-trace-panel"
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0, 0, 0, 0.85)",
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        padding: "12px",
        color: "white",
        fontSize: "12px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "8px",
        }}
      >
        <strong style={{ fontSize: "14px" }}>ContextTrace · 决策轨迹</strong>
        <button
          data-testid="trace-close"
          onClick={onClose}
          style={{
            background: "transparent",
            color: "white",
            border: "1px solid #555",
            borderRadius: "4px",
            padding: "2px 8px",
            cursor: "pointer",
          }}
          title="Close"
        >
          ✕
        </button>
      </div>

      {/* Controls */}
      <div style={{ display: "flex", gap: "6px", marginBottom: "6px", flexWrap: "wrap" }}>
        <button data-testid="trace-refresh" onClick={refresh} style={btnStyle("#3b82f6")}>
          {loading ? "…" : "刷新"}
        </button>
        <label style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          <span style={{ opacity: 0.65 }}>条数</span>
          <input
            data-testid="trace-limit"
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={(e) =>
              setLimit(Math.max(1, Math.min(200, Number(e.target.value) || 50)))
            }
            style={numInputStyle}
          />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          <span style={{ opacity: 0.65 }}>context_window</span>
          <input
            data-testid="trace-ctx-window"
            type="number"
            min={1024}
            step={1024}
            value={contextWindow}
            onChange={(e) =>
              setContextWindow(Math.max(1024, Number(e.target.value) || 32_000))
            }
            style={{ ...numInputStyle, width: "86px" }}
          />
        </label>
      </div>

      {reason && (
        <div
          data-testid="trace-reason"
          style={{ opacity: 0.6, fontSize: "10px", marginBottom: "6px" }}
        >
          后端提示：{reason}
        </div>
      )}

      {/* Budget warn banner + usage bar */}
      {latest && (
        <div
          data-testid="trace-budget"
          style={{
            marginBottom: "8px",
            padding: "6px 8px",
            border: `1px solid ${budgetWarn ? "#f97316" : "#334155"}`,
            borderRadius: "4px",
            background: budgetWarn ? "rgba(249,115,22,0.15)" : "rgba(51,65,85,0.25)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px" }}>
            <span>
              {budgetWarn ? "⚠️ 预算接近上限" : "上一回合 token 预算"}
            </span>
            <span>
              {latestBudgetUsed.toLocaleString()} / {contextWindow.toLocaleString()}
              （{(latestBudgetPct * 100).toFixed(1)}%）
            </span>
          </div>
          <div
            style={{
              marginTop: "4px",
              height: "6px",
              background: "#0f172a",
              borderRadius: "3px",
              overflow: "hidden",
            }}
            aria-label="budget usage"
            role="progressbar"
            aria-valuenow={Math.round(latestBudgetPct * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              data-testid="trace-budget-bar"
              style={{
                width: `${latestBudgetPct * 100}%`,
                height: "100%",
                background: budgetWarn ? "#f97316" : "#2563eb",
                transition: "width 200ms ease",
              }}
            />
          </div>
        </div>
      )}

      {/* Timeline */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          border: "1px solid #333",
          borderRadius: "6px",
          padding: "6px",
        }}
      >
        {ordered.length === 0 && !loading && (
          <div style={{ opacity: 0.5, textAlign: "center", marginTop: "20px" }}>
            (无决策记录)
          </div>
        )}
        {ordered.map((d, i) => (
          <DecisionRow key={i} index={i} decision={d} contextWindow={contextWindow} />
        ))}
      </div>
    </div>
  );
}

// ---- Decision row ------------------------------------------------------

function DecisionRow({
  index,
  decision,
  contextWindow,
}: {
  index: number;
  decision: DecisionRecord;
  contextWindow: number;
}) {
  const breakdown = decision.token_breakdown || {};
  const breakdownEntries = Object.entries(breakdown);
  const total = decision.total_tokens ?? 0;

  return (
    <div
      data-testid={`trace-decision-${index}`}
      style={{
        padding: "6px 4px",
        borderBottom: "1px solid #1f2937",
        display: "flex",
        flexDirection: "column",
        gap: "3px",
      }}
    >
      <div
        style={{
          display: "flex",
          gap: "8px",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span style={{ fontSize: "11px", opacity: 0.85 }}>
          <strong style={{ color: classifierColor(decision.classifier_path) }}>
            {decision.classifier_path || "?"}
          </strong>
          {decision.reason ? ` · ${decision.reason}` : ""}
        </span>
        <span style={{ fontSize: "10px", opacity: 0.6 }}>
          {formatTimestamp(decision.timestamp)}
        </span>
      </div>
      <div style={{ display: "flex", gap: "12px", fontSize: "10px", opacity: 0.75 }}>
        <span>
          latency <strong>{fmtMs(decision.latency_ms)}</strong>
        </span>
        <span>
          total_tokens <strong>{total.toLocaleString()}</strong>
        </span>
        {decision.session_id && (
          <span title={decision.session_id}>
            sid{" "}
            <strong>
              {decision.session_id.length > 10
                ? `…${decision.session_id.slice(-8)}`
                : decision.session_id}
            </strong>
          </span>
        )}
      </div>
      {breakdownEntries.length > 0 && (
        <TokenBreakdownBar
          entries={breakdownEntries}
          total={total || sum(breakdownEntries.map(([, v]) => v))}
          contextWindow={contextWindow}
        />
      )}
    </div>
  );
}

// Tiny CSS-only stacked bar — no chart lib to keep bundle size down.
function TokenBreakdownBar({
  entries,
  total,
  contextWindow,
}: {
  entries: [string, number][];
  total: number;
  contextWindow: number;
}) {
  // Palette — one slot per known section. Unknown sections get #64748b.
  const palette: Record<string, string> = {
    system: "#3b82f6",
    l1: "#10b981",
    l2: "#f59e0b",
    l3: "#ef4444",
    tools: "#8b5cf6",
    history: "#06b6d4",
    summary: "#f472b6",
  };
  const safeTotal = total || 1;
  const usedPct = contextWindow > 0 ? Math.min(1, total / contextWindow) : 0;
  return (
    <div style={{ marginTop: "2px" }} data-testid="trace-breakdown">
      <div
        style={{
          display: "flex",
          height: "6px",
          background: "#0f172a",
          borderRadius: "3px",
          overflow: "hidden",
          width: `${usedPct * 100}%`,
          minWidth: "20%",
          transition: "width 200ms ease",
        }}
        title={`使用 ${total.toLocaleString()} / ${contextWindow.toLocaleString()} tokens`}
      >
        {entries.map(([section, value]) => {
          const pct = Math.max(0, (value / safeTotal) * 100);
          if (pct <= 0) return null;
          return (
            <div
              key={section}
              data-testid={`trace-section-${section}`}
              style={{
                width: `${pct}%`,
                background: palette[section] || "#64748b",
              }}
              title={`${section}: ${value.toLocaleString()}`}
            />
          );
        })}
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "8px",
          fontSize: "10px",
          opacity: 0.65,
          marginTop: "3px",
        }}
      >
        {entries.map(([section, value]) => (
          <span key={section}>
            <span
              style={{
                display: "inline-block",
                width: "8px",
                height: "8px",
                background: palette[section] || "#64748b",
                marginRight: "3px",
                borderRadius: "2px",
                verticalAlign: "middle",
              }}
            />
            {section} {value.toLocaleString()}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---- helpers -----------------------------------------------------------

function classifierColor(path?: string): string {
  switch (path) {
    case "cloud":
      return "#60a5fa";
    case "local":
      return "#34d399";
    case "echo":
      return "#fbbf24";
    default:
      return "#e2e8f0";
  }
}

function formatTimestamp(ts?: string | number): string {
  if (ts === undefined || ts === null) return "-";
  let ms: number;
  if (typeof ts === "number") {
    // Heuristic: seconds vs ms.
    ms = ts < 10_000_000_000 ? ts * 1000 : ts;
  } else {
    const parsed = Date.parse(ts);
    if (Number.isNaN(parsed)) return ts;
    ms = parsed;
  }
  const d = new Date(ms);
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function fmtMs(x?: number): string {
  if (x === undefined || x === null) return "-";
  if (x < 1000) return `${x.toFixed(0)}ms`;
  return `${(x / 1000).toFixed(2)}s`;
}

function cmpTimestamp(a?: string | number, b?: string | number): number {
  const ta = toEpoch(a);
  const tb = toEpoch(b);
  return ta - tb;
}

function toEpoch(ts?: string | number): number {
  if (ts === undefined || ts === null) return 0;
  if (typeof ts === "number") {
    return ts < 10_000_000_000 ? ts * 1000 : ts;
  }
  const parsed = Date.parse(ts);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function sum(xs: number[]): number {
  return xs.reduce((a, b) => a + b, 0);
}

const numInputStyle: React.CSSProperties = {
  width: "56px",
  background: "#0f172a",
  color: "white",
  border: "1px solid #334155",
  borderRadius: "4px",
  padding: "2px 4px",
  fontSize: "11px",
};

function btnStyle(bg: string): React.CSSProperties {
  return {
    background: bg,
    color: "white",
    border: "none",
    borderRadius: "4px",
    padding: "3px 8px",
    fontSize: "11px",
    cursor: "pointer",
  };
}
