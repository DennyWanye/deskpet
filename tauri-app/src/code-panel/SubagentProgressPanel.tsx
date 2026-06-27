// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * subagent-concurrency-driver WI-3.4 — 子代理并发进度面板。
 *
 * 订阅 subagentStore，渲染当前并发子代理的实时进度（queued/running/
 * completed/failed/cancelled + kind 徽章）。runs 为空时不渲染（零侵入）。
 *
 * 2026-06-21 交互升级（用户要求「主消息面板也要看到 + 好的交互体验」）：
 *   • `variant` 主题：code 面板用 light（浅底深字，BC），桌宠主消息面板
 *     用 dark（深色玻璃拟态，与 MessageStreamPanel 一致）。
 *   • 可折叠：点表头收起/展开子代理明细；全部跑完后自动收起减少干扰。
 *   • 运行中实时计时：每秒刷新 running 行的已运行时长，给「活着」的感觉。
 *   • 取消态独立：后端排队中被取消发 status="failed"+reason="cancelled"
 *     → 渲染成「🚫 已取消」而非「❌ 失败」（区分主动取消 vs 真失败）。
 *   • 出现/消失带淡入动画（index.css `@keyframes deskpet-subagent-*`）。
 */
import { useEffect, useRef, useState } from "react";
import { useSubagentStore, type SubagentRunView } from "./subagentStore";

const KIND_LABEL: Record<string, string> = {
  research: "调研",
  code: "编码",
  doc: "文档",
  web: "联网",
  fileops: "文件",
  general: "通用",
};

type Phase = "queued" | "running" | "completed" | "failed" | "cancelled";

/** 把 store 里的 (status, reason) 归一成渲染用的 phase。 */
function phaseOf(r: SubagentRunView): Phase {
  if (r.status === "cancelled") return "cancelled";
  if (r.status === "failed") {
    return r.reason === "cancelled" ? "cancelled" : "failed";
  }
  if (r.status === "running") return "running";
  if (r.status === "completed") return "completed";
  return "queued";
}

const PHASE_ICON: Record<Phase, string> = {
  queued: "⏳",
  running: "🔧",
  completed: "✅",
  failed: "❌",
  cancelled: "🚫",
};

const PHASE_LABEL: Record<Phase, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
};

function isActive(p: Phase): boolean {
  return p === "queued" || p === "running";
}

