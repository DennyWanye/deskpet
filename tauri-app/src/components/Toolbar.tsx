// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 桌宠右上角工具栏 — 高级感重设计。
 *
 * 设计要点：
 * - 视觉分组：[左] 应用面板入口 (memory/trace/settings/store/feedback/code)
 *   ・分隔条・[中] 功能开关 (autostart) ・分隔条・[右] 状态徽章
 * - emoji 图标全部替换为统一的 SVG 线性图标（Icon 组件）—— 跨平台一致、
 *   克制精致。
 * - 玻璃容器：深色渐变 + 高饱和模糊 + 顶部内高光，呈现「液态玻璃」质感。
 * - 图标按钮 26×26，hover 有柔和高亮 + 1px 上抬。
 * - 状态徽章用「圆点 + 文字」语义编码（绿=本地、青=云、橙=断开）。
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { Icon, type IconName } from "./Icon";
import { ContextRing } from "./ContextRing";
import type { ContextUsageSnapshot } from "../stores/sessionsStore";

interface Props {
  /** @deprecated chat path is unified now; kept to avoid prop-drilling churn */
  useToolUseLoop?: boolean;
  /** @deprecated */
  toggleToolUseLoop?: () => void;
  onMemory: () => void;
  onTrace: () => void;
  onSettings: () => void;
  onSkillStore: () => void;
  /** WI-02 (beta-100): open the in-app feedback / diagnostic panel. */
  onFeedback: () => void;
  /** P4-S21 #7: invoked when user clicks the Quit button. */
  onExit: () => void;
  /** P4-S22: open Code mode entry flow (folder picker + IPC). */
  onCodeMode: () => void;
  /** True when Code mode is currently active for this session. */
  codeModeActive?: boolean;
  /** 2026-05-26: relay edition 账户按钮。null/undefined 时不渲染（OSS 默认）。 */
  onAccount?: () => void;
  autostartReady: boolean;
  autostartEnabled: boolean;
  onToggleAutostart: () => void;
  vadStatus: "idle" | "listening" | "speaking" | "thinking";
  isPlaying: boolean;
  isRecording: boolean;
  fps: number;
  connectionState: "disconnected" | "connecting" | "connected";
  routeKind: "cloud" | "local" | null;
  /** Push the toolbar down (px) when a top error banner is shown above
   * it, so the banner sits ABOVE the toolbar instead of overlapping. */
  topOffset?: number;
  /** 2026-05-31 restore — context-usage ring snapshot + click handler.
   * Undefined = ring hidden; null = ring rendered dim (no LLM call yet). */
  contextUsage?: ContextUsageSnapshot | null;
  onContextRingClick?: () => void;
}

