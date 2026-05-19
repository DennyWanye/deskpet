/**
 * P4-S20-UI: top-right toolbar.
 *
 * 设计要点：
 * - 视觉分组：[左] 应用面板入口 (memory/trace/settings/store) ・分隔条・
 *   [中] 功能开关 (chat_v2 tool-use, autostart) ・分隔条・
 *   [右] 状态徽章 (rec / tts / thinking / fps / connection)
 * - 按钮统一为 28×28 圆角图标按钮，深色半透明 + 模糊背景
 * - 状态徽章用色块语义（绿=本地、蓝=云、灰=未知、橙=断开）
 * - 全部 hover 有 1px 上抬动画 + 边框高亮
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { buttonStyle } from "../theme/components";

interface Props {
  /** @deprecated chat path is unified now; kept to avoid prop-drilling churn */
  useToolUseLoop?: boolean;
  /** @deprecated */
  toggleToolUseLoop?: () => void;
  onMemory: () => void;
  onTrace: () => void;
  onSettings: () => void;
  onSkillStore: () => void;
  /** P4-S21 #7: invoked when user clicks the Quit (⏻) button. */
  onExit: () => void;
  /** P4-S22: open Code mode entry flow (folder picker + IPC). */
  onCodeMode: () => void;
  /** True when Code mode is currently active for this session. */
  codeModeActive?: boolean;
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
}

