/**
 * P4-S23 — multi-session dashboard.
 *
 * Renders a responsive 4-col grid (auto-fit) of all active code
 * sessions. Each tile is a snapshot: project name + path + todos
 * progress + last assistant line + status pill. Clicking a tile
 * switches the panel's active session (no destructive action).
 *
 * Inspired by OctoAlly's "Active Sessions" view.
 */
import { useState, useRef, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useSessionsStore, chatLimiter, severity_score } from "../stores/sessionsStore";
import type {
  CodeModelParams,
  Message,
  SessionState,
  SessionStatus,
  Todo,
} from "../stores/sessionsStore";
import { codePanelWS } from "./ws";
import { ConfirmDialog } from "./ConfirmDialog";
import { ChangeModelModal } from "./ChangeModelModal";
import {
  useProvidersStore,
  build_provider_dropdown_options,
  format_provider_label,
  type ProviderEntry,
} from "./providersStore";

// ---------------------------------------------------------------------------
// multi-provider-management Phase 5 — pure helpers for the per-session
// provider dropdown. Exposed here (rather than baked into the JSX) so the
// vitest node-env tests can verify dropdown behaviour without a DOM.
// ---------------------------------------------------------------------------

/** Build the outbound `code_session_set_provider` ws message.
 * `provider_id == null` clears the binding (card falls back to Global Chain).
 */
export function buildSetProviderMessage(
  session_id: string,
  provider_id: string | null,
): { type: string; payload: Record<string, unknown> } {
  return {
    type: "code_session_set_provider",
    payload: { session_id, provider_id },
  };
}

/** Build the outbound `code_session_set_model` ws message.
 * An empty string is normalised to `null` so the backend treats it as
 * "clear preferred_model" (no accidental empty-string model id).
 *
 * code-session-model-params S2: optional structured `params`. Back-compat
 * is load-bearing — when `params` is omitted (legacy free-text callers /
 * clear-binding) the payload keeps the old `{session_id, model}` shape and
 * the backend falls back to provider defaults (no model_params row). Only
 * when the Cursor-style picker passes an explicit dict do we attach it. */
export function buildSetModelMessage(
  session_id: string,
  model: string | null,
  params?: CodeModelParams | null,
): { type: string; payload: Record<string, unknown> } {
  const normalized = model && model.trim() !== "" ? model : null;
  const payload: Record<string, unknown> = { session_id, model: normalized };
  if (params != null) {
    payload.params = params;
  }
  return { type: "code_session_set_model", payload };
}

// code-session-model-params: the model dropdown is data-driven from the
// relay's live /models catalog (see codeModelsStore +
// buildModelOptionsFromCatalog / capsForModel). The old hardcoded
// CODE_MODEL_PRESETS / buildModelOptions / buildModelParams were removed —
// a static preset list was exactly the "mock data" problem.

export interface CardDropdownDisplay {
  label: string;
  /** True when the card overrides global chain (provider_id != null). */
  locked: boolean;
  /** Visual lock indicator (🔒 when locked, "" otherwise). */
  icon: string;
}

/** Compute the dropdown's display state for one card. Pure — fed by
 * sessionsStore.provider_id and providersStore.providers. */
export function resolveCardDropdownDisplay(
  provider_id: string | null | undefined,
  providers: ProviderEntry[],
): CardDropdownDisplay {
  const has_binding = !!provider_id;
  return {
    label: format_provider_label(provider_id, providers),
    locked: has_binding,
    icon: has_binding ? "🔒" : "",
  };
}

/** Pure helper used by ws.ts when a `providers_changed` event removes a
 * provider that some cards were pinned to. Returns the new binding +
 * toast text the UI should surface. */
export function pickProviderRemovedFallback(
  removed_provider_id: string,
): { provider_id: null; toast: string } {
  return {
    provider_id: null,
    toast: `Provider ${removed_provider_id} 已删除，本会话回到全局链`,
  };
}