export const Toolbar: React.FC<Props> = ({
  onMemory,
  onTrace,
  onSettings,
  onSkillStore,
  onFeedback,
  onExit,
  onCodeMode,
  codeModeActive,
  onAccount,
  autostartReady,
  autostartEnabled,
  onToggleAutostart,
  vadStatus,
  isPlaying,
  isRecording,
  fps,
  connectionState,
  routeKind,
  topOffset,
  contextUsage,
  onContextRingClick,
}) => {
  return (
    <div
      style={{
        position: "absolute",
        top: topOffset != null ? topOffset : tokens.space.xs,
        right: tokens.space.xs,
        // 桌宠窗口很窄（~360px）— 允许多行换行避免溢出
        maxWidth: "calc(100% - 8px)",
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        justifyContent: "flex-end",
        rowGap: 4,
        columnGap: 3,
        zIndex: 20,
        background:
          "linear-gradient(180deg, rgba(28,33,48,0.78) 0%, rgba(16,19,28,0.84) 100%)",
        padding: "4px 5px",
        borderRadius: 13,
        border: "1px solid rgba(255,255,255,0.09)",
        boxShadow:
          "0 8px 28px rgba(0,0,0,0.40), inset 0 1px 0 rgba(255,255,255,0.07)",
        backdropFilter: "blur(20px) saturate(1.5)",
        WebkitBackdropFilter: "blur(20px) saturate(1.5)",
      }}
    >
      {/* Group 0 — 账户（relay edition 才出现） */}
      {onAccount && (
        <>
          <IconButton
            title="账户设置"
            testId="relay-account-pill"
            icon="user"
            onClick={onAccount}
          />
          <Divider />
        </>
      )}

      {/* Group 1 — panel toggles */}
      <IconButton title="记忆管理" testId="memory-toggle" icon="archive" onClick={onMemory} />
      <IconButton title="ContextTrace" testId="trace-toggle" icon="compass" onClick={onTrace} />
      <IconButton title="设置" testId="settings-toggle" icon="settings" onClick={onSettings} />
      <IconButton title="技能商店" testId="skill-store-toggle" icon="store" onClick={onSkillStore} />
      <IconButton title="反馈问题" testId="feedback-toggle" icon="bug" onClick={onFeedback} />
      <IconButton
        title={codeModeActive ? "Code 模式已开启 — 点击切换项目" : "进入 Code 模式（编程助手）"}
        testId="code-mode-toggle"
        icon="terminal"
        onClick={onCodeMode}
        active={codeModeActive}
      />
      <IconButton
        title="退出 DeskPet"
        testId="exit-toggle"
        icon="power"
        onClick={onExit}
        danger
      />

      <Divider />

      {/* Group 2 — feature toggles */}
      {autostartReady && (
        <ToggleChip
          active={autostartEnabled}
          onClick={onToggleAutostart}
          label="开机启动"
          title={autostartEnabled ? "已开启开机自启 — 点击关闭" : "点击开启开机自启"}
        />
      )}

      <Divider />

      {/* Group 3 — status badges */}
      {vadStatus === "thinking" && !isPlaying && (
        <StatusBadge color="warning" pulse>
          思考中
        </StatusBadge>
      )}
      {isPlaying && <StatusBadge color="info">朗读</StatusBadge>}
      {isRecording && (
        <StatusBadge color="danger" pulse>
          录音
        </StatusBadge>
      )}
      <StatusBadge color={fps >= 30 ? "success" : "danger"}>{fps} FPS</StatusBadge>
      <StatusBadge color={getConnColor(connectionState, routeKind)}>
        {getConnLabel(connectionState, routeKind)}
      </StatusBadge>
      {/* 2026-05-31 restore — Claude-Code-style context ring */}
      {contextUsage !== undefined && (
        <ContextRing
          snapshot={contextUsage}
          size={20}
          showLabel
          onClick={onContextRingClick}
          style={{ marginLeft: 2 }}
        />
      )}
    </div>
  );
};

// -------------- helpers --------------

function getConnColor(
  state: Props["connectionState"],
  route: Props["routeKind"]
): "success" | "info" | "warning" | "muted" {
  if (state !== "connected") return "warning";
  if (route === "cloud") return "info";
  if (route === "local") return "success";
  return "muted";
}

function getConnLabel(
  state: Props["connectionState"],
  route: Props["routeKind"]
): string {
  if (state === "connected") {
    if (route === "cloud") return "云端";
    if (route === "local") return "本地";
    return "已连接";
  }
  if (state === "connecting") return "连接中";
  return "未连接";
}

// -------------- IconButton --------------

const IconButton: React.FC<{
  icon: IconName;
  onClick: () => void;
  title: string;
  testId?: string;
  active?: boolean;
  danger?: boolean;
}> = ({ icon, onClick, title, testId, active, danger }) => (
  <button
    type="button"
    data-testid={testId}
    onClick={onClick}
    title={title}
    style={{
      width: 27,
      height: 27,
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      padding: 0,
      borderRadius: 8,
      cursor: "pointer",
      background: active ? "rgba(52,211,153,0.20)" : "transparent",
      border: `1px solid ${active ? "rgba(52,211,153,0.42)" : "transparent"}`,
      color: active ? "#6ee7b7" : "rgba(226,232,240,0.78)",
      transition: `background ${tokens.duration.fast}ms ${tokens.easing.inOut}, color ${tokens.duration.fast}ms ${tokens.easing.inOut}, transform ${tokens.duration.fast}ms ${tokens.easing.inOut}`,
    }}
    onMouseEnter={(e) => {
      const b = e.currentTarget;
      if (!active) {
        b.style.background = danger
          ? "rgba(239,68,68,0.20)"
          : "rgba(255,255,255,0.10)";
        b.style.color = danger ? "#fca5a5" : "#f1f5f9";
      }
      b.style.transform = "translateY(-1px)";
    }}
    onMouseLeave={(e) => {
      const b = e.currentTarget;
      if (!active) {
        b.style.background = "transparent";
        b.style.color = "rgba(226,232,240,0.78)";
      }
      b.style.transform = "translateY(0)";
    }}
  >
    <Icon name={icon} size={15} />
  </button>
);

