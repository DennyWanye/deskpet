/**
 * P4-S22 — Code mode UI: top banner + auto-suggest banner + todo list panel.
 *
 * Composes three small sub-components in one file because they share
 * state and lifecycle handlers (all listen to the control WS for
 * `code_mode_state`, `code_mode_suggest`, `code_todo_update`).
 *
 * Bound to App-level state via props (parent owns the truth). The
 * panel itself is presentation + side-effects (invoke the folder
 * picker, send IPC).
 */
import React, { useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";

import type { ControlChannel } from "../ws/ControlChannel";

interface TodoItem {
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}

interface CodeModeState {
  enabled: boolean;
  project_root?: string;
  project_name?: string;
}

interface Props {
  state: CodeModeState;
  todos: TodoItem[];
  suggest: { trigger_text: string } | null;
  onDismissSuggest: () => void;
  onAcceptSuggest: () => void;
  onExitCodeMode: () => void;
  // Click on Toolbar 🔧 calls this; we run the folder picker + send enter IPC.
  registerEnterHandler: (handler: () => Promise<void>) => void;
  getChannel: () => ControlChannel | null;
}

export const CodeModePanel: React.FC<Props> = ({
  state,
  todos,
  suggest,
  onDismissSuggest,
  onAcceptSuggest,
  onExitCodeMode,
  registerEnterHandler,
  getChannel,
}) => {
  // P4-S23: in-pet todos panel removed; todos render in the dedicated
  // Code Panel sidebar instead. We keep the prop signature unchanged
  // (todos[] still flows through) so the parent's wiring is untouched.
  const todosOpen = false;
  void todos;
  void todosOpen;

  useEffect(() => {
    registerEnterHandler(async () => {
      try {
        const path = await invoke<string | null>("open_directory_dialog");
        const ch = getChannel();
        if (!ch) return;
        ch.send({
          type: "code_mode_enter",
          payload: {
            project_path: path || null,
            // No suggested name — user picked or cancelled, backend
            // falls back to "untitled" when path is null.
            suggested_name: "untitled",
          },
        });
      } catch (e) {
        console.warn("[CodeMode] folder picker failed:", e);
      }
    });
  }, [registerEnterHandler, getChannel]);

  return (
    <>
      {/* Suggest banner — shows when backend detects project intent and
          code mode isn't on yet. Yellow strip near the top. */}
      {suggest && !state.enabled && (
        <div style={suggestBannerStyle}>
          <span style={{ flex: 1, fontSize: 12 }}>
            🔧 看起来你想启动一个项目（"{suggest.trigger_text.slice(0, 40)}..."），
            <br />
            要切到 Code 模式吗？
          </span>
          <button
            onClick={onAcceptSuggest}
            style={suggestActionStyle("primary")}
          >
            是的
          </button>
          <button
            onClick={onDismissSuggest}
            style={suggestActionStyle("secondary")}
          >
            稍后
          </button>
        </div>
      )}

      {/* Active banner — when code mode is on. Shows project root + exit.
          P4-S23 UX: removed the inline 📋 N todo-count chip — todos
          live in the dedicated Code Panel sidebar now, where users can
          actually read them at a glance. Showing them twice was noisy
          and the count fights for space when users have many tasks. */}
      {state.enabled && (
        <div style={activeBannerStyle}>
          <span style={{ marginRight: 6 }}>🔧</span>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11 }}>
            <strong>Code 模式</strong> · {state.project_name || "(未知项目)"}
          </span>
          <button
            onClick={onExitCodeMode}
            style={bannerBtnStyle("danger")}
            title="退出 Code 模式"
          >
            退出
          </button>
        </div>
      )}

      {/* P4-S23: in-pet todo slide-out removed (replaced by Code
          Panel sidebar's "Todos" section). Kept the conditional
          guard so future re-introduction is a one-line revert. */}
    </>
  );
};

// ----- Styles ---------------------------------------------------------------

// Banner positioned BELOW the toolbar. Toolbar wraps to two rows when
// the window is narrow (status badges go to row 2), so we sit at top:84
// to clear both rows + give some breathing room. Use `flexWrap: wrap`
// inside so the action buttons drop to a second line on narrow widths
// instead of getting clipped off-screen.
const activeBannerStyle: React.CSSProperties = {
  position: "absolute",
  top: 84,
  left: 8,
  right: 8,
  zIndex: 19,
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: 6,
  rowGap: 4,
  padding: "5px 8px",
  borderRadius: 6,
  background: "rgba(20, 60, 30, 0.88)",
  color: "#a7f3d0",
  border: "1px solid rgba(34, 197, 94, 0.5)",
  backdropFilter: "blur(8px)",
  fontSize: 11,
};

const suggestBannerStyle: React.CSSProperties = {
  position: "absolute",
  top: 84,
  left: 8,
  right: 8,
  zIndex: 19,
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: 6,
  rowGap: 4,
  padding: "6px 10px",
  borderRadius: 6,
  background: "rgba(120, 80, 0, 0.85)",
  color: "#fde68a",
  border: "1px solid rgba(245, 158, 11, 0.45)",
  backdropFilter: "blur(8px)",
};

function suggestActionStyle(variant: "primary" | "secondary"): React.CSSProperties {
  return {
    fontSize: 11,
    fontWeight: 600,
    padding: "3px 8px",
    borderRadius: 4,
    border: "1px solid rgba(255,255,255,0.2)",
    background: variant === "primary" ? "#f59e0b" : "rgba(255,255,255,0.1)",
    color: variant === "primary" ? "#000" : "#fff",
    cursor: "pointer",
  };
}

function bannerBtnStyle(variant: "info" | "danger" | "ghost"): React.CSSProperties {
  const colors: Record<string, { bg: string; fg: string }> = {
    info: { bg: "rgba(6, 182, 212, 0.30)", fg: "#67e8f9" },
    danger: { bg: "rgba(239, 68, 68, 0.30)", fg: "#fca5a5" },
    ghost: { bg: "transparent", fg: "rgba(255,255,255,0.7)" },
  };
  const c = colors[variant];
  return {
    fontSize: 10,
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: 4,
    border: "1px solid transparent",
    background: c.bg,
    color: c.fg,
    cursor: "pointer",
  };
}

// P4-S23: todoPanelStyle / todoItemStyle / statusIcon removed —
// the in-pet todos slide-out is gone (todos render in the dedicated
// Code Panel sidebar now). Helpers can be reintroduced via git
// history if a compact todo overlay is ever needed again.