// P5-S3: tile border colour by severity_score. Bands match the pet
// state machine thresholds so the panel and the pet agree on "this
// session looks dangerous".
function severity_border(score: number): { color: string; pulse: boolean } {
  if (score >= 100) return { color: "rgba(239, 68, 68, 0.85)", pulse: true };
  if (score >= 60) return { color: "rgba(245, 158, 11, 0.75)", pulse: false };
  if (score >= 30) return { color: "rgba(59, 130, 246, 0.55)", pulse: false };
  return { color: "rgba(34, 197, 94, 0.40)", pulse: false };
}

export function SessionGridView({ onSelectSession }: { onSelectSession: () => void }) {
  const sessions = useSessionsStore((s) => s.sessions);
  const set_active = useSessionsStore((s) => s.set_active);

  // P5-S3: cross-window broadcast — when the user clicks the pet's
  // supervisor bubble, the pet posts `pet_focus_session_clicked` on a
  // BroadcastChannel. We listen here and switch active session +
  // surface the chat view so the user lands directly on the offending
  // session.
  useEffect(() => {
    let bc: BroadcastChannel | null = null;
    try {
      bc = new BroadcastChannel("deskpet-pet-focus");
    } catch {
      return;
    }
    const onMsg = (ev: MessageEvent) => {
      const data = ev.data;
      if (!data || data.type !== "pet_focus_session_clicked") return;
      const target = String(data.session_id || "");
      if (!target) return;
      set_active(target);
      onSelectSession();
    };
    bc.addEventListener("message", onMsg);
    return () => {
      bc?.removeEventListener("message", onMsg);
      bc?.close();
    };
  }, [set_active, onSelectSession]);
  // P4-S24 followup: confirm-delete state, identical pattern to
  // SessionSidebar. Two independent state holders is fine — only one
  // dialog can be open at a time across the panel since a click on
  // either surface triggers the same modal-style overlay.
  const [pending_delete, set_pending_delete] = useState<{
    sid: string;
    name: string;
  } | null>(null);

  const list = Object.values(sessions).filter(
    (s) => s.code_session_id || s.base_session_id !== "default",
  );

  return (
    <div
      style={{
        flex: 1,
        background: "#0f1218",
        padding: 16,
        overflowY: "auto",
        color: "#e2e8f0",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          marginBottom: 16,
          gap: 12,
        }}
      >
        <h2 style={{ fontSize: 16, margin: 0 }}>
          Active Code Sessions ({list.length})
        </h2>
        <button
          onClick={() => void newProject(onSelectSession)}
          style={primaryBtn}
        >
          + 新项目
        </button>
        <button onClick={() => void refresh()} style={secondaryBtn}>
          ↻ 刷新
        </button>
      </header>

      {list.length === 0 ? (
        <div style={emptyHint}>
          欢迎进入 Code 模式 ✨<br />
          <br />
          点上方 <strong>"+ 新项目"</strong> 选一个文件夹开始。<br />
          DeskPet 会用 LLM + 工具帮你写代码、查文件、跑命令。
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 12,
          }}
        >
          {list.map((s) => (
            <Tile
              key={s.base_session_id}
              session_id={s.base_session_id}
              project_name={s.project_name}
              project_root={s.project_root}
              status={s.status}
              todos={s.todos}
              messages={s.messages}
              inflight={!!s.inflight}
              last_activity={s.last_activity}
              session={s}
              onOpenFull={() => {
                set_active(s.base_session_id);
                onSelectSession();
              }}
              onDelete={() =>
                set_pending_delete({
                  sid: s.base_session_id,
                  name: s.project_name,
                })
              }
            />
          ))}
        </div>
      )}

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
            useSessionsStore.getState().remove(pending_delete.sid);
            set_pending_delete(null);
          }}
          onCancel={() => set_pending_delete(null)}
        />
      )}
    </div>
  );
}

interface TileProps {
  session_id: string;
  project_name: string;
  project_root: string | null;
  status: SessionStatus;
  todos: Todo[];
  messages: Message[];
  inflight: boolean;
  last_activity: number;
  /** P5-S3: pass the full session record so we can compute severity for
   * the tile border colour without re-deriving from props. */
  session: SessionState;
  /** Open the project in the full single-chat view. */
  onOpenFull: () => void;
  onDelete: () => void;
}

