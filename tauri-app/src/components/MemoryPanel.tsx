import { useCallback, useEffect, useState } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  IncomingMessage,
  MemoryClearAck,
  MemoryDeleteAck,
  MemoryExportResponse,
  MemoryListResponse,
  StoredTurn,
} from "../types/messages";

type Props = {
  open: boolean;
  onClose: () => void;
  sessionId: string;
  getChannel: () => ControlChannel | null;
};

// MemoryPanel — V5 §6 threat 5 affordance: list / delete / clear / export
// the persisted conversation history. Everything rides the control channel,
// so auth reuses the shared-secret gate already in place for chat.
type MemoryScope = "session" | "all";

export function MemoryPanel({ open, onClose, sessionId, getChannel }: Props) {
  const [turns, setTurns] = useState<StoredTurn[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);
  // P2-0-S3: scope tab — "session" lists this session only, "all" lists
  // every session's turns in one view (still uses the same delete/export
  // machinery; backend already supports scope=all everywhere).
  const [scope, setScope] = useState<MemoryScope>("session");

  // Subscribe to memory_* responses from the shared control channel. Non-
  // memory messages are forwarded to the usual App.tsx handler via the same
  // onMessage broadcast, so adding this listener doesn't steal them.
  useEffect(() => {
    if (!open) return;
    const ch = getChannel();
    if (!ch) return;
    const unsub = ch.onMessage((msg: IncomingMessage) => {
      switch (msg.type) {
        case "memory_list_response": {
          const m = msg as MemoryListResponse;
          setTurns(m.payload.turns);
          setLoading(false);
          break;
        }
        case "memory_delete_ack": {
          const m = msg as MemoryDeleteAck;
          if (m.payload.deleted) {
            setTurns((prev) => prev.filter((t) => t.id !== m.payload.id));
            setStatus(`Deleted turn #${m.payload.id}`);
          } else {
            setStatus(`Turn #${m.payload.id} already gone`);
          }
          break;
        }
        case "memory_clear_ack": {
          const m = msg as MemoryClearAck;
          setTurns([]);
          setStatus(
            m.payload.scope === "all"
              ? `Cleared all history (${m.payload.removed ?? 0} turns)`
              : `Cleared session ${m.payload.session_id}`,
          );
          setConfirmingClear(false);
          break;
        }
        case "memory_export_response": {
          const m = msg as MemoryExportResponse;
          // Turn the payload into a downloadable JSON file — written via a
          // transient blob URL so it works identically in browser dev and
          // packaged Tauri (no plugin-fs roundtrip needed).
          const blob = new Blob([JSON.stringify(m.payload, null, 2)], {
            type: "application/json",
          });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `deskpet-memory-${new Date()
            .toISOString()
            .slice(0, 19)
            .replace(/[:]/g, "-")}.json`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(url);
          setStatus(
            `Exported ${m.payload.turns.length} turns across ` +
              `${m.payload.sessions.length} sessions`,
          );
          break;
        }
      }
    });
    return unsub;
  }, [open, getChannel]);

  const refresh = useCallback(() => {
    const ch = getChannel();
    if (!ch) return;
    setLoading(true);
    // scope=all ignores session_id on the backend, but we still send null
    // to make the payload's intent legible in logs.
    ch.send({
      type: "memory_list",
      payload:
        scope === "all"
          ? { scope: "all", session_id: null }
          : { scope: "session", session_id: sessionId },
    });
  }, [getChannel, sessionId, scope]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  const handleDelete = (id: number) => {
    getChannel()?.send({ type: "memory_delete", payload: { id } });
  };

  const handleClearSession = () => {
    getChannel()?.send({
      type: "memory_clear",
      payload: { scope: "session", session_id: sessionId },
    });
  };

  const handleClearAll = () => {
    getChannel()?.send({ type: "memory_clear", payload: { scope: "all" } });
  };

  const handleExport = () => {
    getChannel()?.send({ type: "memory_export", payload: {} });
  };

  if (!open) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0, 0, 0, 0.85)",
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        padding: "12px",
        color: "white",
        fontSize: "12px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "8px",
        }}
      >
        <strong style={{ fontSize: "14px" }}>
          记忆管理{scope === "session" ? ` · ${sessionId}` : " · 全部会话"}
        </strong>
        <button
          data-testid="memory-close"
          onClick={onClose}
          style={{
            background: "transparent",
            color: "white",
            border: "1px solid #555",
            borderRadius: "4px",
            padding: "2px 8px",
            cursor: "pointer",
          }}
          title="Close"
        >
          ✕
        </button>
      </div>

      {/* Scope tabs — keep these on their own row so they read as view-
          switch, not a verb. Active tab uses filled bg; inactive uses
          ghost to match the button-bar visual language below. */}
      <div
        style={{ display: "flex", gap: "4px", marginBottom: "6px" }}
        role="tablist"
        aria-label="Memory scope"
      >
        <button
          data-testid="memory-scope-session"
          role="tab"
          aria-selected={scope === "session"}
          onClick={() => setScope("session")}
          style={tabStyle(scope === "session")}
        >
          本会话
        </button>
        <button
          data-testid="memory-scope-all"
          role="tab"
          aria-selected={scope === "all"}
          onClick={() => setScope("all")}
          style={tabStyle(scope === "all")}
        >
          全部会话
        </button>
      </div>

      <div style={{ display: "flex", gap: "4px", marginBottom: "6px", flexWrap: "wrap" }}>
        <button data-testid="memory-refresh" onClick={refresh} style={btnStyle("#3b82f6")}>
          {loading ? "…" : "刷新"}
        </button>
        <button data-testid="memory-export" onClick={handleExport} style={btnStyle("#10b981")}>
          导出 JSON
        </button>
        {!confirmingClear ? (
          <button
            data-testid="memory-clear-prompt"
            onClick={() => setConfirmingClear(true)}
            style={btnStyle("#dc2626")}
          >
            清空…
          </button>
        ) : (
          <>
            <button data-testid="memory-clear-session" onClick={handleClearSession} style={btnStyle("#dc2626")}>
              仅本会话
            </button>
            <button data-testid="memory-clear-all" onClick={handleClearAll} style={btnStyle("#7f1d1d")}>
              全部会话
            </button>
            <button
              data-testid="memory-clear-cancel"
              onClick={() => setConfirmingClear(false)}
              style={btnStyle("#6b7280")}
            >
              取消
            </button>
          </>
        )}
      </div>

      {status && (
        <div style={{ opacity: 0.75, marginBottom: "6px", fontSize: "11px" }}>
          {status}
        </div>
      )}

      <div
        style={{
          flex: 1,
          overflowY: "auto",
          border: "1px solid #333",
          borderRadius: "6px",
          padding: "6px",
        }}
      >
        {turns.length === 0 && !loading && (
          <div style={{ opacity: 0.5, textAlign: "center", marginTop: "20px" }}>
            (no turns)
          </div>
        )}
        {turns.map((t) => (
          <div
            key={t.id}
            data-testid={`memory-turn-${t.id}`}
            data-turn-role={t.role}
            data-turn-session={t.session_id}
            style={{
              display: "flex",
              gap: "6px",
              padding: "4px 6px",
              borderBottom: "1px solid #222",
              alignItems: "flex-start",
            }}
          >
            <span
              style={{
                flexShrink: 0,
                opacity: 0.6,
                width: "60px",
                fontSize: "10px",
              }}
            >
              {t.role}
            </span>
            <span
              style={{
                flex: 1,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {scope === "all" && (
                <span
                  style={{
                    display: "inline-block",
                    marginRight: "6px",
                    padding: "0 4px",
                    opacity: 0.55,
                    fontSize: "10px",
                    border: "1px solid #444",
                    borderRadius: "3px",
                    verticalAlign: "middle",
                  }}
                  title={t.session_id}
                >
                  {/* Short-form session tag. Full id still on data-turn-session
                      for E2E + tooltip. */}
                  {t.session_id.length > 12
                    ? `…${t.session_id.slice(-10)}`
                    : t.session_id}
                </span>
              )}
              {t.content}
            </span>
            <button
              data-testid={`memory-delete-${t.id}`}
              onClick={() => handleDelete(t.id)}
              style={{
                ...btnStyle("#991b1b"),
                padding: "1px 6px",
                fontSize: "10px",
                flexShrink: 0,
              }}
              title={`Delete turn #${t.id}`}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function btnStyle(bg: string): React.CSSProperties {
  return {
    background: bg,
    color: "white",
    border: "none",
    borderRadius: "4px",
    padding: "3px 8px",
    fontSize: "11px",
    cursor: "pointer",
  };
}

// Scope-tab button — filled when active, outlined + muted when inactive.
// Keeping this visually distinct from the action buttons below so the
// user doesn't confuse a view switch with a destructive action.
function tabStyle(active: boolean): React.CSSProperties {
  return {
    background: active ? "#2563eb" : "transparent",
    color: active ? "white" : "#cbd5e1",
    border: `1px solid ${active ? "#2563eb" : "#334155"}`,
    borderRadius: "4px",
    padding: "3px 10px",
    fontSize: "11px",
    cursor: "pointer",
    fontWeight: active ? 600 : 400,
  };
}
