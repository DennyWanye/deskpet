// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 2026-05-31 restore — Drill-down modal opened by clicking <ContextRing>.
 *
 * Mirrors Claude Code's "where did my context go" view:
 *   1. Header: model name, current % filled, "if I keep going I'll
 *      hit compact at X tokens".
 *   2. Stacked bar by section (system / memory / tools / history).
 *   3. Each section row — token count, preview snippet (expandable).
 *
 * Pulls its data via WS request `context_breakdown_request` and waits
 * for `context_breakdown_response`. Caller passes a `sessionId` + a
 * `send` + `onMessage` plumbing pair.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";

import type { ContextUsageSnapshot } from "../stores/sessionsStore";
import { ringColor, ringPercent } from "./ContextRing";

interface BreakdownSection {
  kind: string;
  label: string;
  tokens: number;
  preview: string;
  count?: number;
}

interface BreakdownResponse {
  session_id: string;
  model: string;
  sections: BreakdownSection[];
  total_estimated_tokens: number;
  last_usage_prompt_tokens: number;
  context_window: number;
  effective_ceiling: number;
  compact_at: number;
  updated_at: number;
  ts: number;
}

export interface ContextBreakdownModalProps {
  open: boolean;
  onClose(): void;
  sessionId: string;
  /** Live snapshot — drives the header gauge while we wait for the
   *  breakdown response. */
  snapshot: ContextUsageSnapshot | null | undefined;
  /** Sender. */
  send(msg: { type: string; payload?: Record<string, unknown> }): void;
  /** Subscriber — returns an unsubscribe. */
  onMessage(fn: (msg: any) => void): () => void;
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

const SECTION_COLOR: Record<string, string> = {
  system: "#3b82f6",
  memory: "#a855f7",
  tools: "#f59e0b",
  history: "#10b981",
  current: "#ec4899",
};

export function ContextBreakdownModal({
  open,
  onClose,
  sessionId,
  snapshot,
  send,
  onMessage,
}: ContextBreakdownModalProps) {
  const [data, setData] = useState<BreakdownResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // 2026-05-31 restore — parent commonly passes inline lambdas for send /
  // onMessage which change identity every render. If we depended on them in
  // our effect, each re-render (e.g. ws lastMessage tick) would unsubscribe
  // + resubscribe, racing with the response arrival. Pin the latest
  // callbacks in refs and only re-run the effect on open/sessionId changes.
  const sendRef = useRef(send);
  const onMessageRef = useRef(onMessage);
  useEffect(() => { sendRef.current = send; }, [send]);
  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);

  useEffect(() => {
    if (!open) {
      setData(null);
      setExpanded({});
      return;
    }
    setLoading(true);
    setData(null);
    const off = onMessageRef.current((msg) => {
      if (msg?.type === "context_breakdown_response") {
        const p = msg.payload as BreakdownResponse;
        if (p?.session_id === sessionId) {
          setData(p);
          setLoading(false);
        }
      }
    });
    try {
      sendRef.current({ type: "context_breakdown_request", payload: { session_id: sessionId } });
    } catch {
      setLoading(false);
    }
    return off;
  }, [open, sessionId]);

