import { useCallback, useEffect, useState } from "react";
import { Icon } from "./Icon";
import {
  dark,
  darkPanelSurface,
  darkPanelHeader,
  darkCloseBtn,
  segGroup,
  segTab,
  darkButton,
  darkListSurface,
  darkInput,
} from "../theme/components";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  IncomingMessage,
  L1Entry,
  L1Target,
  MemoryClearAck,
  MemoryDeleteAck,
  MemoryExportResponse,
  MemoryHit,
  MemoryL1DeleteAck,
  MemoryL1ListResponse,
  MemoryListResponse,
  MemorySearchResponse,
  SkillDescriptor,
  SkillsListResponse,
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
//
// P4-S11 §16.1/§16.3/§16.4 extension: added three more views on top of the
// original 对话 view — L1 档案 (MEMORY.md/USER.md), 向量搜索 (L3 recall),
// and 技能 (SkillLoader list). All requests hit the existing control WS;
// backend handlers degrade gracefully when services aren't yet registered.
type MemoryScope = "session" | "all";
type PanelView = "turns" | "l1" | "search" | "skills";

export function MemoryPanel({ open, onClose, sessionId, getChannel }: Props) {
  const [view, setView] = useState<PanelView>("turns");

  // --- Conversation-history (legacy) state ------------------------------
  const [turns, setTurns] = useState<StoredTurn[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [scope, setScope] = useState<MemoryScope>("session");

  // --- P4-S11 L1 file-memory state --------------------------------------
  const [l1Target, setL1Target] = useState<L1Target>("memory");
  const [l1Entries, setL1Entries] = useState<L1Entry[]>([]);
  const [l1Reason, setL1Reason] = useState<string | null>(null);

  // --- P4-S11 L3 vector-search state ------------------------------------
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [searchTopK, setSearchTopK] = useState<number>(10);
  const [searchHits, setSearchHits] = useState<MemoryHit[]>([]);
  const [searchReason, setSearchReason] = useState<string | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchPending, setSearchPending] = useState(false);

  // --- P4-S11 skills list state -----------------------------------------
  const [skills, setSkills] = useState<SkillDescriptor[]>([]);
  const [skillsReason, setSkillsReason] = useState<string | null>(null);

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
        case "memory_l1_list_response": {
          const m = msg as MemoryL1ListResponse;
          // Guard against cross-target chatter (user switches tabs mid-flight)
          if (m.payload.target !== l1Target) break;
          setL1Entries(m.payload.entries);
          setL1Reason(m.payload.reason ?? null);
          break;
        }
        case "memory_l1_delete_ack": {
          const m = msg as MemoryL1DeleteAck;
          if (m.payload.deleted) {
            // Easiest correct thing: refetch the list so indexes stay in sync
            // with server truth after the delete.
            getChannel()?.send({
              type: "memory_l1_list",
              payload: { target: m.payload.target },
            });
            setStatus(`L1 deleted: ${m.payload.target}#${m.payload.index}`);
          } else {
            setStatus(
              `L1 delete failed (${m.payload.target}#${m.payload.index})` +
                (m.payload.reason ? ` — ${m.payload.reason}` : ""),
            );
          }
          break;
        }
        case "memory_search_response": {
          const m = msg as MemorySearchResponse;
          setSearchHits(m.payload.hits);
          setSearchReason(m.payload.reason ?? null);
          setSearchError(m.payload.error ?? null);
          setSearchPending(false);
          break;
        }
        case "skills_list_response": {
          const m = msg as SkillsListResponse;
          setSkills(m.payload.skills);
          setSkillsReason(m.payload.reason ?? null);
          break;
        }
      }
    });
    return unsub;
  }, [open, getChannel, l1Target]);

  // --- Conversation history ---------------------------------------------
  const refresh = useCallback(() => {
    const ch = getChannel();
    if (!ch) return;
    setLoading(true);
    ch.send({
      type: "memory_list",
      payload:
        scope === "all"
          ? { scope: "all", session_id: null }
          : { scope: "session", session_id: sessionId },
    });
  }, [getChannel, sessionId, scope]);

  useEffect(() => {
    if (open && view === "turns") refresh();
  }, [open, view, refresh]);

  // --- L1 fetch on tab enter / target change ----------------------------
  const refreshL1 = useCallback(() => {
    getChannel()?.send({
      type: "memory_l1_list",
      payload: { target: l1Target },
    });
  }, [getChannel, l1Target]);

  useEffect(() => {
    if (open && view === "l1") refreshL1();
  }, [open, view, l1Target, refreshL1]);

  // --- Skills fetch -----------------------------------------------------
  const refreshSkills = useCallback(() => {
    getChannel()?.send({ type: "skills_list", payload: {} });
  }, [getChannel]);

  useEffect(() => {
    if (open && view === "skills") refreshSkills();
  }, [open, view, refreshSkills]);

  // --- Handlers ---------------------------------------------------------
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

  const handleL1Delete = (index: number) => {
    getChannel()?.send({
      type: "memory_l1_delete",
      payload: { target: l1Target, index },
    });
  };

  const handleSearch = () => {
    const q = searchQuery.trim();
    if (!q) return;
    setSearchPending(true);
    setSearchError(null);
    setSearchReason(null);
    setSearchHits([]);
    getChannel()?.send({
      type: "memory_search",
      payload: { query: q, top_k: searchTopK },
    });
  };

  if (!open) return null;

  return (
    <div style={darkPanelSurface} role="dialog" aria-label="记忆管理">
      {/* Header */}
      <div style={darkPanelHeader}>
        <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 26,
              height: 26,
              borderRadius: 8,
              background: "rgba(79,147,255,0.16)",
              color: "#7fb0ff",
              flexShrink: 0,
            }}
          >
            <Icon name="archive" size={15} />
          </span>
          <strong style={{ fontSize: 14, fontWeight: 600, letterSpacing: 0.2 }}>
            记忆管理
          </strong>
          <span style={{ color: dark.textFaint, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {view === "turns" && scope === "session" ? sessionId : ""}
            {view === "turns" && scope === "all" ? "全部会话" : ""}
            {view === "l1" ? `L1 · ${l1Target === "memory" ? "MEMORY.md" : "USER.md"}` : ""}
            {view === "search" ? "向量搜索" : ""}
            {view === "skills" ? "技能" : ""}
          </span>
        </span>
        <button
          data-testid="memory-close"
          onClick={onClose}
          style={darkCloseBtn}
          title="关闭"
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "rgba(239,68,68,0.18)";
            e.currentTarget.style.color = "#fca5a5";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "rgba(255,255,255,0.05)";
            e.currentTarget.style.color = dark.textMuted;
          }}
        >
          <Icon name="close" size={15} />
        </button>
      </div>

      {/* Body */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          padding: 13,
          gap: 8,
        }}
      >
      {/* Top-level view tabs — 对话 / L1 / 搜索 / 技能 */}
      <div
        style={{ ...segGroup, alignSelf: "flex-start" }}
        role="tablist"
        aria-label="Panel view"
      >
        <button
          data-testid="memory-view-turns"
          role="tab"
          aria-selected={view === "turns"}
          onClick={() => setView("turns")}
          style={tabStyle(view === "turns")}
        >
          对话
        </button>
        <button
          data-testid="memory-view-l1"
          role="tab"
          aria-selected={view === "l1"}
          onClick={() => setView("l1")}
          style={tabStyle(view === "l1")}
        >
          L1 档案
        </button>
        <button
          data-testid="memory-view-search"
          role="tab"
          aria-selected={view === "search"}
          onClick={() => setView("search")}
          style={tabStyle(view === "search")}
        >
          向量搜索
        </button>
        <button
          data-testid="memory-view-skills"
          role="tab"
          aria-selected={view === "skills"}
          onClick={() => setView("skills")}
          style={tabStyle(view === "skills")}
        >
          技能
        </button>
      </div>

      {/* --- 对话 view ------------------------------------------------ */}
      {view === "turns" && (
        <>
          <div
            style={{ ...segGroup, alignSelf: "flex-start" }}
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

          <div style={listStyle}>
            {turns.length === 0 && !loading && (
              <div style={emptyStyle}>(no turns)</div>
            )}
            {turns.map((t) => (
              <div
                key={t.id}
                data-testid={`memory-turn-${t.id}`}
                data-turn-role={t.role}
                data-turn-session={t.session_id}
                style={rowStyle}
              >
                <span style={{ flexShrink: 0, opacity: 0.6, width: "60px", fontSize: "10px" }}>
                  {t.role}
                </span>
                <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {scope === "all" && (
                    <span style={sessionTagStyle} title={t.session_id}>
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
                  style={{ ...btnStyle("#991b1b"), padding: "1px 6px", fontSize: "10px", flexShrink: 0 }}
                  title={`Delete turn #${t.id}`}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      {/* --- L1 档案 view --------------------------------------------- */}
      {view === "l1" && (
        <>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={segGroup} role="tablist" aria-label="L1 target">
              <button
                data-testid="l1-target-memory"
                role="tab"
                aria-selected={l1Target === "memory"}
                onClick={() => setL1Target("memory")}
                style={tabStyle(l1Target === "memory")}
              >
                MEMORY.md
              </button>
              <button
                data-testid="l1-target-user"
                role="tab"
                aria-selected={l1Target === "user"}
                onClick={() => setL1Target("user")}
                style={tabStyle(l1Target === "user")}
              >
                USER.md
              </button>
            </div>
            <button
              data-testid="l1-refresh"
              onClick={refreshL1}
              style={{ ...btnStyle("#3b82f6"), marginLeft: "auto" }}
            >
              <Icon name="refresh" size={13} />
              刷新
            </button>
          </div>
          {l1Reason && (
            <div style={{ opacity: 0.6, fontSize: "10px", marginBottom: "4px" }}>
              后端提示：{l1Reason}
            </div>
          )}
          {status && (
            <div style={{ opacity: 0.75, marginBottom: "6px", fontSize: "11px" }}>
              {status}
            </div>
          )}
          <div style={listStyle}>
            {l1Entries.length === 0 && (
              <div style={emptyStyle}>(空)</div>
            )}
            {l1Entries.map((e) => (
              <div
                key={e.index}
                data-testid={`l1-entry-${e.index}`}
                data-l1-target={l1Target}
                style={rowStyle}
              >
                <span
                  style={{
                    flexShrink: 0,
                    opacity: 0.6,
                    width: "56px",
                    fontSize: "10px",
                    textAlign: "right",
                    paddingRight: "4px",
                  }}
                  title={`salience=${e.salience.toFixed(2)}`}
                >
                  #{e.index} · {e.salience.toFixed(2)}
                </span>
                <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {e.text}
                </span>
                <button
                  data-testid={`l1-delete-${e.index}`}
                  onClick={() => handleL1Delete(e.index)}
                  style={{ ...btnStyle("#991b1b"), padding: "1px 6px", fontSize: "10px", flexShrink: 0 }}
                  title={`Delete entry #${e.index}`}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      {/* --- 向量搜索 view -------------------------------------------- */}
      {view === "search" && (
        <>
          <div style={{ display: "flex", gap: "4px", marginBottom: "6px" }}>
            <input
              data-testid="memory-search-input"
              type="text"
              placeholder="搜索长期记忆…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSearch();
              }}
              className="bp-dark-input"
              style={{ ...darkInput, flex: 1 }}
            />
            <input
              data-testid="memory-search-topk"
              type="number"
              min={1}
              max={50}
              value={searchTopK}
              onChange={(e) =>
                setSearchTopK(Math.max(1, Math.min(50, Number(e.target.value) || 10)))
              }
              className="bp-dark-input"
              style={{ ...darkInput, width: 60, textAlign: "center" }}
              title="top_k"
            />
            <button
              data-testid="memory-search-submit"
              onClick={handleSearch}
              style={btnStyle("#3b82f6")}
              disabled={searchPending}
            >
              {searchPending ? "…" : "搜索"}
            </button>
          </div>
          {searchReason && (
            <div style={{ opacity: 0.6, fontSize: "10px", marginBottom: "4px" }}>
              后端提示：{searchReason}
            </div>
          )}
          {searchError && (
            <div style={{ color: "#fca5a5", fontSize: "11px", marginBottom: "4px" }}>
              搜索失败：{searchError}
            </div>
          )}
          <div style={listStyle}>
            {searchHits.length === 0 && !searchPending && (
              <div style={emptyStyle}>
                {searchQuery.trim() ? "(无匹配)" : "(输入查询后回车)"}
              </div>
            )}
            {searchHits.map((h, i) => (
              <div
                key={i}
                data-testid={`memory-hit-${i}`}
                style={{ ...rowStyle, flexDirection: "column", alignItems: "stretch" }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    opacity: 0.55,
                    fontSize: "10px",
                    marginBottom: "2px",
                  }}
                >
                  <span>{h.source || "(unknown)"}</span>
                  <span>score {h.score.toFixed(3)}</span>
                </div>
                <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {h.text}
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      {/* --- 技能 view ------------------------------------------------ */}
      {view === "skills" && (
        <>
          <div style={{ display: "flex", gap: "4px", marginBottom: "6px" }}>
            <button data-testid="skills-refresh" onClick={refreshSkills} style={btnStyle("#3b82f6")}>
              刷新
            </button>
            <span style={{ alignSelf: "center", opacity: 0.55, fontSize: "10px" }}>
              共 {skills.length} 个
            </span>
          </div>
          {skillsReason && (
            <div style={{ opacity: 0.6, fontSize: "10px", marginBottom: "4px" }}>
              后端提示：{skillsReason}
            </div>
          )}
          <div style={listStyle}>
            {skills.length === 0 && <div style={emptyStyle}>(无技能)</div>}
            {groupSkills(skills).map(([group, list]) => (
              <div key={group} data-testid={`skills-group-${group}`}>
                <div
                  style={{
                    opacity: 0.55,
                    fontSize: "10px",
                    margin: "4px 2px",
                    borderBottom: "1px dashed #334155",
                    paddingBottom: "2px",
                  }}
                >
                  {group === "builtin" ? "内置" : group === "user" ? "用户" : group}
                </div>
                {list.map((s) => (
                  <div
                    key={`${group}-${s.name}`}
                    data-testid={`skill-${s.name}`}
                    style={{ ...rowStyle, flexDirection: "column", alignItems: "stretch" }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <strong style={{ fontSize: "12px" }}>{s.name}</strong>
                      <span style={{ opacity: 0.55, fontSize: "10px" }}>
                        {s.version || "-"}
                        {s.author ? ` · ${s.author}` : ""}
                      </span>
                    </div>
                    {s.description && (
                      <span
                        style={{
                          opacity: 0.75,
                          fontSize: "11px",
                          marginTop: "2px",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                        }}
                      >
                        {s.description}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
      </div>
    </div>
  );
}

// --- helpers ------------------------------------------------------------

function groupSkills(
  skills: SkillDescriptor[],
): Array<[string, SkillDescriptor[]]> {
  const groups: Record<string, SkillDescriptor[]> = {};
  for (const s of skills) {
    const key = s.source || "builtin";
    (groups[key] ||= []).push(s);
  }
  // Stable order: builtin first, user second, everything else last.
  const order = ["builtin", "user"];
  return Object.entries(groups).sort(([a], [b]) => {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return a.localeCompare(b);
  });
}

const listStyle: React.CSSProperties = {
  ...darkListSurface,
  padding: "4px 6px",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  gap: "8px",
  padding: "7px 8px",
  borderBottom: `1px solid ${dark.hairline}`,
  alignItems: "flex-start",
  borderRadius: 6,
};

const emptyStyle: React.CSSProperties = {
  color: dark.textFaint,
  textAlign: "center",
  marginTop: "32px",
  fontSize: 12,
};

const sessionTagStyle: React.CSSProperties = {
  display: "inline-block",
  marginRight: "6px",
  padding: "1px 6px",
  color: dark.textMuted,
  fontSize: "10px",
  background: "rgba(255,255,255,0.05)",
  border: `1px solid ${dark.border}`,
  borderRadius: 5,
  verticalAlign: "middle",
};

// 把旧的「颜色字符串」入参映射到深色按钮变体，保持各调用点不动。
function btnStyle(bg: string): React.CSSProperties {
  const variant =
    bg === "#3b82f6"
      ? "primary"
      : bg === "#10b981"
        ? "success"
        : bg === "#dc2626" || bg === "#7f1d1d" || bg === "#991b1b"
          ? "danger"
          : "neutral";
  return darkButton(variant, "sm");
}

function tabStyle(active: boolean): React.CSSProperties {
  return segTab(active);
}
