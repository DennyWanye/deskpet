// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S23 / WI-T2-B1 v2 — bottom input bar for the code panel.
 *
 * Multi-line textarea with Enter-to-send (Shift+Enter newline).
 *
 * v2 升级（plans/2026-05-25-companion-code-skill-upgrade/10-tool-layer-...）:
 *  - 输入 `/` → fetch /api/commands/help → 显示 filterable SlashDropdown
 *  - ↑/↓ 在 dropdown 移动；Tab/Enter 接受；ESC 关闭
 *  - 接受后显示 ArgHintBar 显参数 inline
 *  - 输入历史：空输入 + ↑ → 浏览 last /命令 (max 50)
 *  - 普通聊天 (不以 / 开头) 行为不变 — backward compatible
 *
 * Concurrency-limited via `chatLimiter` so 5 tiles all sending at once
 * won't smash the relay with parallel requests.
 */
import { useState, useCallback, useRef, useEffect } from "react";

import { useSessionsStore, chatLimiter } from "../stores/sessionsStore";
import { BACKEND_PORT } from "../backendPort";
import { codePanelWS } from "./ws";
import { SlashDropdown, type SlashCommand } from "./SlashDropdown";
import { ArgHintBar, type ArgSchema } from "./ArgHintBar";

// 输入历史 — module-scope，跨 InputBar 实例共享 (max 50 entries)
const _slashInputHistory: string[] = [];
const HISTORY_MAX = 50;

function pushHistory(entry: string) {
  if (!entry.startsWith("/")) return;
  if (_slashInputHistory[_slashInputHistory.length - 1] === entry) return;
  _slashInputHistory.push(entry);
  while (_slashInputHistory.length > HISTORY_MAX) _slashInputHistory.shift();
}

// commands 缓存 (页面级；后端可 reload skill 触发刷新)
let _cachedCommands: SlashCommand[] | null = null;
let _cachedCommandsPromise: Promise<SlashCommand[]> | null = null;

async function fetchCommands(): Promise<SlashCommand[]> {
  if (_cachedCommands !== null) return _cachedCommands;
  if (_cachedCommandsPromise !== null) return _cachedCommandsPromise;
  _cachedCommandsPromise = (async () => {
    try {
      // WI-T2-B fix v2.1: backend 绝对 URL，复用 backendPort.ts 单一源.
      // 相对路径在 Tauri WebView2 (tauri://) 或 vite dev 跨 5473→8400 都失效；
      // 必须显式 http://127.0.0.1:${BACKEND_PORT}/api/... 走 CORS.
      const resp = await fetch(
        `http://127.0.0.1:${BACKEND_PORT}/api/commands/help`,
      );
      if (!resp.ok) return [];
      const data = await resp.json();
      const out: SlashCommand[] = Array.isArray(data.commands) ? data.commands : [];
      _cachedCommands = out;
      return out;
    } catch {
      return [];
    }
  })();
  return _cachedCommandsPromise;
}

function filterCommands(all: SlashCommand[], q: string): SlashCommand[] {
  if (!q) return all;
  const lower = q.toLowerCase();
  // 排序：prefix-match 优先，substring-match 次之
  const prefix = all.filter((c) => c.name.toLowerCase().startsWith(lower));
  const substr = all.filter(
    (c) =>
      !c.name.toLowerCase().startsWith(lower) &&
      c.name.toLowerCase().includes(lower),
  );
  return [...prefix, ...substr];
}

