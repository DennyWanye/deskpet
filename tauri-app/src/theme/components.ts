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