// -------------- ToggleChip --------------

const ToggleChip: React.FC<{
  active: boolean;
  onClick: () => void;
  label: string;
  title: string;
}> = ({ active, onClick, label, title }) => {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      style={{
        fontFamily: tokens.font.ui,
        fontSize: 10.5,
        fontWeight: tokens.weight.semibold,
        height: 21,
        padding: "0 9px",
        borderRadius: tokens.radius.pill,
        border: `1px solid ${
          active ? "rgba(52,211,153,0.55)" : "rgba(255,255,255,0.12)"
        }`,
        background: active
          ? "linear-gradient(180deg, rgba(52,211,153,0.32), rgba(16,185,129,0.22))"
          : "rgba(255,255,255,0.04)",
        color: active ? "#a7f3d0" : "rgba(148,163,184,0.92)",
        cursor: "pointer",
        transition: `background ${tokens.duration.fast}ms ${tokens.easing.inOut}, border-color ${tokens.duration.fast}ms ${tokens.easing.inOut}, color ${tokens.duration.fast}ms ${tokens.easing.inOut}`,
        whiteSpace: "nowrap",
        letterSpacing: 0.3,
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.background = "rgba(255,255,255,0.10)";
          e.currentTarget.style.color = "#e2e8f0";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.background = "rgba(255,255,255,0.04)";
          e.currentTarget.style.color = "rgba(148,163,184,0.92)";
        }
      }}
    >
      {label}
    </button>
  );
};

// -------------- StatusBadge --------------

const PALETTES = {
  success: { dot: "#34d399", fg: "#a7f3d0", bg: "rgba(16,185,129,0.14)" },
  info: { dot: "#22d3ee", fg: "#a5f3fc", bg: "rgba(6,182,212,0.14)" },
  warning: { dot: "#fbbf24", fg: "#fde68a", bg: "rgba(245,158,11,0.14)" },
  danger: { dot: "#f87171", fg: "#fecaca", bg: "rgba(239,68,68,0.14)" },
  muted: { dot: "#94a3b8", fg: "#cbd5e1", bg: "rgba(148,163,184,0.12)" },
} as const;

const StatusBadge: React.FC<{
  color: keyof typeof PALETTES;
  children: React.ReactNode;
  pulse?: boolean;
}> = ({ color, children, pulse }) => {
  const p = PALETTES[color];
  return (
    <span
      style={{
        fontFamily: tokens.font.ui,
        fontSize: 10.5,
        fontWeight: tokens.weight.semibold,
        height: 21,
        padding: "0 8px 0 6px",
        borderRadius: tokens.radius.pill,
        background: p.bg,
        color: p.fg,
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        whiteSpace: "nowrap",
        letterSpacing: 0.2,
        border: `1px solid ${p.bg}`,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: p.dot,
          boxShadow: `0 0 5px ${p.dot}`,
          animation: pulse ? "bp-pulse 1.5s ease-in-out infinite" : "none",
          flexShrink: 0,
        }}
      />
      {children}
    </span>
  );
};

// -------------- Divider --------------

const Divider: React.FC = () => (
  <span
    aria-hidden
    style={{
      width: 1,
      height: 15,
      background:
        "linear-gradient(180deg, transparent, rgba(148,163,184,0.30), transparent)",
      margin: "0 2px",
    }}
  />
);

export default Toolbar;
