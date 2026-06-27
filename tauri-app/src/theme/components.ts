// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S20-UI: Reusable style helpers built on top of design tokens.
 *
 * Pure CSSProperties factories — no React state. Consumers compose
 * them with their own overrides via spread.
 */
import type { CSSProperties } from "react";

import { tokens } from "./tokens";

const { color, radius, shadow, space, text, weight, font, duration, easing } =
  tokens;

// ---------- Button ----------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "icon";
type ButtonSize = "sm" | "md";

export function buttonStyle(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  disabled = false
): CSSProperties {
  const padY = size === "sm" ? 4 : 8;
  const padX = size === "sm" ? 10 : 14;
  const fontSize = size === "sm" ? text.sm.size : text.md.size;

  const base: CSSProperties = {
    fontFamily: font.ui,
    fontSize,
    fontWeight: weight.medium,
    lineHeight: 1,
    padding: variant === "icon" ? 6 : `${padY}px ${padX}px`,
    border: "1px solid transparent",
    borderRadius: variant === "icon" ? radius.md : radius.md,
    cursor: disabled ? "not-allowed" : "pointer",
    transition: `background ${duration.fast}ms ${easing.inOut}, transform ${duration.fast}ms ${easing.inOut}, box-shadow ${duration.fast}ms ${easing.inOut}`,
    userSelect: "none",
    opacity: disabled ? 0.5 : 1,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs,
    whiteSpace: "nowrap",
  };

  switch (variant) {
    case "primary":
      return {
        ...base,
        background: color.accent.bg,
        color: color.neutral[0],
        boxShadow: shadow.sm,
      };
    case "danger":
      return {
        ...base,
        background: color.danger.bg,
        color: color.neutral[0],
        boxShadow: shadow.sm,
      };
    case "secondary":
      return {
        ...base,
        background: color.neutral[0],
        color: color.neutral[800],
        borderColor: color.neutral[200],
      };
    case "ghost":
      return {
        ...base,
        background: "transparent",
        color: color.neutral[600],
      };
    case "icon":
      return {
        ...base,
        width: 32,
        height: 32,
        background: color.surface.darkOverlay,
        color: color.surface.darkText,
        borderColor: color.surface.darkBorder,
        backdropFilter: "blur(10px)",
      };
  }
}

// ---------- Surface (panel / card) ----------

export const surfaceLight: CSSProperties = {
  background: color.surface.lightBg,
  color: color.surface.lightText,
  borderRadius: radius.lg,
  boxShadow: shadow.xl,
  fontFamily: font.ui,
};

export const cardStyle: CSSProperties = {
  background: color.neutral[0],
  border: `1px solid ${color.neutral[200]}`,
  borderRadius: radius.lg,
  padding: space.md,
  transition: `border-color ${duration.fast}ms ${easing.inOut}, box-shadow ${duration.fast}ms ${easing.inOut}`,
};

// ---------- Tab ----------

export function tabStyle(active: boolean): CSSProperties {
  return {
    fontFamily: font.ui,
    fontSize: text.sm.size,
    fontWeight: active ? weight.semibold : weight.medium,
    padding: `${space.xs + 2}px ${space.md}px`,
    border: `1px solid ${active ? color.accent.bg : color.neutral[200]}`,
    background: active ? color.accent.bg : color.neutral[0],
    color: active ? color.neutral[0] : color.neutral[700],
    borderRadius: radius.md,
    cursor: "pointer",
    transition: `background ${duration.fast}ms ${easing.inOut}`,
  };
}

// ---------- Banner / inline alert ----------

type BannerLevel = "info" | "warning" | "error" | "success";

export function bannerStyle(level: BannerLevel): CSSProperties {
  const palette = {
    info: { bg: color.info.soft, fg: color.info.fg, border: color.info.border },
    warning: {
      bg: color.warning.soft,
      fg: color.warning.fg,
      border: color.warning.border,
    },
    error: {
      bg: color.danger.soft,
      fg: color.danger.fg,
      border: color.danger.border,
    },
    success: {
      bg: color.success.soft,
      fg: color.success.fg,
      border: color.success.border,
    },
  }[level];
  return {
    fontFamily: font.ui,
    fontSize: text.sm.size,
    lineHeight: 1.5,
    padding: `${space.sm}px ${space.md}px`,
    background: palette.bg,
    color: palette.fg,
    border: `1px solid ${palette.border}`,
    borderRadius: radius.md,
  };
}

