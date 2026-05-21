/**
 * P4-S23 — Multi-session zustand store.
 *
 * Single source of truth for everything Code-mode UI renders. The
 * companion-pet window AND the code-panel webview both subscribe to
 * the same store via Tauri's broadcast WS — events stamped with
 * `payload.session_id` get fanned out to the matching slice here.
 *
 * Design note: we deliberately keep this minimal. Session state lives
 * in this in-memory store + on backend's SessionDB; we don't try to
 * persist scrollback to localStorage (browser quota) — backend's
 * memory hierarchy already handles long-term retrieval.
 */
import { create } from "zustand";

export type MessageRole =
  | "user"
  | "assistant"
  | "assistant_delta"   // P4-S25 A1: streaming partial assistant content
  | "reasoning_delta"   // P4-S25 A1: streaming thinking-mode chain-of-thought
  | "tool_call"
  | "tool_result"
  | "plan"              // P4-S25 A2: plan card preceding execution
  | "error";

export interface PlanStep {
  title: string;
  detail: string;
}

export interface Message {
  id: string;
  role: MessageRole;
  text?: string;
  // Tool-call specific
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  // Tool-result specific
  tool_ok?: boolean;
  tool_result?: string;
  tool_error?: string;
  // P4-S25 A2: plan-card payload
  plan_rationale?: string;
  plan_steps?: PlanStep[];
  // Bookkeeping
  ts: number;
}

export interface Todo {
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}

export type SessionStatus =
  | "idle"
  | "thinking"
  | "running"
  | "permission"
  | "error";

/** P5-S3-Inbox — single supervisor alert payload, kept in two places:
 *   • `supervisor_alert` (latest, drives PetStateMachine)
 *   • `supervisor_inbox`  (queue of unhandled, drives toolbar badge + MessageStreamPanel) */
export interface SupervisorAlertEntry {
  alert_id: string;
  severity: "green" | "yellow" | "red";
  action: "nudge" | "ask_user";
  diagnosis: string;
  user_message: string;
  suggested_buttons: string[];
  received_at: number;
}

export interface SessionState {
  base_session_id: string;
  code_session_id: string | null;
  project_root: string | null;
  project_name: string;
  messages: Message[];
  todos: Todo[];
  token_usage: { prompt: number; completion: number };
  status: SessionStatus;
  last_activity: number;
  inflight: boolean;
  // P5-S3: supervisor signals — fed by `supervisor_alert` ws event +
  // local heuristics (recent tool-call repeat detection happens in
  // backend; here we only stash what the alert told us). Drives the
  // pet's visual state (PetStateMachine) + tile severity colour.
  current_iteration?: number;
  max_iterations?: number;
  /** Highest signature-window count seen in the most recent watchdog
   * snapshot, used in severity_score. Reset to 0 when no recent alert. */
  tool_signature_repeat?: number;
  /** Last supervisor severity colour (green | yellow | red). */
  supervisor_severity?: "green" | "yellow" | "red";
  /** Latest supervisor alert payload, or null if none active. */
  supervisor_alert?: SupervisorAlertEntry | null;
  /** Inbox of unhandled supervisor alerts (yellow + red). Newest first.
   * Lives in the session so jumping to a session shows its history.
   * Cleared on dismiss / handle. */
  supervisor_inbox?: SupervisorAlertEntry[];
  // P5-S2 Phase 5: 自愈尝试计数。0 = 未在自愈中；>0 表示
  // backend AutoResumeOrchestrator 正在第 N 次尝试。
  // ws.ts 收到 auto_resume_started 时设为 attempt 值；
  // succeeded / exhausted 时归 0。AutoResumeBanner 订阅此字段。
  auto_resume_attempts?: number;
  // multi-provider-management Phase 5: per-session provider binding.
  // `provider_id == null` 表示走全局 chain；非空时表示 pin 到某 provider。
  // `preferred_model` 可选 model 覆盖，独立于 provider 选择。
  // ws.ts 在 code_sessions_list_response / code_session_provider_set /
  // code_session_model_set 时写入这两个字段。
  provider_id?: string | null;
  preferred_model?: string | null;
  // code-session-model-params S2: Cursor 风格的 per-session 模型参数。
  // null/undefined = 走 provider 默认（未显式配置）。后端 echo 回的
  // `model_params` 原样写入这里，ChangeModelModal 据此预填。
  model_params?: CodeModelParams | null;
}

