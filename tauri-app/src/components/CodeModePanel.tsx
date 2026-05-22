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

import { Icon } from "./Icon";

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
  // 2026-05-19: green in-face Code-mode banner removed; prop kept for
  // the parent's wiring and possible future re-surfacing elsewhere.
  void onExitCodeMode;

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
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 26,
              height: 26,
              borderRadius: 8,
              background: "rgba(245,158,11,0.22)",
              color: "#fcd34d",
              flexShrink: 0,
            }}
          >
            <Icon name="terminal" size={15} />
          </span>
          <span style={{ flex: 1, fontSize: 12, lineHeight: 1.5, minWidth: 0 }}>
            看起来你想启动一个项目（"{suggest.trigger_text.slice(0, 32)}…"），要切到 Code 模式吗？
          </span>
          <button onClick={onAcceptSuggest} style={suggestActionStyle("primary")}>
            是的
          </button>
          <button onClick={onDismissSuggest} style={suggestActionStyle("secondary")}>
            稍后
          </button>
        </div>
      )}

      {/* 2026-05-19 UX: the green "Code 模式 · (未知项目) 退出" banner
          was removed — it sat at top:84 directly over the pet's head
          and the user found it intrusive. Code-mode lifecycle is fully
          managed inside the dedicated Code Mode window (the 🔧 panel);
          the pet no longer needs a redundant in-face indicator. */}

      {/* P4-S23: in-pet todo slide-out removed (replaced by Code
          Panel sidebar's "Todos" section). */}
    </>
  );
};

// ----- Styles ---------------------------------------------------------------

// Banner positioned BELOW the toolbar. Toolbar wraps to two rows when
// the window is narrow (status badges go to row 2), so we sit at top:84
// to clear both rows + give some breathing room. Use `flexWrap: wrap`
// inside so the action buttons drop to a second line on narrow widths
// instead of getting clipped off-screen.
const suggestBannerStyle: React.CSSProperties = {
  position: "absolute",
  top: 84,
  left: 8,
  right: 8,
  zIndex: 19,
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: 8,
  rowGap: 6,
  padding: "9px 11px",
  borderRadius: 13,
  background:
    "linear-gradient(180deg, rgba(53,40,12,0.92) 0%, rgba(38,28,8,0.94) 100%)",
  color: "#fde9b8",
  border: "1px solid rgba(245, 158, 11, 0.38)",
  boxShadow:
    "0 12px 32px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.06)",
  backdropFilter: "blur(18px) saturate(1.4)",
  WebkitBackdropFilter: "blur(18px) saturate(1.4)",
};

function suggestActionStyle(variant: "primary" | "secondary"): React.CSSProperties {
  return {
    fontSize: 11,
    fontWeight: 700,
    padding: "5px 12px",
    borderRadius: 999,
    border: `1px solid ${
      variant === "primary"
        ? "rgba(251,191,36,0.6)"
        : "rgba(255,255,255,0.16)"
    }`,
    background:
      variant === "primary"
        ? "linear-gradient(180deg, #fbbf24, #f59e0b)"
        : "rgba(255,255,255,0.07)",
    color: variant === "primary" ? "#3a2606" : "#fde9b8",
    cursor: "pointer",
    transition: "transform 120ms ease",
  };
}

// P4-S23: todoPanelStyle / todoItemStyle / statusIcon removed —
// the in-pet todos slide-out is gone (todos render in the dedicated
// Code Panel sidebar now). Helpers can be reintroduced via git
// history if a compact todo overlay is ever needed again.