function Tile({
  session_id,
  project_name,
  project_root,
  status,
  todos,
  messages,
  inflight,
  last_activity,
  session,
  onOpenFull,
  onDelete,
}: TileProps) {
  // P5-S3 — severity-driven border. Recomputes whenever session state
  // changes (zustand selector subscription via parent already triggers
  // re-render of the grid).
  const _score = severity_score(session);
  const _sev_border = severity_border(_score);
  // P4-S25 B2: per-card chat input. Each tile is now a mini chat panel
  // — todos list + recent messages + input box — so users can fan
  // commands across multiple projects without leaving the dashboard.
  const [text, set_text] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  // multi-provider-management Phase 5 — per-session provider dropdown +
  // change-model modal. Both source from providersStore (Phase 4 owns the
  // list state); writes go through buildSet* helpers tested above.
  const providers = useProvidersStore((s) => s.providers);
  const dropdown_opts = build_provider_dropdown_options(providers);
  const card_display = resolveCardDropdownDisplay(session.provider_id, providers);
  const [show_model_modal, set_show_model_modal] = useState(false);

  const on_provider_change = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const raw = e.target.value;
    // Dropdown is provider-selection ONLY now. Model+params editing
    // moved to the clickable model chip (next to it). No more
    // "✏️ 改 model..." sentinel polluting the provider list.
    const next: string | null = raw === "" ? null : raw;
    // Optimistic UI update — backend ack via code_session_provider_set
    // reconciles.
    useSessionsStore.getState().upsert(session_id, { provider_id: next });
    codePanelWS.send(buildSetProviderMessage(session_id, next));
  };

  useEffect(() => {
    if (!taRef.current) return;
    taRef.current.style.height = "auto";
    taRef.current.style.height = Math.min(taRef.current.scrollHeight, 80) + "px";
  }, [text]);

  const send = () => {
    const t = text.trim();
    if (!t) return;
    set_text("");
    useSessionsStore.getState().push_message(session_id, {
      role: "user",
      text: t,
    });
    useSessionsStore.getState().upsert(session_id, {
      status: "thinking",
      inflight: true,
    });
    void chatLimiter.run(async () => {
      codePanelWS.send({
        type: "chat_v2",
        payload: { text: t, session_id },
      });
    });
  };

  const stop = () => {
    codePanelWS.send({
      type: "chat_v2_interrupt",
      payload: { session_id },
    });
    useSessionsStore.getState().upsert(session_id, { inflight: false, status: "idle" });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (inflight) stop();
      else send();
    }
  };

  const todos_done = todos.filter((t) => t.status === "completed").length;
  // Last 4 messages, chronological. Hide tool_call/tool_result clutter
  // — keep just user / assistant / error / streaming previews.
  //
  // P6 bugfix 2026-05-13: also hide synthetic sentinel triggers
  // (`<<auto_resume>>`, `<<supervisor_followup>>`) that pre-fix
  // backend wrote into SessionDB as `role=user`. The backend write
  // path is now patched, but historical rows still surface them and
  // the UI must never render them as "You:" — they're internal
  // dispatch tokens, not user speech.
  const is_sentinel = (text: unknown): boolean =>
    typeof text === "string" &&
    /^<<[a-z_]+>>$/i.test(text.trim());
  // P6 bugfix 2026-05-13: thinking-mode models (deepseek-v4-pro, GLM-4.5,
  // the relay proxy etc.) emit `<think>...</think>` chain-of-thought inline
  // in the assistant `content` field. Backend supervisor/plan already
  // strip these for JSON parsing, but the chat stream doesn't — so users
  // see "AI: <think>Let me check the ChapterRead page…</think>" raw.
  // Strip on the render side as a defensive layer; backend may also
  // start stripping pre-stream later.
  const strip_think = (text: unknown): string => {
    if (typeof text !== "string") return "";
    // Remove completed <think>...</think> blocks (greedy across newlines).
    let out = text.replace(/<think>[\s\S]*?<\/think>/gi, "");
    // Handle in-progress streaming: a leading <think> with no close yet.
    out = out.replace(/<think>[\s\S]*$/i, "");
    return out.trim();
  };
  const visible = messages
    .filter(
      (m) =>
        (m.role === "user" ||
          m.role === "assistant" ||
          m.role === "assistant_delta" ||
          m.role === "error") &&
        !(m.role === "user" && is_sentinel(m.text)),
    )
    .slice(-3);

  return (
    <div
      data-severity-score={Math.round(_score)}
      style={{
        background: "rgba(20, 24, 35, 0.95)",
        border: `1.5px solid ${_sev_border.color}`,
        borderRadius: 8,
        padding: 12,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        transition: "border-color 200ms",
        animation: _sev_border.pulse
          ? "deskpet-tile-pulse 1s ease-in-out infinite"
          : undefined,
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLDivElement).style.borderColor =
          "rgba(37, 99, 235, 0.5)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.borderColor = _sev_border.color;
      }}
    >
      <style>{`
        @keyframes deskpet-tile-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
          50%      { box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.0); }
        }
      `}</style>
      {/* Header: name + status + actions */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span
          style={{
            fontSize: 13.5,
            fontWeight: 600,
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            cursor: "pointer",
          }}
          title="打开完整 chat"
          onClick={onOpenFull}
        >
          {project_name}
        </span>
        {_score >= 60 && (
          <span
            title={`severity: ${Math.round(_score)}`}
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: "1px 5px",
              borderRadius: 3,
              background: _score >= 100 ? "rgba(239, 68, 68, 0.25)" : "rgba(245, 158, 11, 0.20)",
              color: _score >= 100 ? "#fca5a5" : "#fde68a",
            }}
          >
            {_score >= 100 ? "🚨" : "⚠"} {Math.round(_score)}
          </span>
        )}
        <StatusBadge status={status} />
        <button
          type="button"
          title="打开完整 chat"
          aria-label={`打开 ${project_name} 完整 chat`}
          onClick={onOpenFull}
          style={tileExpandBtnStyle}
        >
          ⤢
        </button>
        <button
          type="button"
          title="删除项目"
          aria-label={`删除项目 ${project_name}`}
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          style={tileDeleteBtnStyle}
        >
          🗑️
        </button>
      </header>

      {/* Path */}
      <div
        style={{
          fontSize: 10.5,
          color: "#94a3b8",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={project_root ?? ""}
      >
        {project_root ?? "(无路径)"}
      </div>

      {/* multi-provider-management Phase 5 — provider/model dropdown */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          fontSize: 10.5,
        }}
        title={
          card_display.locked
            ? `本会话固定使用 ${card_display.label}`
            : "本会话跟随全局 chain"
        }
      >
        <span style={{ color: "#94a3b8" }}>
          {card_display.icon ? `${card_display.icon} ` : ""}provider:
        </span>
        <select
          aria-label={`provider for ${project_name}`}
          value={session.provider_id ?? ""}
          onChange={on_provider_change}
          style={dropdownStyle}
        >
          {dropdown_opts.map((opt) => (
            <option key={opt.value ?? "__global__"} value={opt.value ?? ""}>
              {opt.label}
            </option>
          ))}
        </select>
        {/* Clickable model chip → opens the model+params editor. Always
            shown (even unbound = "默认模型") so it's discoverable. */}
        <button
          type="button"
          onClick={() => set_show_model_modal(true)}
          title={
            session.preferred_model
              ? `点击编辑模型与参数（当前 ${session.preferred_model}）`
              : "点击选择模型与参数（当前跟随默认）"
          }
          aria-label={`编辑 ${project_name} 的模型与参数`}
          style={{
            fontSize: 10,
            color: session.preferred_model ? "#cbd5e1" : "#94a3b8",
            background: session.preferred_model
              ? "rgba(37, 99, 235, 0.22)"
              : "rgba(148, 163, 184, 0.14)",
            border: "1px solid rgba(96, 165, 250, 0.35)",
            padding: "1px 6px",
            borderRadius: 3,
            maxWidth: 150,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            cursor: "pointer",
          }}
        >
          {session.preferred_model
            ? `${session.preferred_model} ✎`
            : "默认模型 ✎"}
        </button>
      </div>

      {/* Todos block */}
      <div
        style={{
          fontSize: 11,
          color: "#cbd5e1",
          background: "rgba(15, 18, 28, 0.55)",
          borderRadius: 6,
          padding: "6px 8px",
          maxHeight: 96,
          overflowY: "auto",
        }}
      >
        <div style={{ fontSize: 10.5, color: "#94a3b8", marginBottom: 2 }}>
          📋 todos {todos_done}/{todos.length}
        </div>
        {todos.length === 0 ? (
          <div style={{ fontSize: 10.5, color: "rgba(148,163,184,0.55)", fontStyle: "italic" }}>
            （让 LLM 用 todo_write 拆步骤）
          </div>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {todos.map((t, i) => (
              <li
                key={i}
                style={{
                  padding: "1px 0",
                  fontSize: 11,
                  color:
                    t.status === "in_progress"
                      ? "#fde68a"
                      : t.status === "completed"
                        ? "rgba(167,243,208,0.85)"
                        : "rgba(255,255,255,0.78)",
                  textDecoration:
                    t.status === "completed" ? "line-through" : "none",
                }}
              >
                {t.status === "in_progress" ? "⏳" : t.status === "completed" ? "✓" : "○"}{" "}
                {t.status === "in_progress" ? t.activeForm : t.content}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Recent messages preview (last 3) */}
      <div
        data-bp-selectable=""
        style={{
          fontSize: 11.5,
          color: "rgba(255, 255, 255, 0.85)",
          lineHeight: 1.45,
          maxHeight: 110,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {visible.length === 0 ? (
          <span style={{ color: "rgba(148,163,184,0.55)", fontStyle: "italic" }}>
            (暂无消息)
          </span>
        ) : (
          visible.map((m) => (
            <div key={m.id} style={tilePreviewLineStyle(m.role)}>
              <strong style={{ fontSize: 10, color: "#94a3b8", marginRight: 4 }}>
                {m.role === "user" ? "You" : m.role === "error" ? "Err" : "AI"}:
              </strong>
              {(() => {
                const display = m.role === "user" ? (m.text ?? "") : strip_think(m.text);
                return (
                  <>
                    {display.slice(0, 200)}
                    {display.length > 200 ? "…" : ""}
                  </>
                );
              })()}
            </div>
          ))
        )}
      </div>

      {/* Input bar */}
      <div style={{ display: "flex", gap: 6, alignItems: "flex-end" }}>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => set_text(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={`给 ${project_name} 发指令...`}
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            background: "rgba(30, 35, 48, 0.85)",
            color: "#e2e8f0",
            border: "1px solid rgba(148, 163, 184, 0.22)",
            borderRadius: 6,
            padding: "5px 8px",
            fontSize: 12,
            lineHeight: 1.4,
            fontFamily: "inherit",
            minHeight: 28,
            maxHeight: 80,
            outline: "none",
          }}
        />
        <button
          type="button"
          onClick={() => (inflight ? stop() : send())}
          disabled={!inflight && !text.trim()}
          style={{
            background: inflight
              ? "#dc2626"
              : text.trim()
                ? "#2563eb"
                : "rgba(148, 163, 184, 0.2)",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            padding: "5px 10px",
            fontSize: 11.5,
            fontWeight: 600,
            cursor: inflight || text.trim() ? "pointer" : "not-allowed",
            height: 28,
            whiteSpace: "nowrap",
          }}
        >
          {inflight ? "■ 停止" : "发送"}
        </button>
      </div>

      <div style={{ fontSize: 10, color: "#64748b" }}>
        {formatTime(last_activity)}
      </div>

      {show_model_modal && (
        <ChangeModelModal
          session_id={session_id}
          current_model={session.preferred_model}
          current_params={session.model_params}
          onClose={() => set_show_model_modal(false)}
        />
      )}
    </div>
  );
}

function tilePreviewLineStyle(role: string): React.CSSProperties {
  return {
    fontSize: 11.5,
    color:
      role === "user"
        ? "#cbd5e1"
        : role === "error"
          ? "#fca5a5"
          : "rgba(255,255,255,0.85)",
    overflow: "hidden",
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical",
    wordBreak: "break-word",
  };
}

function StatusBadge({ status }: { status: SessionStatus }) {
  const map: Record<SessionStatus, { label: string; bg: string; fg: string }> = {
    idle:       { label: "idle",      bg: "rgba(148, 163, 184, 0.18)", fg: "#cbd5e1" },
    thinking:   { label: "thinking",  bg: "rgba(245, 158, 11, 0.22)", fg: "#fde68a" },
    running:    { label: "running",   bg: "rgba(6, 182, 212, 0.22)", fg: "#67e8f9" },
    permission: { label: "permission", bg: "rgba(245, 158, 11, 0.30)", fg: "#fbbf24" },
    error:      { label: "error",     bg: "rgba(220, 38, 38, 0.22)", fg: "#fca5a5" },
  };
  const m = map[status];
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 600,
        padding: "2px 6px",
        borderRadius: 3,
        background: m.bg,
        color: m.fg,
      }}
    >
      {m.label}
    </span>
  );
}

function formatTime(ts: number): string {
  const dt = new Date(ts);
  const now = Date.now();
  const diff_s = Math.floor((now - ts) / 1000);
  if (diff_s < 60) return "刚刚";
  if (diff_s < 3600) return `${Math.floor(diff_s / 60)} 分钟前`;
  if (diff_s < 86400) return `${Math.floor(diff_s / 3600)} 小时前`;
  return dt.toLocaleString();
}

async function newProject(onAfterCreate?: () => void) {
  try {
    const path = await invoke<string | null>("open_directory_dialog");
    if (!path) {
      // User cancelled the picker — don't create a phantom session.
      return;
    }
    const sid = "code-" + Math.random().toString(36).slice(2, 10);
    codePanelWS.send({
      type: "code_mode_enter",
      payload: { project_path: path, suggested_name: "untitled", session_id: sid },
    });
    useSessionsStore.getState().ensure(sid, {
      project_root: path,
      project_name: path.split(/[\\/]/).pop() || "untitled",
    });
    useSessionsStore.getState().set_active(sid);
    // Auto-switch to chat view so user lands directly on the new
    // session's input bar, no extra click needed.
    onAfterCreate?.();
  } catch (e) {
    console.warn("[grid] new project failed:", e);
  }
}

async function refresh() {
  codePanelWS.send({ type: "code_sessions_list" });
}

const primaryBtn: React.CSSProperties = {
  background: "#2563eb",
  color: "#fff",
  border: "none",
  borderRadius: 5,
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: 600,
};
const secondaryBtn: React.CSSProperties = {
  background: "rgba(148, 163, 184, 0.18)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.30)",
  borderRadius: 5,
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
};
const tileExpandBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "rgba(148, 163, 184, 0.85)",
  cursor: "pointer",
  fontSize: 13,
  padding: "2px 4px",
  lineHeight: 1,
  opacity: 0.7,
};

const tileDeleteBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "rgba(252, 165, 165, 0.85)",
  cursor: "pointer",
  fontSize: 13,
  padding: "2px 4px",
  lineHeight: 1,
  opacity: 0.55,
  transition: "opacity 100ms",
};

const dropdownStyle: React.CSSProperties = {
  background: "rgba(30, 35, 48, 0.85)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  borderRadius: 4,
  padding: "1px 4px",
  fontSize: 10.5,
  flex: 1,
  minWidth: 0,
  cursor: "pointer",
};

const emptyHint: React.CSSProperties = {
  fontSize: 13,
  color: "rgba(148, 163, 184, 0.7)",
  textAlign: "center",
  marginTop: 60,
  lineHeight: 1.6,
};
