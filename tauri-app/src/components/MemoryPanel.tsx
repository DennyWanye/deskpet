// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useRef, useState } from "react";
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
  ControlMessage,
  FactItem,
  IncomingMessage,
  L1Entry,
  L1Target,
  MemoryClearAck,
  MemoryDeleteAck,
  MemoryExportResponse,
  MemoryFactsListResponse,
  MemoryForgetResponse,
  MemoryForgetUndoResponse,
  MemoryHit,
  MemoryL1DeleteAck,
  MemoryL1ListResponse,
  MemoryListResponse,
  MemorySearchResponse,
  MemoryThumbsUpResponse,
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
// WI-S2.1b 新增 "facts" — 显示 active 事实 + 🗑 + 5s undo
type PanelView = "turns" | "l1" | "search" | "skills" | "facts";

// undo 浮窗倒计时 5 秒；后端 max_age_seconds 默认值与之保持一致
export const FORGET_UNDO_WINDOW_MS = 5000;

// --- ws message builders (testable，无 React 副作用) -------------------

export function buildMemoryFactsListMessage(opts?: {
  limit?: number;
  subject?: string;
  category?: string;
}): ControlMessage {
  const payload: Record<string, unknown> = { limit: opts?.limit ?? 200 };
  if (opts?.subject) payload.subject = opts.subject;
  if (opts?.category) payload.category = opts.category;
  return { type: "memory_facts_list", payload };
}

export function buildMemoryForgetMessage(factId: number): ControlMessage {
  return { type: "memory_forget", payload: { fact_id: factId } };
}

export function buildMemoryForgetUndoMessage(opId: string): ControlMessage {
  return { type: "memory_forget_undo", payload: { op_id: opId } };
}

// --- pure state reducers（vitest 直接断言用） ---------------------------

export type PendingForget = { op_id: string; fact: FactItem };

/**
 * 收到 memory_forget_response 后该如何更新 facts + pendingForget。
 *
 * status=ok + forgotten_ids[0] 命中时：从 facts 移除 + 建 pendingForget。
 * 其它分支（error/skipped/not_found）只返回 statusText，列表不动。
 */
export function applyForgetResponse(
  facts: FactItem[],
  payload: MemoryForgetResponse["payload"],
): {
  nextFacts: FactItem[];
  pending: PendingForget | null;
  statusText: string | null;
} {
  if (payload.status !== "ok") {
    return {
      nextFacts: facts,
      pending: null,
      statusText: payload.reason
        ? `忘记失败：${payload.status} — ${payload.reason}`
        : `忘记失败：${payload.status}`,
    };
  }
  const forgottenId = payload.forgotten_ids?.[0];
  if (forgottenId == null) {
    return { nextFacts: facts, pending: null, statusText: "忘记失败：未知 id" };
  }
  const target = facts.find((f) => f.id === forgottenId);
  if (!target) {
    return { nextFacts: facts, pending: null, statusText: null };
  }
  const nextFacts = facts.filter((f) => f.id !== forgottenId);
  const opId = payload.op_id ?? "";
  return {
    nextFacts,
    pending: opId ? { op_id: opId, fact: target } : null,
    statusText: null,
  };
}

/**
 * 收到 memory_forget_undo_response 后该如何还原 facts。
 *
 * status=ok 时把 pendingForget.fact 加回列表头部；其它分支只清 pending。
 */
export function applyUndoResponse(
  facts: FactItem[],
  pending: PendingForget | null,
  payload: MemoryForgetUndoResponse["payload"],
): {
  nextFacts: FactItem[];
  pending: PendingForget | null;
  statusText: string | null;
} {
  if (payload.status === "ok" && pending) {
    return {
      nextFacts: [pending.fact, ...facts],
      pending: null,
      statusText: null,
    };
  }
  return {
    nextFacts: facts,
    pending: null,
    statusText:
      payload.status === "expired"
        ? "撤销窗口已过期（>5s）"
        : payload.reason
          ? `撤销失败：${payload.reason}`
          : null,
  };
}