/** code-session-model-params — the structured picker params persisted
 * per code session. Mirrors the backend IPC contract exactly:
 *   { thinking, fast, context, effort }
 * Effort `extra_high`/`max` clamp to `high` server-side (OpenAI only
 * exposes low/medium/high) — the UI still shows all 5 rungs. */
export interface CodeModelParams {
  thinking?: boolean;
  fast?: boolean;
  context?: "300k" | "1m";
  effort?: "low" | "medium" | "high" | "extra_high" | "max";
}

interface SessionsStore {
  active_sid: string;
  sessions: Record<string, SessionState>;
  // Concurrency limiter inflight count (for status rendering)
  inflight_count: number;
  inflight_max: number;

  set_active(sid: string): void;
  ensure(sid: string, init?: Partial<SessionState>): void;
  upsert(sid: string, patch: Partial<SessionState>): void;
  push_message(sid: string, msg: Omit<Message, "id" | "ts"> & { id?: string; ts?: number }): void;
  /** Replace the entire message list for a session — used by F5
   * rehydration when the panel reloads and pulls history from
   * SessionDB via `session_messages_load`. */
  set_messages(sid: string, messages: Message[]): void;
  upsert_todos(sid: string, todos: Todo[]): void;
  remove(sid: string): void;
  set_inflight(delta: number): void;
  // P5-S3: supervisor surface
  apply_supervisor_alert(sid: string, alert: SupervisorAlertEntry): void;
  clear_supervisor_alert(sid: string): void;
  /** Remove a single alert from the inbox (and clear `supervisor_alert`
   * if it matches). Used when user clicks a button or "已知道" in the
   * MessageStreamPanel. */
  dismiss_alert(sid: string, alert_id: string): void;
  /** Clear all alerts of one severity across all sessions. Used by the
   * toolbar's "全部已读" sweep button. */
  dismiss_all_alerts(severity: "yellow" | "red"): void;
}

const newId = (): string =>
  globalThis.crypto?.randomUUID?.() ?? `m-${Math.random().toString(36).slice(2, 10)}`;

const blank_session = (sid: string): SessionState => ({
  base_session_id: sid,
  code_session_id: null,
  project_root: null,
  project_name: "(untitled)",
  messages: [],
  todos: [],
  token_usage: { prompt: 0, completion: 0 },
  status: "idle",
  last_activity: Date.now(),
  inflight: false,
  current_iteration: 0,
  max_iterations: 50,
  tool_signature_repeat: 0,
  supervisor_severity: "green",
  supervisor_alert: null,
  supervisor_inbox: [],
  auto_resume_attempts: 0,
  // multi-provider-management Phase 5: default = no binding ⇒ "Global Chain".
  provider_id: null,
  preferred_model: null,
  // code-session-model-params S2: no params ⇒ provider defaults.
  model_params: null,
});

