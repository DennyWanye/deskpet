// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S20-UI: Design tokens — 单一真相源 of all visual values.
 *
 * 任何组件都从这里取值，避免硬编码。色板基于 Radix Slate (中性) +
 * Tailwind sky/emerald/amber/rose (语义)。三层尺度（compact/regular/
 * comfortable）统一基于 4px 网格。
 *
 * 使用：
 *   import { tokens } from "../theme/tokens";
 *   style={{ background: tokens.color.surface.elevated }}
 */

// 间距：4px 网格
export const space = {
  xxs: 2,
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

// 圆角
export const radius = {
  none: 0,
  sm: 4,
  md: 8,
  lg: 12,
  xl: 16,
  pill: 999,
} as const;

// 字号 + 行高
export const text = {
  xs: { size: 11, lh: 1.4 },
  sm: { size: 12, lh: 1.5 },
  base: { size: 13, lh: 1.55 },
  md: { size: 14, lh: 1.55 },
  lg: { size: 16, lh: 1.5 },
  xl: { size: 18, lh: 1.4 },
  xxl: { size: 22, lh: 1.3 },
} as const;

export const weight = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
} as const;

// 阴影 — 5 级
export const shadow = {
  sm: "0 1px 2px rgba(0, 0, 0, 0.06)",
  md: "0 4px 12px rgba(0, 0, 0, 0.10)",
  lg: "0 10px 30px rgba(0, 0, 0, 0.18)",
  xl: "0 20px 60px rgba(0, 0, 0, 0.30)",
  glow: "0 0 0 4px rgba(59, 130, 246, 0.12)", // focus ring
} as const;

// 动画时长
export const duration = {
  fast: 120,
  base: 200,
  slow: 320,
} as const;

export const easing = {
  // 默认进出曲线 — 类 macOS 弹簧感
  inOut: "cubic-bezier(0.4, 0, 0.2, 1)",
  out: "cubic-bezier(0.16, 1, 0.3, 1)",
} as const;

/**
 * 颜色：
 * - 桌宠主壳走 dark theme（透明背景 + 半透明深色 surface）
 * - 模态框/面板走 light theme（白色 surface 提供阅读对比）
 * - 语义色（accent/success/warning/danger）跨主题共用
 */
export const color = {
  // 中性色（Radix Slate 8/9 校准）
  neutral: {
    0: "#ffffff",
    50: "#f8fafc",
    100: "#f1f5f9",
    200: "#e2e8f0",
    300: "#cbd5e1",
    400: "#94a3b8",
    500: "#64748b",
    600: "#475569",
    700: "#334155",
    800: "#1e293b",
    900: "#0f172a",
    950: "#020617",
  },
  // 语义色 — 都从 tailwind sky/emerald/amber/rose 取
  accent: {
    bg: "#3b82f6",       // sky-500
    bgHover: "#2563eb",  // sky-600
    bgActive: "#1d4ed8", // sky-700
    soft: "#dbeafe",     // sky-100
    fg: "#1e3a8a",       // sky-900
    border: "#93c5fd",   // sky-300
  },
  success: {
    bg: "#10b981",
    soft: "#d1fae5",
    fg: "#064e3b",
    border: "#6ee7b7",
  },
  warning: {
    bg: "#f59e0b",
    soft: "#fef3c7",
    fg: "#78350f",
    border: "#fcd34d",
  },
  danger: {
    bg: "#ef4444",
    bgHover: "#dc2626",
    soft: "#fee2e2",
    fg: "#7f1d1d",
    border: "#fca5a5",
  },
  info: {
    bg: "#06b6d4",
    soft: "#cffafe",
    fg: "#164e63",
    border: "#67e8f9",
  },

  // 表面（主题分支）
  surface: {
    // 深色（桌宠主壳）
    darkOverlay: "rgba(20, 23, 33, 0.92)",
    darkOverlayHover: "rgba(28, 32, 45, 0.95)",
    darkBorder: "rgba(148, 163, 184, 0.18)",
    darkText: "#e2e8f0",
    darkTextMuted: "#94a3b8",
    darkBackdrop: "rgba(0, 0, 0, 0.5)",
    // 亮色（模态框/面板）
    lightBg: "#ffffff",
    lightBgAlt: "#f8fafc",
    lightBorder: "#e2e8f0",
    lightText: "#0f172a",
    lightTextMuted: "#64748b",
    lightBackdrop: "rgba(15, 23, 42, 0.45)",
  },
} as const;

// 字体栈 — 中文优先，含 emoji 兜底
export const font = {
  ui: [
    '"Inter"',
    '"PingFang SC"',
    '"Microsoft YaHei UI"',
    '"Microsoft YaHei"',
    '"Helvetica Neue"',
    'Arial',
    'sans-serif',
    '"Apple Color Emoji"',
    '"Segoe UI Emoji"',
  ].join(", "),
  mono: [
    '"JetBrains Mono"',
    '"Cascadia Code"',
    '"SF Mono"',
    '"Consolas"',
    'monospace',
  ].join(", "),
} as const;

// 综合输出（习惯写法）
export const tokens = {
  space,
  radius,
  text,
  weight,
  shadow,
  duration,
  easing,
  color,
  font,
} as const;

export type Tokens = typeof tokens;