  const pct = ringPercent(snapshot);
  const color = ringColor(pct);
  const sectionTotal = useMemo(
    () => (data?.sections || []).reduce((a, s) => a + (s.tokens || 0), 0),
    [data],
  );

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Context usage breakdown"
      style={overlayStyle}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={modalStyle}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 14 }}>Context usage · <span style={{ color: "#94a3b8", fontWeight: 400 }}>{sessionId}</span></h3>
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
              {snapshot?.model || "(no model yet)"}
            </div>
          </div>
          <button onClick={onClose} aria-label="关闭" style={closeBtn}>✕</button>
        </header>

        {snapshot && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
              <span><b style={{ color }}>{snapshot.prompt_tokens.toLocaleString()}</b> / {snapshot.effective_ceiling.toLocaleString()} tokens</span>
              <span style={{ color: "#94a3b8" }}>{pct.toFixed(1)}% used</span>
            </div>
            <div style={{ position: "relative", height: 8, background: "#1f2937", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 240ms" }} />
              {snapshot.compact_at > 0 && snapshot.effective_ceiling > 0 && (
                <div title="compact 阈值" style={{
                  position: "absolute",
                  left: `${(snapshot.compact_at / snapshot.effective_ceiling) * 100}%`,
                  top: 0, bottom: 0, width: 2, background: "#f59e0b", opacity: 0.8,
                }} />
              )}
              {snapshot.recall_sweet > 0 && snapshot.effective_ceiling > 0 && (
                <div title="recall sweet spot" style={{
                  position: "absolute",
                  left: `${(snapshot.recall_sweet / snapshot.effective_ceiling) * 100}%`,
                  top: 0, bottom: 0, width: 1, background: "#fbbf24", opacity: 0.7,
                }} />
              )}
            </div>
            <div style={{ fontSize: 10, color: "#6b7280", marginTop: 4, display: "flex", gap: 12 }}>
              {snapshot.cached_tokens > 0 && <span>cache hit: {fmtTokens(snapshot.cached_tokens)}</span>}
              {snapshot.compact_at > 0 && <span>compact @ {fmtTokens(snapshot.compact_at)}</span>}
              {snapshot.recall_sweet > 0 && <span>sweet @ {fmtTokens(snapshot.recall_sweet)}</span>}
            </div>
          </div>
        )}

        <div style={{ borderTop: "1px solid #374151", paddingTop: 12 }}>
          <div style={{ fontSize: 12, color: "#cbd5e1", marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span>构成（前端估算 · ~3.5 chars/token）</span>
            {data && (
              <span style={{ color: "#94a3b8", fontSize: 10 }}>
                估算合计 {fmtTokens(sectionTotal)} · LLM 实测 {fmtTokens(data.last_usage_prompt_tokens)}
              </span>
            )}
          </div>

          {loading && (
            <div style={{ color: "#94a3b8", fontSize: 12, padding: 12, textAlign: "center" }}>
              加载 breakdown …
            </div>
          )}
          {!loading && !data && (
            <div style={{ color: "#94a3b8", fontSize: 12, padding: 12 }}>
              暂无数据
            </div>
          )}

          {data && (
            <>
              <div style={{ display: "flex", height: 10, borderRadius: 4, overflow: "hidden", marginBottom: 8, background: "#1f2937" }}>
                {data.sections.map((s) => {
                  const w = sectionTotal > 0 ? (s.tokens / sectionTotal) * 100 : 0;
                  if (w <= 0) return null;
                  return (
                    <div
                      key={s.kind}
                      title={`${s.label}: ${fmtTokens(s.tokens)} (${w.toFixed(1)}%)`}
                      style={{ width: `${w}%`, background: SECTION_COLOR[s.kind] || "#64748b" }}
                    />
                  );
                })}
              </div>

              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
                {data.sections.map((s) => (
                  <li key={s.kind} style={{
                    background: "#111827",
                    borderRadius: 4,
                    padding: "8px 10px",
                    border: "1px solid #1f2937",
                  }}>
                    <button
                      type="button"
                      onClick={() => setExpanded((e) => ({ ...e, [s.kind]: !e[s.kind] }))}
                      style={{
                        background: "transparent", border: "none", padding: 0, color: "inherit",
                        cursor: "pointer", width: "100%",
                        display: "flex", justifyContent: "space-between", alignItems: "center",
                      }}
                    >
                      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                        <span style={{
                          display: "inline-block",
                          width: 8, height: 8, borderRadius: 2,
                          background: SECTION_COLOR[s.kind] || "#64748b",
                        }} />
                        <b>{s.label}</b>
                        {typeof s.count === "number" && (
                          <span style={{ color: "#94a3b8", fontSize: 11 }}>· {s.count} 项</span>
                        )}
                      </span>
                      <span style={{ color: "#94a3b8", fontSize: 11 }}>
                        {fmtTokens(s.tokens)} tokens {expanded[s.kind] ? "▴" : "▾"}
                      </span>
                    </button>
                    {expanded[s.kind] && (
                      <pre style={{
                        marginTop: 8, marginBottom: 0,
                        fontSize: 10.5,
                        color: "#cbd5e1",
                        background: "#0b1120",
                        padding: 8,
                        borderRadius: 3,
                        maxHeight: 160,
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}>{s.preview || "(空)"}</pre>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.55)",
  display: "grid",
  placeItems: "center",
  padding: 8,
  zIndex: 1250,
};

const modalStyle: React.CSSProperties = {
  background: "#0f172a",
  color: "#e2e8f0",
  padding: 16,
  borderRadius: 8,
  width: "min(94vw, 540px)",
  maxHeight: "90vh",
  overflowY: "auto",
  border: "1px solid #1e293b",
  boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
  fontFamily: "inherit",
};

const closeBtn: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#94a3b8",
  cursor: "pointer",
  fontSize: 14,
  padding: 4,
};