export const useSessionsStore = create<SessionsStore>((set) => ({
  active_sid: "default",
  sessions: { default: blank_session("default") },
  inflight_count: 0,
  inflight_max: 2,

  set_active(sid) {
    // Auto-create slot if frontend asks to switch to an unknown sid
    // (e.g. dashboard tile before WS event landed).
    set((state) => {
      if (!state.sessions[sid]) {
        return {
          active_sid: sid,
          sessions: { ...state.sessions, [sid]: blank_session(sid) },
        };
      }
      return { active_sid: sid };
    });
  },

  ensure(sid, init) {
    set((state) => {
      if (state.sessions[sid]) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...blank_session(sid), ...init },
        },
      };
    });
  },

  upsert(sid, patch) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, ...patch, last_activity: Date.now() },
        },
      };
    });
  },

  push_message(sid, msg) {
    const id = msg.id ?? newId();
    const ts = msg.ts ?? Date.now();
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: [...cur.messages, { ...msg, id, ts }],
            last_activity: ts,
          },
        },
      };
    });
  },

  set_messages(sid, messages) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, messages, last_activity: Date.now() },
        },
      };
    });
  },

  upsert_todos(sid, todos) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, todos, last_activity: Date.now() },
        },
      };
    });
  },

  remove(sid) {
    set((state) => {
      if (!state.sessions[sid]) return state;
      const next = { ...state.sessions };
      delete next[sid];
      const active =
        state.active_sid === sid
          ? Object.keys(next)[0] ?? "default"
          : state.active_sid;
      return { sessions: next, active_sid: active };
    });
  },

  set_inflight(delta) {
    set((state) => ({
      inflight_count: Math.max(0, state.inflight_count + delta),
    }));
  },

  apply_supervisor_alert(sid, alert) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const prev_inbox = cur.supervisor_inbox ?? [];
      // Dedup by alert_id — if the same alert lands twice (ws reconnect
      // replays, double broadcast), don't grow the badge.
      const filtered = prev_inbox.filter((a) => a.alert_id !== alert.alert_id);
      const next_inbox =
        alert.severity === "yellow" || alert.severity === "red"
          ? [alert, ...filtered].slice(0, 50)
          : filtered;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            supervisor_severity: alert.severity,
            supervisor_alert: alert,
            supervisor_inbox: next_inbox,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  clear_supervisor_alert(sid) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, supervisor_alert: null, supervisor_severity: "green" },
        },
      };
    });
  },

  dismiss_alert(sid, alert_id) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return state;
      const next_inbox = (cur.supervisor_inbox ?? []).filter(
        (a) => a.alert_id !== alert_id,
      );
      const next_alert =
        cur.supervisor_alert && cur.supervisor_alert.alert_id === alert_id
          ? null
          : cur.supervisor_alert ?? null;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            supervisor_inbox: next_inbox,
            supervisor_alert: next_alert,
            // Severity downgrades to green only when nothing pending.
            supervisor_severity: next_inbox.length === 0
              ? "green"
              : cur.supervisor_severity,
          },
        },
      };
    });
  },

  dismiss_all_alerts(severity) {
    set((state) => {
      const next: Record<string, SessionState> = {};
      for (const [sid, s] of Object.entries(state.sessions)) {
        const remaining = (s.supervisor_inbox ?? []).filter(
          (a) => a.severity !== severity,
        );
        const cur_alert_dismissed =
          s.supervisor_alert && s.supervisor_alert.severity === severity;
        next[sid] = {
          ...s,
          supervisor_inbox: remaining,
          supervisor_alert: cur_alert_dismissed ? null : s.supervisor_alert ?? null,
          supervisor_severity:
            remaining.length === 0 ? "green" : s.supervisor_severity,
        };
      }
      return { sessions: next };
    });
  },
}));

// ----------------------------------------------------------------------
// Inbox selectors. Pure helpers — components call these from useMemo.
// ----------------------------------------------------------------------

export function count_unhandled_by_severity(
  sessions: Record<string, SessionState>,
  severity: "yellow" | "red",
): number {
  let n = 0;
  for (const s of Object.values(sessions)) {
    for (const a of s.supervisor_inbox ?? []) {
      if (a.severity === severity) n += 1;
    }
  }
  return n;
}

export interface InboxItem extends SupervisorAlertEntry {
  session_id: string;
  project_name: string;
}

export function collect_inbox(
  sessions: Record<string, SessionState>,
  severity: "yellow" | "red",
): InboxItem[] {
  const out: InboxItem[] = [];
  for (const s of Object.values(sessions)) {
    for (const a of s.supervisor_inbox ?? []) {
      if (a.severity === severity) {
        out.push({
          ...a,
          session_id: s.base_session_id,
          project_name: s.project_name || s.base_session_id,
        });
      }
    }
  }
  // Newest first
  out.sort((a, b) => b.received_at - a.received_at);
  return out;
}

// ----------------------------------------------------------------------
// P5-S3 — severity score + pet focus selectors.
//
// Pure functions over SessionState so they're trivial to unit-test and
// to memoize at call sites. Backend's watchdog also computes a similar
// score for telemetry but the pet UI's "which session looks worst" is
// frontend-derived (no need to round-trip ws for every event).
// ----------------------------------------------------------------------