// ---------- Badge / chip ----------

export function badgeStyle(level: BannerLevel = "info"): CSSProperties {
  const palette = {
    info: { bg: color.info.soft, fg: color.info.fg },
    warning: { bg: color.warning.soft, fg: color.warning.fg },
    error: { bg: color.danger.soft, fg: color.danger.fg },
    success: { bg: color.success.soft, fg: color.success.fg },
  }[level];
  return {
    fontFamily: font.ui,
    fontSize: text.xs.size,
    fontWeight: weight.medium,
    padding: `2px 8px`,
    background: palette.bg,
    color: palette.fg,
    borderRadius: radius.pill,
    display: "inline-flex",
    alignItems: "center",
    lineHeight: 1.4,
  };
}

// ---------- Input ----------

export const inputStyle: CSSProperties = {
  fontFamily: font.ui,
  fontSize: text.md.size,
  width: "100%",
  padding: `${space.sm}px ${space.md}px`,
  border: `1px solid ${color.neutral[300]}`,
  borderRadius: radius.md,
  outline: "none",
  background: color.neutral[0],
  color: color.neutral[900],
  transition: `border-color ${duration.fast}ms ${easing.inOut}, box-shadow ${duration.fast}ms ${easing.inOut}`,
  boxSizing: "border-box",
};

// ---------- Modal backdrop ----------

export const backdropStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  background: color.surface.lightBackdrop,
  backdropFilter: "blur(2px)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9000,
  animation: `bp-fade-in ${duration.base}ms ${easing.out}`,
};

export const tokensExport = tokens;

// ============================================================
// 深色玻璃面板套件 — 桌宠各功能面板（记忆/设置/技能商店/反馈…）
// 统一的高级感深色玻璃语言。单一来源，跨面板复用，避免各面板各画各的。
// ============================================================

/** 深色玻璃面板调色板 — 供各面板内联取色。 */
export const dark = {
  // 表面
  bg: "linear-gradient(180deg, rgba(26,30,43,0.97) 0%, rgba(15,17,25,0.98) 100%)",
  bgSolid: "#14161f",
  // 内嵌区块（列表/卡片容器）
  inset: "rgba(255,255,255,0.035)",
  insetBorder: "rgba(255,255,255,0.07)",
  // 卡片
  card: "rgba(255,255,255,0.045)",
  cardHover: "rgba(255,255,255,0.075)",
  cardBorder: "rgba(255,255,255,0.08)",
  // 描边
  border: "rgba(255,255,255,0.09)",
  borderStrong: "rgba(255,255,255,0.14)",
  hairline: "rgba(255,255,255,0.055)",
  // 文字
  text: "#e8edf6",
  textMuted: "#9aa6b8",
  textFaint: "#64748b",
  // 语义
  accent: "#4f93ff",
  accentGrad: "linear-gradient(180deg, #4f93ff 0%, #2563eb 100%)",
  successGrad: "linear-gradient(180deg, #34d399 0%, #10b981 100%)",
  dangerGrad: "linear-gradient(180deg, #f87171 0%, #dc2626 100%)",
} as const;

/** 面板根容器 — 全屏覆盖式的深色玻璃。 */
export const darkPanelSurface: CSSProperties = {
  position: "absolute",
  inset: 0,
  zIndex: 1000,
  display: "flex",
  flexDirection: "column",
  background: dark.bg,
  backdropFilter: "blur(28px) saturate(1.5)",
  WebkitBackdropFilter: "blur(28px) saturate(1.5)",
  color: dark.text,
  fontFamily: font.ui,
  fontSize: text.sm.size,
  animation: `bp-fade-in ${duration.base}ms ${easing.out}`,
};

/** 面板顶栏 — 标题 + 关闭，底部一条 hairline 分隔。 */
export const darkPanelHeader: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "11px 13px",
  borderBottom: `1px solid ${dark.hairline}`,
  flexShrink: 0,
};

