// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S23 — left sidebar of the code panel.
 *
 * Three sections:
 *   1. Active sessions list (click to switch active_sid)
 *   2. Current session's todos
 *   3. Token usage / concurrency limiter status
 */
import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useSessionsStore } from "../stores/sessionsStore";
import { codePanelWS } from "./ws";
import { ConfirmDialog } from "./ConfirmDialog";
import { ContextRing } from "../components/ContextRing";
import { ContextBreakdownModal } from "../components/ContextBreakdownModal";

export function SessionSidebar({
  onShowDashboard,
  onPick,
}: {
  onShowDashboard: () => void;
  /** P4-S25: parent passes a callback to switch to chat view when the
   *  user clicks a sidebar project. Removes the need for a separate
   *  "← chat" button — one click does both `set_active(sid)` and
   *  view switch. */
  onPick?: (sid: string) => void;
}) {
  const sessions = useSessionsStore((s) => s.sessions);
  const active_sid = useSessionsStore((s) => s.active_sid);
  const set_active = useSessionsStore((s) => s.set_active);
  const inflight = useSessionsStore((s) => s.inflight_count);
  const inflight_max = useSessionsStore((s) => s.inflight_max);
  // P4-S24 followup: pending-delete state. When non-null, ConfirmDialog
  // is shown; on confirm we send code_session_delete + clear.
  const [pending_delete, set_pending_delete] = useState<{
    sid: string;
    name: string;
  } | null>(null);
  // 2026-05-31 restore — context breakdown modal for active session.
  const [ctxModalOpen, setCtxModalOpen] = useState(false);

  const sidList = Object.values(sessions).filter(
    (s) => s.code_session_id || s.base_session_id !== "default",
  );
  const active = sessions[active_sid];

  return (
    <aside
      style={{
        width: 240,
        minWidth: 220,
        background: "rgba(20, 24, 35, 0.97)",
        borderRight: "1px solid rgba(148, 163, 184, 0.18)",
        display: "flex",
        flexDirection: "column",
        color: "#e2e8f0",
        fontSize: 12.5,
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
      }}
    >
      {/* Sessions */}
      <Section title="项目" right={
        <div style={{ display: "flex", gap: 4 }}>
          <button
            type="button"
            title="多项目仪表盘"
            onClick={onShowDashboard}
            style={iconBtnStyle}
          >
            ⊞
          </button>
          <button
            type="button"
            title="新建项目（选文件夹）"
            onClick={() => void newProject()}
            style={iconBtnStyle}
          >
            +
          </button>
        </div>
      }>
        {sidList.length === 0 ? (
          <p style={emptyHint}>暂无 Code 模式会话</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {sidList.map((s) => (
              <li
                key={s.base_session_id}
                onClick={() => {
                  set_active(s.base_session_id);
                  onPick?.(s.base_session_id);
                }}
                style={{
                  position: "relative",
                  padding: "6px 8px",
                  marginBottom: 3,
                  borderRadius: 5,
                  cursor: "pointer",
                  background:
                    s.base_session_id === active_sid
                      ? "rgba(37, 99, 235, 0.22)"
                      : "transparent",
                  border:
                    s.base_session_id === active_sid
                      ? "1px solid rgba(37, 99, 235, 0.5)"
                      : "1px solid transparent",
                }}
              >
                <div
                  style={{
                    fontSize: 12.5,
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                  }}
                >
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {iconForStatus(s.status)} {s.project_name}
                  </span>
                  <button
                    type="button"
                    title="删除项目"
                    aria-label={`删除项目 ${s.project_name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      set_pending_delete({
                        sid: s.base_session_id,
                        name: s.project_name,
                      });
                    }}
                    style={deleteBtnStyle}
                  >
                    🗑️
                  </button>
                </div>
                <div
                  style={{
                    fontSize: 10.5,
                    color: "#94a3b8",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={s.project_root ?? ""}
                >
                  {s.project_root ?? "(无路径)"}
                </div>
                <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>
                  📋 {s.todos.filter((t) => t.status === "completed").length}/
                  {s.todos.length}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Todos */}
      <Section title={`Todos · ${active_sid === "default" ? "default" : active?.project_name ?? ""}`}>
        {!active || active.todos.length === 0 ? (
          <p style={emptyHint}>（没有任务，让 LLM 用 todo_write 拆步骤）</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {active.todos.map((t, i) => (
              <li
                key={i}
                data-bp-selectable=""
                style={{
                  padding: "3px 0",
                  fontSize: 11.5,
                  color: todoColor(t.status),
                  textDecoration:
                    t.status === "completed" ? "line-through" : "none",
                }}
              >
                {todoIcon(t.status)} {t.status === "in_progress" ? t.activeForm : t.content}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Footer — concurrency / usage */}
      <div style={{ marginTop: "auto", padding: 12, borderTop: "1px solid rgba(148, 163, 184, 0.18)", fontSize: 10.5, color: "#94a3b8" }}>
        <div>并发: {inflight}/{inflight_max}{inflight > inflight_max ? ` (排队 ${inflight - inflight_max})` : ""}</div>
        {active && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
            {/* 2026-05-31 restore — context-usage ring + click → modal */}
            <ContextRing
              snapshot={active.context_usage}
              size={16}
              showLabel
              onClick={() => setCtxModalOpen(true)}
            />
            <span style={{ fontSize: 10, color: "#64748b" }}>context</span>
          </div>
        )}
      </div>

      <ContextBreakdownModal
        open={ctxModalOpen}
        onClose={() => setCtxModalOpen(false)}
        sessionId={active_sid}
        snapshot={active?.context_usage ?? null}
        send={(m) => codePanelWS.send(m)}
        onMessage={(fn) => codePanelWS.on_message(fn)}
      />

      {/* P4-S24 followup: delete confirmation. Modal is portaled to
          document body via fixed positioning; rendering it here keeps
          the state local to this component. */}
      {pending_delete && (
        <ConfirmDialog
          title="删除项目"
          message={
            <>
              确定要删除项目 <strong>{pending_delete.name}</strong> 吗？
              <br />
              <span style={{ color: "#94a3b8", fontSize: 11 }}>
                · 项目的 todos 会被清掉
                <br />
                · 聊天记录保留（再次添加同一目录可恢复）
                <br />
                · 此操作不可撤销
              </span>
            </>
          }
          confirm_label="删除"
          variant="danger"
          onConfirm={() => {
            codePanelWS.send({
              type: "code_session_delete",
              payload: { session_id: pending_delete.sid },
            });
            // Optimistic local removal so the UI feels instant; ws
            // dispatch's `code_session_deleted` handler will also
            // remove on confirmation, idempotent either way.
            useSessionsStore.getState().remove(pending_delete.sid);
            set_pending_delete(null);
          }}
          onCancel={() => set_pending_delete(null)}
        />
      )}
    </aside>
  );
}

async function newProject() {
  try {
    const path = await invoke<string | null>("open_directory_dialog");
    if (!path) {
      // User cancelled the picker — don't create a phantom session.
      return;
    }
    // Generate a fresh base_session_id for this new project — backend
    // CodeModeManager keys by base_session_id; collision = same project.
    // Random suffix so each "+ 新项目" click is its own slot.
    const sid = "code-" + Math.random().toString(36).slice(2, 10);
    codePanelWS.send({
      type: "code_mode_enter",
      payload: {
        project_path: path,
        suggested_name: "untitled",
        session_id: sid,
      },
    });
    // Switch active immediately for snappy feedback; backend confirms
    // via the code_mode_state event later.
    useSessionsStore.getState().ensure(sid, {
      project_root: path,
      project_name: path.split(/[\\/]/).pop() || "untitled",
    });
    useSessionsStore.getState().set_active(sid);
  } catch (e) {
    console.warn("[code-panel] new project failed:", e);
  }
}

function Section({
  title,
  right,
  children,
}: {
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div style={{ padding: "10px 12px 12px", borderBottom: "1px solid rgba(148, 163, 184, 0.10)" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          marginBottom: 6,
          fontSize: 11,
          letterSpacing: 0.4,
          textTransform: "uppercase",
          color: "#94a3b8",
        }}
      >
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title}
        </span>
        {right}
      </header>
      {children}
    </div>
  );
}

function iconForStatus(s: string) {
  switch (s) {
    case "thinking": return "⏳";
    case "running": return "🔧";
    case "permission": return "🔒";
    case "error": return "✗";
    default: return "▸";
  }
}

function todoIcon(s: string) {
  if (s === "in_progress") return "⏳";
  if (s === "completed") return "✓";
  return "○";
}

function todoColor(s: string) {
  if (s === "in_progress") return "#fde68a";
  if (s === "completed") return "rgba(167, 243, 208, 0.85)";
  return "rgba(255, 255, 255, 0.78)";
}

const iconBtnStyle: React.CSSProperties = {
  background: "rgba(148, 163, 184, 0.14)",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  color: "#e2e8f0",
  borderRadius: 4,
  width: 22,
  height: 22,
  fontSize: 12,
  cursor: "pointer",
  padding: 0,
};

const deleteBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "rgba(252, 165, 165, 0.85)",
  cursor: "pointer",
  fontSize: 12,
  padding: "0 2px",
  lineHeight: 1,
  // Subtle so it doesn't dominate the title row; reveals hover via
  // the parent <li>'s :hover bg, plus opacity bump on direct hover.
  opacity: 0.55,
  transition: "opacity 100ms",
};

const emptyHint: React.CSSProperties = {
  fontSize: 11,
  color: "rgba(148, 163, 184, 0.6)",
  margin: "4px 0",
  fontStyle: "italic",
};