export function MemoryPanel({ open, onClose, sessionId, getChannel }: Props) {
  const [view, setView] = useState<PanelView>("turns");

  // --- Conversation-history (legacy) state ------------------------------
  const [turns, setTurns] = useState<StoredTurn[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [scope, setScope] = useState<MemoryScope>("session");
  // WI-M1.1 评估反馈回路：记录每条 turn 已给的反馈（1=👍 / -1=👎），
  // 用于按钮高亮 + 防重复点。
  const [feedbackGiven, setFeedbackGiven] = useState<Record<number, 1 | -1>>(
    {},
  );

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

  // --- WI-S2.1b facts view + 🗑 + 5s undo -------------------------------
  const [facts, setFacts] = useState<FactItem[]>([]);
  const [factsReason, setFactsReason] = useState<string | null>(null);
  const [pendingForget, setPendingForget] = useState<PendingForget | null>(
    null,
  );
  const [forgetDeadline, setForgetDeadline] = useState<number | null>(null);
  // 显示用倒计时（每 250ms 刷新一次），不参与超时判断
  const [forgetRemainingMs, setForgetRemainingMs] = useState<number>(0);
  const pendingForgetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  const clearPendingForgetTimer = useCallback(() => {
    if (pendingForgetTimerRef.current) {
      clearTimeout(pendingForgetTimerRef.current);
      pendingForgetTimerRef.current = null;
    }
  }, []);

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
        case "memory_thumbs_up_response": {
          const m = msg as MemoryThumbsUpResponse;
          if (m.payload.ok) {
            setStatus(`反馈已记录 (#${m.payload.feedback_id})`);
          } else {
            setStatus(
              m.payload.reason === "feedback_loop_disabled"
                ? "反馈回路未启用（[memory.v2] feedback_loop=false）"
                : `反馈记录失败：${m.payload.reason ?? "unknown"}`,
            );
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
        case "memory_facts_list_response": {
          const m = msg as MemoryFactsListResponse;
          // 后端按 updated_at DESC 排好；再排一次纯保险。
          const sorted = [...m.payload.facts].sort(
            (a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0),
          );
          setFacts(sorted);
          setFactsReason(m.payload.reason ?? null);
          break;
        }
        case "memory_forget_response": {
          const m = msg as MemoryForgetResponse;
          // 用 functional setFacts 拿到最新 facts，再函数式更新 pending
          setFacts((prev) => {
            const r = applyForgetResponse(prev, m.payload);
            if (r.pending) {
              clearPendingForgetTimer();
              setPendingForget(r.pending);
              setForgetDeadline(Date.now() + FORGET_UNDO_WINDOW_MS);
              setForgetRemainingMs(FORGET_UNDO_WINDOW_MS);
              pendingForgetTimerRef.current = setTimeout(() => {
                setPendingForget(null);
                setForgetDeadline(null);
                setForgetRemainingMs(0);
              }, FORGET_UNDO_WINDOW_MS);
            }
            if (r.statusText) setStatus(r.statusText);
            return r.nextFacts;
          });
          break;
        }
        case "memory_forget_undo_response": {
          const m = msg as MemoryForgetUndoResponse;
          clearPendingForgetTimer();
          setForgetDeadline(null);
          setForgetRemainingMs(0);
          // 用 functional updater + 当前 pending 闭包：onMessage 重新订阅时
          // 闭包会随 useEffect 依赖刷新（pendingForget 进依赖数组）
          setPendingForget((curPending) => {
            setFacts((prev) => {
              const r = applyUndoResponse(prev, curPending, m.payload);
              if (r.statusText) setStatus(r.statusText);
              return r.nextFacts;
            });
            return null;
          });
          break;
        }
      }
    });
    return unsub;
  }, [open, getChannel, l1Target, clearPendingForgetTimer]);

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

  // --- Facts fetch on tab enter (WI-S2.1b) -------------------------------
  const refreshFacts = useCallback(() => {
    getChannel()?.send(buildMemoryFactsListMessage({ limit: 200 }));
  }, [getChannel]);

  useEffect(() => {
    if (open && view === "facts") refreshFacts();
  }, [open, view, refreshFacts]);

  // Tick countdown for the undo toast (display only)
  useEffect(() => {
    if (!forgetDeadline) return;
    const id = setInterval(() => {
      const remaining = Math.max(0, forgetDeadline - Date.now());
      setForgetRemainingMs(remaining);
      if (remaining === 0) clearInterval(id);
    }, 250);
    return () => clearInterval(id);
  }, [forgetDeadline]);

  // Clean up dangling timer on unmount / panel close
  useEffect(() => {
    return () => clearPendingForgetTimer();
  }, [clearPendingForgetTimer]);

  // --- Handlers ---------------------------------------------------------
  const handleDelete = (id: number) => {
    getChannel()?.send({ type: "memory_delete", payload: { id } });
  };

  // WI-M1.1：对某条 assistant 回复点 👍/👎。query 取该回复前最近一条
  // user turn 的内容（best-effort，召回质量分析需要 query 上下文）。
  const handleFeedback = (turnIndex: number, helpful: boolean) => {
    const turn = turns[turnIndex];
    if (!turn) return;
    let query = "";
    for (let i = turnIndex - 1; i >= 0; i--) {
      if (turns[i].role === "user") {
        query = turns[i].content;
        break;
      }
    }
    getChannel()?.send({
      type: "memory_thumbs_up",
      payload: { msg_id: turn.id, query, helpful },
    });
    setFeedbackGiven((prev) => ({ ...prev, [turn.id]: helpful ? 1 : -1 }));
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

  // --- WI-S2.1b handlers ----------------------------------------------
  const handleFactForget = (factId: number) => {
    getChannel()?.send(buildMemoryForgetMessage(factId));
  };

  const handleFactUndo = () => {
    if (!pendingForget) return;
    getChannel()?.send(buildMemoryForgetUndoMessage(pendingForget.op_id));
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
            {view === "facts" ? `事实 · ${facts.length}` : ""}
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
      {/* Top-level view tabs — 对话 / L1 / 搜索 / 技能 / 事实 (5 项)。
          Stage 2 round2 真测试 fix：panel 宽 ~200px 装不下 5 个中文 tab,
          原 inline-flex 让第 5 个"事实"被父 overflow 隐藏。改成
          flex-wrap + alignSelf:stretch 让它换行显示。 */}
      <div
        style={{
          ...segGroup,
          alignSelf: "stretch",
          display: "flex",
          flexWrap: "wrap",
        }}
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
        <button
          data-testid="memory-view-facts"
          role="tab"
          aria-selected={view === "facts"}
          onClick={() => setView("facts")}
          style={tabStyle(view === "facts")}
        >
          事实
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
            {turns.map((t, idx) => (
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
                {/* WI-M1.1：只给 assistant 回复挂 👍/👎 —— 用户对桌宠
                    回复的满意度才是召回质量信号。 */}
                {t.role === "assistant" && (
                  <>
                    <button
                      data-testid={`memory-feedback-up-${t.id}`}
                      onClick={() => handleFeedback(idx, true)}
                      style={{
                        ...btnStyle(feedbackGiven[t.id] === 1 ? "#16a34a" : "#334155"),
                        padding: "1px 6px",
                        fontSize: "10px",
                        flexShrink: 0,
                      }}
                      title="这条回复有帮助"
                      aria-label={`turn ${t.id} 有帮助`}
                    >
                      👍
                    </button>
                    <button
                      data-testid={`memory-feedback-down-${t.id}`}
                      onClick={() => handleFeedback(idx, false)}
                      style={{
                        ...btnStyle(feedbackGiven[t.id] === -1 ? "#dc2626" : "#334155"),
                        padding: "1px 6px",
                        fontSize: "10px",
                        flexShrink: 0,
                      }}
                      title="这条回复没帮助"
                      aria-label={`turn ${t.id} 没帮助`}
                    >
                      👎
                    </button>
                  </>
                )}
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

      {/* --- 事实 view (WI-S2.1b) ------------------------------------ */}
      {view === "facts" && (
        <>
          <div style={{ display: "flex", gap: "4px", marginBottom: "6px" }}>
            <button
              data-testid="facts-refresh"
              onClick={refreshFacts}
              style={btnStyle("#3b82f6")}
            >
              刷新
            </button>
            <span
              style={{ alignSelf: "center", opacity: 0.55, fontSize: "10px" }}
            >
              共 {facts.length} 条
            </span>
          </div>
          {factsReason && (
            <div
              style={{ opacity: 0.6, fontSize: "10px", marginBottom: "4px" }}
            >
              后端提示：{factsReason}
            </div>
          )}
          {status && (
            <div
              style={{ opacity: 0.75, marginBottom: "6px", fontSize: "11px" }}
            >
              {status}
            </div>
          )}
          <div style={listStyle}>
            {facts.length === 0 && (
              <div style={emptyStyle}>(暂无事实)</div>
            )}
            {facts.map((f) => (
              <div
                key={f.id}
                data-testid={`fact-row-${f.id}`}
                data-fact-category={f.category}
                style={{ ...rowStyle, flexDirection: "column", alignItems: "stretch" }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span style={categoryBadgeStyle} title={f.category}>
                    {f.category}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontSize: 12,
                    }}
                  >
                    <strong>{f.key}</strong>
                    <span style={{ opacity: 0.55 }}>: </span>
                    <span>{f.value}</span>
                  </span>
                  <button
                    data-testid={`fact-forget-${f.id}`}
                    onClick={() => handleFactForget(f.id)}
                    style={{
                      ...btnStyle("#991b1b"),
                      padding: "1px 6px",
                      fontSize: "12px",
                      flexShrink: 0,
                    }}
                    title={`忘记 fact #${f.id}`}
                    aria-label={`忘记事实 ${f.key}`}
                  >
                    🗑
                  </button>
                </div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    opacity: 0.5,
                    fontSize: "10px",
                    marginTop: 2,
                  }}
                >
                  <span>{f.subject || "(no subject)"}</span>
                  <span>
                    更新于 {new Date((f.updated_at || 0) * 1000).toLocaleString()}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {pendingForget && (
            <div data-testid="fact-undo-toast" style={undoToastStyle}>
              <span style={{ flex: 1 }}>
                已忘记 <strong>{pendingForget.fact.key}</strong>:{" "}
                {pendingForget.fact.value}，撤销？
              </span>
              <span
                data-testid="fact-undo-remaining"
                style={{ opacity: 0.6, fontSize: 10 }}
              >
                {Math.ceil(forgetRemainingMs / 1000)}s
              </span>
              <button
                data-testid="fact-undo-btn"
                onClick={handleFactUndo}
                style={btnStyle("#3b82f6")}
              >
                撤销
              </button>
            </div>
          )}
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

const categoryBadgeStyle: React.CSSProperties = {
  display: "inline-block",
  padding: "1px 6px",
  color: "#7fb0ff",
  fontSize: "10px",
  background: "rgba(79,147,255,0.16)",
  border: `1px solid ${dark.border}`,
  borderRadius: 5,
  flexShrink: 0,
};

const undoToastStyle: React.CSSProperties = {
  position: "absolute",
  left: 12,
  right: 12,
  bottom: 12,
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 10px",
  borderRadius: 8,
  background: "rgba(30, 30, 45, 0.96)",
  border: `1px solid ${dark.border}`,
  fontSize: 11,
  boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
  zIndex: 5,
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