/** 关闭按钮（圆形幽灵按钮，配 Icon "close"）。 */
export const darkCloseBtn: CSSProperties = {
  width: 28,
  height: 28,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  borderRadius: 8,
  background: "rgba(255,255,255,0.05)",
  border: `1px solid ${dark.border}`,
  color: dark.textMuted,
  cursor: "pointer",
  transition: `background ${duration.fast}ms ${easing.inOut}, color ${duration.fast}ms ${easing.inOut}`,
};

/** 分段控件容器（包住若干 segTab）。 */
export const segGroup: CSSProperties = {
  display: "inline-flex",
  gap: 2,
  padding: 3,
  background: "rgba(255,255,255,0.04)",
  border: `1px solid ${dark.border}`,
  borderRadius: radius.lg,
};

/** 分段控件单项 — 选中态填充蓝色渐变。 */
export function segTab(active: boolean): CSSProperties {
  return {
    fontFamily: font.ui,
    fontSize: text.sm.size,
    fontWeight: active ? weight.semibold : weight.medium,
    padding: "5px 12px",
    border: "1px solid transparent",
    borderRadius: radius.md,
    background: active ? dark.accentGrad : "transparent",
    color: active ? "#fff" : dark.textMuted,
    boxShadow: active ? "0 2px 8px rgba(37,99,235,0.40)" : "none",
    cursor: "pointer",
    whiteSpace: "nowrap",
    transition: `background ${duration.fast}ms ${easing.inOut}, color ${duration.fast}ms ${easing.inOut}`,
  };
}

type DarkBtnVariant = "primary" | "success" | "danger" | "neutral" | "ghost";

/** 深色面板内的实心动作按钮。 */
export function darkButton(
  variant: DarkBtnVariant = "neutral",
  size: "sm" | "md" = "sm"
): CSSProperties {
  const pad = size === "sm" ? "5px 11px" : "7px 14px";
  const fs = size === "sm" ? text.sm.size : text.base.size;
  const base: CSSProperties = {
    fontFamily: font.ui,
    fontSize: fs,
    fontWeight: weight.semibold,
    padding: pad,
    borderRadius: radius.md,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs + 1,
    whiteSpace: "nowrap",
    border: "1px solid transparent",
    transition: `background ${duration.fast}ms ${easing.inOut}, transform ${duration.fast}ms ${easing.inOut}, box-shadow ${duration.fast}ms ${easing.inOut}`,
  };
  switch (variant) {
    case "primary":
      return { ...base, background: dark.accentGrad, color: "#fff", borderColor: "rgba(96,165,250,0.5)", boxShadow: "0 3px 12px rgba(37,99,235,0.34)" };
    case "success":
      return { ...base, background: dark.successGrad, color: "#04261b", borderColor: "rgba(52,211,153,0.5)", boxShadow: "0 3px 12px rgba(16,185,129,0.30)" };
    case "danger":
      return { ...base, background: dark.dangerGrad, color: "#fff", borderColor: "rgba(248,113,113,0.5)", boxShadow: "0 3px 12px rgba(220,38,38,0.30)" };
    case "ghost":
      return { ...base, background: "transparent", color: dark.textMuted, borderColor: "transparent" };
    case "neutral":
    default:
      return { ...base, background: "rgba(255,255,255,0.06)", color: dark.text, borderColor: dark.border };
  }
}

/** 内嵌列表/滚动区容器。 */
export const darkListSurface: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  background: dark.inset,
  border: `1px solid ${dark.insetBorder}`,
  borderRadius: radius.lg,
  padding: space.xs + 2,
};

/** 深色文本输入。 */
export const darkInput: CSSProperties = {
  fontFamily: font.ui,
  fontSize: text.sm.size,
  background: "rgba(255,255,255,0.05)",
  color: dark.text,
  border: `1px solid ${dark.border}`,
  borderRadius: radius.md,
  padding: "6px 11px",
  outline: "none",
  transition: `border-color ${duration.fast}ms ${easing.inOut}, box-shadow ${duration.fast}ms ${easing.inOut}`,
};