/** Decompose severity for debug overlay display. */
export interface SeverityBreakdown {
  base: number;
  age: number;
  repeat: number;
  supervisor: number;
  iteration: number;
  total: number;
}

const STATUS_BASE: Record<SessionStatus, number> = {
  idle: 0,
  thinking: 5,
  running: 10,
  permission: 25,
  error: 60,
};

const SUPERVISOR_BOOST: Record<"green" | "yellow" | "red", number> = {
  green: 0,
  yellow: 20,
  red: 50,
};

/** Compute severity score breakdown for one session.
 * See spec D7 for the formula.
 * `now` is injectable for unit tests; defaults to Date.now(). */
export function severity_score_breakdown(
  s: SessionState,
  now: number = Date.now(),
): SeverityBreakdown {
  const base = STATUS_BASE[s.status] ?? 0;
  const age_seconds = Math.max(0, (now - (s.last_activity || now)) / 1000);
  // log2 of minutes-since-activity, clamped at 30 points (roughly 32 min
  // saturates the dial).
  const age = Math.min(
    30,
    Math.log2(Math.max(1, age_seconds / 60)) * 6,
  );
  const repeat = Math.min(40, Math.max(0, s.tool_signature_repeat ?? 0) * 10);
  const supervisor = SUPERVISOR_BOOST[s.supervisor_severity ?? "green"] ?? 0;
  const cur = s.current_iteration ?? 0;
  const max = Math.max(1, s.max_iterations ?? 50);
  const iteration = (cur / max) * 10;
  const total = base + age + repeat + supervisor + iteration;
  return { base, age, repeat, supervisor, iteration, total };
}

/** Pure helper used by selectors + tests. */
export function severity_score(s: SessionState, now: number = Date.now()): number {
  return severity_score_breakdown(s, now).total;
}

/** Return the sid of the most-dangerous session (highest score), or null
 * if the store has no sessions to evaluate. Code-mode-only sessions are
 * eligible (companion sessions don't carry code_session_id) — except
 * a session with an active supervisor_alert is ALWAYS eligible, since
 * the supervisor's own decision says "this matters".
 *
 * The companion "default" sid is still excluded so the pet doesn't
 * focus itself when an alert hypothetically targets the chitchat
 * channel — supervisor only watches Code mode by design. */
export function pet_focus_sid(
  sessions: Record<string, SessionState>,
  now: number = Date.now(),
): string | null {
  let best_sid: string | null = null;
  let best_score = -1;
  for (const [sid, s] of Object.entries(sessions)) {
    // Companion sid is never eligible. Otherwise: Code-mode metadata
    // OR an active supervisor_alert qualifies the session for focus.
    if (sid === "default") continue;
    const has_code_meta = !!(s.code_session_id || s.project_root);
    const has_active_alert = !!s.supervisor_alert;
    if (!has_code_meta && !has_active_alert) continue;
    const score = severity_score(s, now);
    if (score > best_score) {
      best_score = score;
      best_sid = sid;
    }
  }
  return best_sid;
}

// ----------------------------------------------------------------------
// Concurrency limiter — wrap outbound chat sends so chinzy doesn't
// see N parallel chat_v2 messages from N tiles all at once. Default
// max is 2 simultaneous in-flight LLM round-trips; the rest queue.
// ----------------------------------------------------------------------

export class ConcurrencyLimiter {
  private inflight = 0;
  private queue: (() => void)[] = [];
  private max: number;

  constructor(max: number) {
    this.max = max;
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.inflight >= this.max) {
      await new Promise<void>((resolve) => this.queue.push(resolve));
    }
    this.inflight++;
    useSessionsStore.getState().set_inflight(+1);
    try {
      return await fn();
    } finally {
      this.inflight--;
      useSessionsStore.getState().set_inflight(-1);
      const next = this.queue.shift();
      if (next) next();
    }
  }

  get_max() {
    return this.max;
  }
}

export const chatLimiter = new ConcurrencyLimiter(2);