export const Toolbar: React.FC<Props> = ({
  onMemory,
  onTrace,
  onSettings,
  onSkillStore,
  onExit,
  onCodeMode,
  codeModeActive,
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
}) => {
  return (
    <div
      style={{
        position: "absolute",
        top: topOffset != null ? topOffset : tokens.space.xs,
        right: tokens.space.xs,
        // 桌宠窗口很窄（~282px）— 允许多行换行避免溢出
        maxWidth: "calc(100% - 8px)",
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        justifyContent: "flex-end",
        rowGap: 4,
        columnGap: 2,
        zIndex: 20,
        background: "rgba(15, 18, 28, 0.55)",
        padding: "3px 4px",
        borderRadius: tokens.radius.md,
        border: `1px solid rgba(148, 163, 184, 0.14)`,
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Group 1 — panel toggles */}
      <IconButton title="记忆管理" testId="memory-toggle" onClick={onMemory}>
        🗂
      </IconButton>
      <IconButton title="ContextTrace" testId="trace-toggle" onClick={onTrace}>
        🧭
      </IconButton>
      <IconButton title="设置" testId="settings-toggle" onClick={onSettings}>
        ⚙
      </IconButton>
      <IconButton
        title="技能商店"
        testId="skill-store-toggle"
        onClick={onSkillStore}
      >
        🏪
      </IconButton>
      {/* P4-S22: Code mode entry. Click → folder picker → enters Code
          mode for the chosen project. When active, button shows green
          tint to make the mode obvious. */}
      <IconButton
        title={codeModeActive ? "Code 模式已开启 — 点击切换项目" : "进入 Code 模式（编程助手）"}
        testId="code-mode-toggle"
        onClick={onCodeMode}
        active={codeModeActive}
      >
        🔧
      </IconButton>
      {/* P4-S21 #7: Quit button. Without this, after backend startup
          finishes the only path to exit was Task Manager — the boot
          overlay's Exit button hides itself once startup succeeds. */}
      <IconButton title="退出 DeskPet" testId="exit-toggle" onClick={onExit}>
        ⏻
      </IconButton>

      <Divider />

      {/* Group 2 — feature toggles. P4-S20-LLM-Unified 之后 chat 永远走
          工具调用回路，🛠 toggle 删掉，UI 更简洁。 */}
      {autostartReady && (
        <ToggleChip
          active={autostartEnabled}
          onClick={onToggleAutostart}
          label={autostartEnabled ? "开机启动" : "开机启动"}
          title={
            autostartEnabled ? "已开启开机自启 — 点击关闭" : "点击开启开机自启"
          }
          variant="success"
        />
      )}

      <Divider />

      {/* Group 3 — status badges */}
      {vadStatus === "thinking" && !isPlaying && (
        <StatusBadge color="warning">思考中</StatusBadge>
      )}
      {isPlaying && <StatusBadge color="info">朗读</StatusBadge>}
      {isRecording && <StatusBadge color="danger">录音</StatusBadge>}
      <StatusBadge color={fps >= 30 ? "success" : "danger"}>
        {fps} FPS
      </StatusBadge>
      <StatusBadge color={getConnColor(connectionState, routeKind)}>
        {getConnLabel(connectionState, routeKind)}
      </StatusBadge>
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
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  testId?: string;
  active?: boolean;
}> = ({ children, onClick, title, testId, active }) => (
  <button
    type="button"
    className="bp-btn-icon"
    data-testid={testId}
    onClick={onClick}
    title={title}
    style={{
      ...buttonStyle("icon", "md"),
      width: 24,
      height: 24,
      fontSize: 13,
      padding: 0,
      // override the dark overlay for tighter integration with toolbar bg
      background: active ? "rgba(34, 197, 94, 0.30)" : "transparent",
      borderColor: active ? "rgba(34, 197, 94, 0.50)" : "transparent",
      color: tokens.color.surface.darkText,
    }}
  >
    {children}
  </button>
);

// -------------- ToggleChip --------------

const ToggleChip: React.FC<{
  active: boolean;
  onClick: () => void;
  label: string;
  title: string;
  variant?: "primary" | "success";
  testId?: string;
}> = ({ active, onClick, label, title, variant = "primary", testId }) => {
  const accentColor =
    variant === "success" ? tokens.color.success.bg : tokens.color.accent.bg;
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      title={title}
      style={{
        fontFamily: tokens.font.ui,
        fontSize: 10.5,
        fontWeight: tokens.weight.semibold,
        height: 20,
        padding: "0 6px",
        borderRadius: tokens.radius.sm,
        border: `1px solid ${
          active ? accentColor : "rgba(148, 163, 184, 0.18)"
        }`,
        background: active ? accentColor : "rgba(0, 0, 0, 0.18)",
        color: active ? "#fff" : tokens.color.surface.darkTextMuted,
        cursor: "pointer",
        transition: `background ${tokens.duration.fast}ms ${tokens.easing.inOut}, border-color ${tokens.duration.fast}ms ${tokens.easing.inOut}`,
        whiteSpace: "nowrap",
        letterSpacing: 0.3,
      }}
      onMouseEnter={(e) => {
        if (!active) {
          (e.currentTarget as HTMLButtonElement).style.background =
            "rgba(255, 255, 255, 0.08)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          (e.currentTarget as HTMLButtonElement).style.background =
            "rgba(0, 0, 0, 0.18)";
        }
      }}
    >
      {label}
    </button>
  );
};

// -------------- StatusBadge --------------

const PALETTES = {
  success: { bg: "rgba(16, 185, 129, 0.20)", fg: "#86efac" },
  info: { bg: "rgba(6, 182, 212, 0.22)", fg: "#67e8f9" },
  warning: { bg: "rgba(245, 158, 11, 0.22)", fg: "#fcd34d" },
  danger: { bg: "rgba(239, 68, 68, 0.22)", fg: "#fca5a5" },
  muted: { bg: "rgba(148, 163, 184, 0.18)", fg: "#cbd5e1" },
} as const;

const StatusBadge: React.FC<{
  color: keyof typeof PALETTES;
  children: React.ReactNode;
}> = ({ color, children }) => {
  const p = PALETTES[color];
  return (
    <span
      style={{
        fontFamily: tokens.font.ui,
        fontSize: 10.5,
        fontWeight: tokens.weight.medium,
        height: 20,
        padding: "0 6px",
        borderRadius: tokens.radius.sm,
        background: p.bg,
        color: p.fg,
        display: "inline-flex",
        alignItems: "center",
        whiteSpace: "nowrap",
        letterSpacing: 0.3,
      }}
    >
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
      height: 14,
      background: "rgba(148, 163, 184, 0.22)",
      margin: "0 3px",
    }}
  />
);

export default Toolbar;
