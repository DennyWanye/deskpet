// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 2026-05-31 restore — Claude-Code-style context-usage ring gauge.
 *
 * Renders an 18px SVG circle with an arc that fills as
 * `prompt_tokens / effective_ceiling` grows. Colour ramps:
 *   <50%  green (#10b981)
 *   <80%  amber (#f59e0b)
 *   <95%  orange (#ea580c)
 *   ≥95%  red    (#dc2626)
 *
 * The yellow tick at `recall_sweet / effective_ceiling` flags the
 * needle-recall sweet-spot beyond which long-context models degrade.
 *
 * Click handler is wired by the parent — usually opens
 * <ContextBreakdownModal>. Hovers show a 1-line summary.
 */
import React, { useMemo } from "react";

import type { ContextUsageSnapshot } from "../stores/sessionsStore";

export interface ContextRingProps {
  snapshot: ContextUsageSnapshot | null | undefined;
  /** Outer SVG size in CSS px. Default 18 — same scale as Claude Code's
   *  chip in the input bar. Pass 22 for a slightly more prominent header
   *  ring. */
  size?: number;
  /** Click handler — usually opens the breakdown modal. */
  onClick?: () => void;
  /** Show a number next to the ring (default false — ring-only is the
   *  Claude-Code default). Pass true for the main pet header where there
   *  is room for a small numeric label. */
  showLabel?: boolean;
  /** Extra inline style for the wrapper. */
  style?: React.CSSProperties;
}

const RING_STROKE = 2.5;

/** Pure helper — % filled of the *practical* window. Exposed for vitest. */
export function ringPercent(snap: ContextUsageSnapshot | null | undefined): number {
  if (!snap || !snap.effective_ceiling || snap.effective_ceiling <= 0) return 0;
  return Math.max(0, Math.min(100, (snap.prompt_tokens / snap.effective_ceiling) * 100));
}

/** Pure helper — colour for a given fill %. Exposed for vitest. */
export function ringColor(pct: number): string {
  if (pct >= 95) return "#dc2626";
  if (pct >= 80) return "#ea580c";
  if (pct >= 50) return "#f59e0b";
  return "#10b981";
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

export function ContextRing({
  snapshot,
  size = 18,
  onClick,
  showLabel = false,
  style,
}: ContextRingProps) {
  const pct = ringPercent(snapshot);
  const color = ringColor(pct);
  const cx = size / 2;
  const cy = size / 2;
  const r = (size - RING_STROKE) / 2;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const sweetPct = snapshot && snapshot.effective_ceiling
    ? Math.max(0, Math.min(100, (snapshot.recall_sweet / snapshot.effective_ceiling) * 100))
    : 0;
  // Tick position on the ring (in arc length, rotated -90° so 0% sits at 12).
  const tickAngle = (sweetPct / 100) * 2 * Math.PI - Math.PI / 2;
  const tickX = cx + r * Math.cos(tickAngle);
  const tickY = cy + r * Math.sin(tickAngle);

  const tooltip = useMemo(() => {
    if (!snapshot) return "尚无 LLM 调用记录 — 发一条消息后会显示";
    const parts = [
      `${snapshot.model || "(unknown)"}`,
      `${fmtTokens(snapshot.prompt_tokens)} / ${fmtTokens(snapshot.effective_ceiling)} (${pct.toFixed(1)}%)`,
      snapshot.cached_tokens > 0 ? `cache: ${fmtTokens(snapshot.cached_tokens)}` : null,
      snapshot.prompt_tokens >= snapshot.compact_at && snapshot.compact_at > 0
        ? `⚠ 已超过 compact 阈值 (${fmtTokens(snapshot.compact_at)})` : null,
    ].filter(Boolean);
    return parts.join(" · ");
  }, [snapshot, pct]);

  const dim = !snapshot || snapshot.stub || snapshot.prompt_tokens === 0;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      title={tooltip}
      aria-label={`Context usage: ${pct.toFixed(0)}%`}
      data-testid="context-ring"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: 2,
        background: "transparent",
        border: "none",
        cursor: onClick ? "pointer" : "default",
        opacity: dim ? 0.45 : 1,
        ...style,
      }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke="#374151"
          strokeOpacity={0.35}
          strokeWidth={RING_STROKE}
        />
        <circle
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke={color}
          strokeWidth={RING_STROKE}
          strokeDasharray={`${dash} ${circ - dash}`}
          strokeDashoffset={circ / 4}
          strokeLinecap="round"
          style={{ transition: "stroke-dasharray 360ms ease-out, stroke 240ms ease" }}
        />
        {sweetPct > 0 && sweetPct < 100 && (
          <circle cx={tickX} cy={tickY} r={1.2} fill="#fbbf24" />
        )}
      </svg>
      {showLabel && (
        <span style={{ fontSize: 10.5, color: "#94a3b8", lineHeight: 1, whiteSpace: "nowrap" }}>
          {snapshot ? `${pct.toFixed(0)}%` : "—"}
        </span>
      )}
    </button>
  );
}