function elapsedLabel(since: number, nowMs: number): string {
  // 后端进度事件的 ts 是 epoch 秒（time.time()），而 now 是 Date.now() 毫秒。
  // <1e12 视为秒 → 换算成毫秒，避免「秒 - 毫秒」算出天文数字的 elapsed。
  const sinceMs = since > 0 && since < 1e12 ? since * 1000 : since;
  const s = Math.max(0, Math.round((nowMs - sinceMs) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${String(s % 60).padStart(2, "0")}s`;
}

export interface SubagentProgressPanelProps {
  /** light = code 面板（浅底，BC 默认）；dark = 桌宠消息面板（深色玻璃）。 */
  variant?: "light" | "dark";
}

interface Theme {
  bg: string;
  border: string;
  headFg: string;
  bodyFg: string;
  subFg: string;
  badgeBg: string;
  badgeFg: string;
}

const THEMES: Record<"light" | "dark", Theme> = {
  light: {
    bg: "rgba(120,140,200,0.10)",
    border: "rgba(120,140,200,0.25)",
    headFg: "#445",
    bodyFg: "#556",
    subFg: "#889",
    badgeBg: "rgba(90,110,170,0.18)",
    badgeFg: "#445",
  },
  dark: {
    bg: "rgba(99,102,241,0.12)",
    border: "rgba(129,140,248,0.32)",
    headFg: "#e5e7eb",
    bodyFg: "#cbd5e1",
    subFg: "#94a3b8",
    badgeBg: "rgba(129,140,248,0.22)",
    badgeFg: "#c7d2fe",
  },
};

export function SubagentProgressPanel({
  variant = "light",
}: SubagentProgressPanelProps) {
  const runs = useSubagentStore((s) => s.runs);
  const metrics = useSubagentStore((s) => s.metrics);
  const clearTerminal = useSubagentStore((s) => s.clearTerminal);
  const theme = THEMES[variant];

  const list = Object.values(runs).sort((a, b) => a.ts - b.ts);
  const active = list.filter((r) => isActive(phaseOf(r))).length;

  // 折叠态：用户可手动切；全部跑完(active 由正→0)时自动收起减少干扰。
  const [collapsed, setCollapsed] = useState(false);
  const prevActive = useRef(active);
  useEffect(() => {
    if (prevActive.current > 0 && active === 0) setCollapsed(true);
    if (prevActive.current === 0 && active > 0) setCollapsed(false);
    prevActive.current = active;
  }, [active]);

  // 运行中实时计时：仅在有活跃子代理时每秒刷新（无活跃则不空转）。
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (active === 0) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);

  if (list.length === 0) return null;

  const total = list.length;

  return (
    <div
      data-testid="subagent-progress-panel"
      data-variant={variant}
      className="deskpet-subagent-card"
      style={{
        margin: variant === "dark" ? "6px 6px 2px" : "6px 8px",
        padding: "7px 9px",
        borderRadius: 9,
        background: theme.bg,
        border: `1px solid ${theme.border}`,
        fontSize: 12,
        boxShadow: variant === "dark" ? "0 2px 10px rgba(0,0,0,0.25)" : undefined,
      }}
    >
      <div
        role="button"
        tabIndex={0}
        onClick={() => setCollapsed((c) => !c)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") setCollapsed((c) => !c);
        }}
        data-testid="subagent-progress-header"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontWeight: 600,
          color: theme.headFg,
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        <span
          style={{ fontSize: 11, opacity: 0.7, transition: "transform .15s" }}
        >
          {collapsed ? "▸" : "▾"}
        </span>
        <span>🤖 子代理并发</span>
        {active > 0 && (
          <span
            className="deskpet-subagent-dot"
            aria-hidden
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: "#22c55e",
              display: "inline-block",
            }}
          />
        )}
        <span style={{ marginLeft: 2, fontWeight: 500, color: theme.subFg }}>
          {active > 0 ? `运行中 ${active}/${total}` : `全部完成 (${total})`}
        </span>
        {active === 0 && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              clearTerminal();
            }}
            data-testid="subagent-progress-clear"
            style={{
              marginLeft: "auto",
              fontSize: 11,
              border: "none",
              background: "transparent",
              cursor: "pointer",
              color: theme.subFg,
            }}
          >
            清除
          </button>
        )}
      </div>

      {!collapsed && (
        <div style={{ marginTop: 5, display: "flex", flexDirection: "column", gap: 2 }}>
          {list.map((r) => {
            const phase = phaseOf(r);
            const running = phase === "running";
            const terminal = !isActive(phase);
            return (
              <div
                key={r.run_id}
                data-testid="subagent-run-row"
                data-phase={phase}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "2px 0",
                  opacity: terminal ? 0.72 : 1,
                }}
              >
                <span
                  className={running ? "deskpet-subagent-pulse" : undefined}
                  aria-hidden
                >
                  {PHASE_ICON[phase]}
                </span>
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    borderRadius: 6,
                    background: theme.badgeBg,
                    color: theme.badgeFg,
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                  }}
                >
                  {KIND_LABEL[r.kind] || r.kind || "?"}
                </span>
                <span
                  style={{
                    color: theme.bodyFg,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={r.task_id || r.run_id}
                >
                  {r.task_id || r.run_id}
                </span>
                <span
                  style={{
                    marginLeft: "auto",
                    color: theme.subFg,
                    fontSize: 11,
                    whiteSpace: "nowrap",
                  }}
                >
                  {running ? elapsedLabel(r.ts, now) : PHASE_LABEL[phase]}
                </span>
              </div>
            );
          })}
          {/* WI-OC-2 累计观测汇总（背压/lane 指标）。后端在每条进度事件附带
              调度器全局快照；旧后端不推 → 全 0 → 不渲染（优雅降级）。 */}
          {(metrics.total_queued > 0 ||
            metrics.peak_concurrent > 0 ||
            metrics.total_rejected > 0) && (
            <div
              data-testid="subagent-metrics-summary"
              style={{
                marginTop: 4,
                paddingTop: 4,
                borderTop: `1px solid ${theme.border}`,
                display: "flex",
                flexWrap: "wrap",
                gap: 10,
                fontSize: 11,
                color: theme.subFg,
              }}
            >
              <span data-testid="subagent-metric-peak" title="历史运行峰值（背压上限）">
                峰值 {metrics.peak_concurrent}
              </span>
              <span data-testid="subagent-metric-queued" title="累计入队总数">
                累计入队 {metrics.total_queued}
              </span>
              <span
                data-testid="subagent-metric-rejected"
                title="累计拒绝/取消（排队或运行中被取消）"
                style={
                  metrics.total_rejected > 0
                    ? { color: "#ef4444" }
                    : undefined
                }
              >
                拒绝 {metrics.total_rejected}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