export function InputBar({
  placeholder,
  sessionId,
  leftAccessory,
}: {
  placeholder?: string;
  sessionId?: string;
  leftAccessory?: React.ReactNode;
} = {}) {
  const [text, set_text] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // v2 slash state
  const [allCommands, setAllCommands] = useState<SlashCommand[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [argHintCmd, setArgHintCmd] = useState<SlashCommand | null>(null);
  const [historyIdx, setHistoryIdx] = useState<number | null>(null);

  const active_sid = useSessionsStore((s) => s.active_sid);
  const sid = sessionId ?? active_sid;
  const session = useSessionsStore((s) => s.sessions[sessionId ?? s.active_sid]);
  const inflight_count = useSessionsStore((s) => s.inflight_count);
  const inflight_max = useSessionsStore((s) => s.inflight_max);

  // 挂载时 fetch commands (一次性；缓存命中即返)
  useEffect(() => {
    fetchCommands().then(setAllCommands).catch(() => setAllCommands([]));
  }, []);

  // Auto-grow textarea
  useEffect(() => {
    if (!taRef.current) return;
    taRef.current.style.height = "auto";
    taRef.current.style.height = Math.min(taRef.current.scrollHeight, 120) + "px";
  }, [text]);

  // 计算当前 filter + candidates
  const candidates: SlashCommand[] = (() => {
    if (!dropdownOpen) return [];
    if (!text.startsWith("/")) return [];
    const q = text.slice(1).split(/\s+/)[0] ?? "";
    return filterCommands(allCommands, q);
  })();

  // 计算 current arg index (空格数)
  const currentArgIndex = (() => {
    if (!argHintCmd) return 0;
    // text 形如 "/cmd arg1 arg2 ..."
    const parts = text.split(/\s+/);
    return Math.max(0, parts.length - 2);
  })();

  const acceptCandidate = useCallback(
    (idx: number) => {
      const cmd = candidates[idx];
      if (!cmd) return;
      set_text(`/${cmd.name} `);
      setDropdownOpen(false);
      setSelectedIdx(0);
      const hasArgs = cmd.args_schema && cmd.args_schema.length > 0;
      setArgHintCmd(hasArgs ? cmd : null);
      // 重新 focus 让 textarea 接收后续键入
      taRef.current?.focus();
    },
    [candidates],
  );

  const send = useCallback(async () => {
    const t = text.trim();
    if (!t) return;
    if (!sid) return;
    pushHistory(t);
    setHistoryIdx(null);
    set_text("");
    setDropdownOpen(false);
    setArgHintCmd(null);
    useSessionsStore.getState().push_message(sid, { role: "user", text: t });
    if (t.startsWith("/")) {
      const m = t.slice(1).match(/^(\S+)\s*(.*)$/);
      const cmd = m ? m[1] : "";
      const args = m ? (m[2] ?? "") : "";
      useSessionsStore.getState().upsert(sid, {
        status: "thinking",
        inflight: true,
      });
      codePanelWS.send({
        type: "slash_command",
        payload: { command: cmd, args, session_id: sid },
      });
      return;
    }
    useSessionsStore.getState().upsert(sid, {
      status: "thinking",
      inflight: true,
    });
    void chatLimiter.run(async () => {
      codePanelWS.send({
        type: "chat_v2",
        payload: { text: t, session_id: sid },
      });
    });
  }, [text, sid]);

  const stop = useCallback(() => {
    if (!sid) return;
    codePanelWS.send({
      type: "chat_v2_interrupt",
      payload: { session_id: sid },
    });
    useSessionsStore.getState().upsert(sid, { inflight: false, status: "idle" });
  }, [sid]);

  const onChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    set_text(v);
    setHistoryIdx(null);
    // 状态机：开/关 dropdown + arg hint
    if (v.startsWith("/")) {
      const firstWord = v.slice(1).split(/\s+/)[0] ?? "";
      const hasSpace = v.length > firstWord.length + 1;
      if (!hasSpace) {
        // 还在打命令名 → 显 dropdown
        setDropdownOpen(true);
        setArgHintCmd(null);
        setSelectedIdx(0);
      } else {
        // 已输空格 → 关 dropdown，看是否需 arg hint
        setDropdownOpen(false);
        const cmdMatch = allCommands.find(
          (c) => c.name.toLowerCase() === firstWord.toLowerCase(),
        );
        if (cmdMatch && cmdMatch.args_schema && cmdMatch.args_schema.length > 0) {
          setArgHintCmd(cmdMatch);
        } else {
          setArgHintCmd(null);
        }
      }
    } else {
      setDropdownOpen(false);
      setArgHintCmd(null);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME composition — never interfere
    if (e.nativeEvent.isComposing) return;

    // dropdown 打开时拦截 ↑↓ Tab Enter ESC
    if (dropdownOpen && candidates.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((i) => (i + 1) % candidates.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx((i) => (i - 1 + candidates.length) % candidates.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        acceptCandidate(selectedIdx);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDropdownOpen(false);
        return;
      }
    }

    // 历史浏览 — 空输入 + ↑ → 上一条 history
    if (!dropdownOpen && e.key === "ArrowUp" && _slashInputHistory.length > 0) {
      const ta = e.currentTarget;
      const atTop = ta.selectionStart === 0 && ta.selectionEnd === 0;
      // 仅在空输入 或 光标在最顶且无 selection 时启 history
      if (text === "" || atTop) {
        e.preventDefault();
        const nextIdx =
          historyIdx === null
            ? _slashInputHistory.length - 1
            : Math.max(0, historyIdx - 1);
        set_text(_slashInputHistory[nextIdx]);
        setHistoryIdx(nextIdx);
        return;
      }
    }
    if (!dropdownOpen && e.key === "ArrowDown" && historyIdx !== null) {
      e.preventDefault();
      const nextIdx = historyIdx + 1;
      if (nextIdx >= _slashInputHistory.length) {
        set_text("");
        setHistoryIdx(null);
      } else {
        set_text(_slashInputHistory[nextIdx]);
        setHistoryIdx(nextIdx);
      }
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      // Bug#2 修复 (2026-06-11)：inflight 时 Enter 原来是 stop() —— 打好的
      // 字既不发送也不排队还静默打断当前 turn(真机 4 次复现"消息被吞")。
      // 改为:有文字 → 照常发送(后端同 sid 抢占,新消息取代旧 turn);
      // 空文字 + inflight → 才是停止。
      if (text.trim()) {
        void send();
      } else if (session?.inflight) {
        stop();
      }
    }
  };

  const status = session?.status ?? "idle";
  const queued = Math.max(0, inflight_count - inflight_max);
  const inflight = !!session?.inflight;

  return (
    <div
      style={{
        position: "relative",  // for absolute SlashDropdown
        borderTop: "1px solid rgba(148, 163, 184, 0.18)",
        background: "rgba(15, 18, 28, 0.95)",
        padding: "10px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      {argHintCmd && (
        <ArgHintBar
          commandName={argHintCmd.name}
          argSchema={(argHintCmd.args_schema ?? []) as ArgSchema[]}
          currentArgIndex={currentArgIndex}
        />
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", position: "relative" }}>
        <SlashDropdown
          candidates={candidates}
          selectedIdx={selectedIdx}
          onAccept={acceptCandidate}
        />
        {leftAccessory}
        <textarea
          ref={taRef}
          value={text}
          onChange={onChange}
          onKeyDown={onKeyDown}
          placeholder={
            placeholder ??
            (session?.project_root
              ? `跟 LLM 说点什么 — 当前项目: ${session.project_name}（输 / 命令）`
              : "输入消息开始 Code 模式聊天... (输 / 弹命令补全)")
          }
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            background: "rgba(30, 35, 48, 0.85)",
            color: "#e2e8f0",
            border: "1px solid rgba(148, 163, 184, 0.22)",
            borderRadius: 8,
            padding: "8px 10px",
            fontSize: 13,
            lineHeight: 1.5,
            fontFamily: "inherit",
            minHeight: 36,
            maxHeight: 120,
            outline: "none",
          }}
        />
        <button
          type="button"
          // Bug#2 修复：与 Enter 一致 —— 有文字永远是发送(inflight 时后端
          // 同 sid 抢占);只有空文字 + inflight 才显示/执行停止。
          onClick={() => (text.trim() ? void send() : inflight ? stop() : undefined)}
          disabled={!inflight && !text.trim()}
          style={{
            background: text.trim()
              ? "#2563eb"
              : inflight
                ? "#dc2626"
                : "rgba(148, 163, 184, 0.2)",
            color: "#fff",
            border: "none",
            borderRadius: 8,
            padding: "8px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: inflight || text.trim() ? "pointer" : "not-allowed",
            height: 36,
          }}
        >
          {text.trim() ? "发送" : inflight ? "■ 停止" : "发送"}
        </button>
      </div>
      <div
        style={{
          display: "flex",
          gap: 12,
          fontSize: 11,
          color: "#94a3b8",
        }}
      >
        <StatusPill status={status} />
        {queued > 0 && (
          <span style={{ color: "#fde68a" }}>
            等待中: {queued}（the relay 并发上限 {inflight_max}）
          </span>
        )}
        <span style={{ marginLeft: "auto", opacity: 0.6 }}>
          Enter 发送 · Shift+Enter 换行 · / 弹命令 · ↑ 历史
        </span>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string }> = {
    idle: { label: "✓ 空闲", color: "#86efac" },
    thinking: { label: "⏳ 思考中", color: "#fde68a" },
    running: { label: "🔧 工具执行中", color: "#67e8f9" },
    permission: { label: "🔒 等待授权", color: "#f59e0b" },
    error: { label: "✗ 错误", color: "#fca5a5" },
  };
  const m = map[status] ?? { label: status, color: "#94a3b8" };
  return <span style={{ color: m.color }}>{m.label}</span>;
}

// Exports for test (history + cache reset)
export const _testing = {
  pushHistory,
  getHistory: () => [..._slashInputHistory],
  clearHistory: () => {
    _slashInputHistory.length = 0;
  },
  resetCache: () => {
    _cachedCommands = null;
    _cachedCommandsPromise = null;
  },
  filterCommands,
  HISTORY_MAX,
};
