/**
 * P4-S23 — WebSocket dispatcher for the code panel window.
 *
 * Both windows talk to the same backend on `ws://127.0.0.1:8100`.
 * The pet window already owns its WS via `useControlChannel`; the
 * code panel opens its OWN connection so backend can broadcast the
 * same event to both windows independently (matters for multi-session
 * — a tile in the panel grid updates without round-tripping through
 * the pet's React state).
 *
 * The shared secret is fetched via Tauri's `get_shared_secret` IPC
 * (already implemented in process_manager.rs).
 */
import { invoke } from "@tauri-apps/api/core";
import { useSessionsStore } from "../stores/sessionsStore";
// P5-S2 Phase 5 — code session binding events
import { useProvidersStore } from "./providersStore";
import { useCodeModelsStore } from "./codeModelsStore";
import { pickProviderRemovedFallback } from "./SessionGridView";
// P5-S2 Phase 4 — settings panel provider mutation events
import { dispatchProviderEvent } from "../components/SettingsProviders";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

// P4-S23 fix: stash the live socket on globalThis so vite HMR (which
// may swap this module while the old WebSocket is still mid-handshake)
// can find and close the previous instance instead of stacking
// sockets and getting kicked by the backend's "session replaced"
// guard. Without this we saw a connect/disconnect storm in the
// backend log every dev session.
type GlobalWS = {
  __deskpet_panel_ws__?: WebSocket | null;
  __deskpet_panel_listeners__?: Set<(msg: any) => void>;
};
const G = globalThis as unknown as GlobalWS;

let ws: WebSocket | null = G.__deskpet_panel_ws__ ?? null;
let reconnect_timer: number | null = null;
let reconnect_attempt = 0;

type Listener = (msg: any) => void;
const listeners: Set<Listener> = G.__deskpet_panel_listeners__ ?? new Set<Listener>();
G.__deskpet_panel_listeners__ = listeners;

export interface CodePanelWS {
  send(msg: { type: string; payload?: Record<string, unknown> }): void;
  on_message(fn: Listener): () => void;
  state(): "disconnected" | "connecting" | "connected";
}

let current_state: CodePanelWS["state"] extends () => infer R ? R : never = "disconnected";

async function open_socket() {
  // Idempotent guard with global lookup. Any pre-existing socket on
  // globalThis (from a previous module instance / HMR cycle) wins —
  // we just reuse it. Without this we saw a connect/disconnect
  // storm in the backend log every dev iteration because each
  // module reload was calling open_socket() while the old socket
  // was still mid-handshake.
  const existing = G.__deskpet_panel_ws__;
  if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
    ws = existing;
    return;
  }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  current_state = "connecting";
  let secret = "";
  try {
    secret = await invoke<string>("get_shared_secret");
  } catch (e) {
    console.warn("[code-panel] get_shared_secret failed:", e);
    schedule_reconnect();
    return;
  }
  if (!secret) {
    schedule_reconnect();
    return;
  }
  // Use a distinct session_id from the pet's main WS ("default"). The
  // backend stores _control_connections keyed by session_id, and a
  // second connection on the same key kicks the first one out — that
  // produced the 1-second reconnect loop visible in early P4-S23 dev
  // runs. "code-panel-main" is harmless: every chat_v2 message we
  // send carries `payload.session_id` explicitly anyway.
  const url = `ws://127.0.0.1:8100/ws/control?secret=${encodeURIComponent(
    secret,
  )}&session_id=code-panel-main`;
  try {
    ws = new WebSocket(url);
    G.__deskpet_panel_ws__ = ws;
  } catch (e) {
    console.warn("[code-panel] ws ctor failed:", e);
    schedule_reconnect();
    return;
  }
  ws.onopen = () => {
    reconnect_attempt = 0;
    current_state = "connected";
    // Pull current sessions list on (re)connect so the dashboard hydrates.
    ws?.send(JSON.stringify({ type: "code_sessions_list" }));
    // code-session-model-params: pull the live model catalog so the
    // picker's dropdown is data-driven (chinzy /models), not hardcoded.
    ws?.send(JSON.stringify({ type: "code_models_list" }));
    // Also pull current todos for the active session in case they
    // changed since last connect.
    const store = useSessionsStore.getState();
    const sid = store.active_sid;
    ws?.send(JSON.stringify({ type: "code_todo_list", payload: { session_id: sid } }));
    // P4-S23 F5 fix: rehydrate chat history for every known session
    // from SessionDB. Without this, refreshing the panel wipes the
    // user's scrollback even though the messages are persisted.
    // We pull the panel's "default" session AND any code-* sessions
    // already in the store. Backend dedupes by session_id.
    const known_sids = new Set<string>([sid, "default"]);
    for (const k of Object.keys(store.sessions)) known_sids.add(k);
    for (const target of known_sids) {
      ws?.send(JSON.stringify({
        type: "session_messages_load",
        payload: { session_id: target, limit: 200 },
      }));
    }
  };
  ws.onmessage = (ev) => {
    let parsed: any;
    try {
      parsed = JSON.parse(ev.data);
    } catch {
      return;
    }
    dispatch(parsed);
    listeners.forEach((fn) => {
      try { fn(parsed); } catch (e) { console.warn(e); }
    });
  };
  ws.onclose = () => {
    current_state = "disconnected";
    schedule_reconnect();
  };
  ws.onerror = () => {
    // onclose fires after; reconnect handled there.
  };
}

function schedule_reconnect() {
  if (reconnect_timer != null) return;
  const delay = Math.min(
    RECONNECT_MAX_MS,
    RECONNECT_BASE_MS * Math.pow(2, reconnect_attempt),
  );
  reconnect_attempt++;
  // P5-S2 Phase 5: globalThis.setTimeout works in both browser + node (vitest);
  // window.setTimeout would crash in the node test env when ws.ts is imported.
  reconnect_timer = (globalThis as any).setTimeout(() => {
    reconnect_timer = null;
    void open_socket();
  }, delay) as unknown as number;
}

/**
 * Route a backend WS message into the zustand store. Most events
 * carry `payload.session_id`; default to "default" for legacy events
 * that don't.
 */
function dispatch(msg: any) {
  const store = useSessionsStore.getState();
  const sid: string =
    (msg?.payload && (msg.payload.session_id || msg.payload.code_session_id)) ||
    store.active_sid;

  switch (msg.type) {
    case "chat_response": {
      // Mid-loop assistant text (with tool calls). Render as assistant bubble.
      const text = msg.payload?.text;
      if (text) store.push_message(sid, { role: "assistant", text });
      break;
    }
    case "chat_v2_delta": {
      // P4-S25 A1: token chunk during streaming. Append to the in-progress
      // assistant bubble. We track the last "assistant" message in the
      // session and append to it; if the previous message isn't an
      // assistant (e.g. first delta of the turn), create a new one.
      const p = msg.payload || {};
      const kind = p.kind || "content";
      const chunk: string = p.content || "";
      if (!chunk) break;
      // Reasoning chunks are visually muted in the UI; stash them on a
      // separate role for now. (Could fold into one bubble in B2.)
      const role = kind === "reasoning" ? "reasoning_delta" : "assistant_delta";
      const cur = store.sessions[sid];
      const last = cur?.messages[cur.messages.length - 1];
      if (last && last.role === (role as any)) {
        // Append to existing partial bubble in-place.
        last.text = (last.text || "") + chunk;
        // Trigger zustand re-render via a no-op upsert.
        store.upsert(sid, { last_activity: Date.now() });
      } else {
        store.push_message(sid, { role: role as any, text: chunk });
      }
      break;
    }
    case "chat_v2_final": {
      const text = msg.payload?.text;
      // P4-S25 A1: final text replaces any partial deltas. Drop trailing
      // assistant_delta + reasoning_delta bubbles (they were the streaming
      // preview); the canonical assistant bubble lands here.
      const cur = store.sessions[sid];
      if (cur) {
        const cleaned = cur.messages.filter(
          (m) => m.role !== ("assistant_delta" as any) &&
                 m.role !== ("reasoning_delta" as any),
        );
        store.set_messages(sid, cleaned);
      }
      if (text) store.push_message(sid, { role: "assistant", text });
      store.upsert(sid, { status: "idle", inflight: false });
      break;
    }
    case "chat_v2_error": {
      const p = msg.payload || {};
      const parts = [p.error, p.detail, p.reason].filter(Boolean);
      const txt = parts.length ? parts.join(" — ") : "unknown";
      store.push_message(sid, { role: "error", text: txt });
      store.upsert(sid, { status: "error", inflight: false });
      break;
    }
    case "chat_v2_plan": {
      // P4-S25 A2: Plan card from the planner phase. Render it as a
      // dedicated bubble in the chat stream so the user can see the
      // intended steps before / during execution.
      const p = msg.payload || {};
      store.push_message(sid, {
        role: "plan" as any,
        plan_rationale: p.rationale,
        plan_steps: p.steps,
      } as any);
      break;
    }
    case "chat_v2_interrupted": {
      // P4-S25 B3: backend cancelled in-flight task. Clear status so
      // the button reverts to "发送" and user can type again.
      store.upsert(sid, { status: "idle", inflight: false });
      break;
    }
    case "tool_call": {
      const p = msg.payload || {};
      store.push_message(sid, {
        role: "tool_call",
        tool_name: p.name,
        tool_args: p.arguments,
      });
      store.upsert(sid, { status: "running" });
      break;
    }
    case "tool_result": {
      const p = msg.payload || {};
      store.push_message(sid, {
        role: "tool_result",
        tool_name: p.tool,
        tool_ok: p.ok,
        tool_result: p.result,
      });
      break;
    }
    case "code_todo_update": {
      // todo_write_tool's broadcaster sends payload.session_id =
      // code_session_id (the per-project sid). The store keys by
      // base_session_id. Reverse-map: find any session whose
      // code_session_id matches.
      //
      // P6 bugfix 2026-05-13: pre-fix, a missing reverse-map fell
      // back to `sid` (active_sid), which silently mis-attached
      // todos from a non-project session (e.g. CLI ws clients,
      // p6_live_test, system probes) onto whichever project tile
      // happened to be focused. The user saw "小说网站" tile showing
      // todos like "list G:/projects/deskpet/backend/agent/" — those
      // came from p6_live_test, not 小说网站. Drop instead of cross-
      // contaminate.
      const items = msg.payload?.items ?? [];
      const code_sid = msg.payload?.session_id;
      let target_base_sid: string | null = null;
      if (code_sid) {
        for (const [base_sid, st] of Object.entries(store.sessions)) {
          if (st.code_session_id === code_sid) {
            target_base_sid = base_sid;
            break;
          }
        }
      }
      if (target_base_sid) {
        store.upsert_todos(target_base_sid, items);
      } else {
        // No matching code session — silently drop. This is the
        // correct behavior for non-project todos (e.g. companion-mode
        // chat or external test scripts). Logging at debug level so
        // genuine wiring bugs (lost code_session_id) are still
        // discoverable.
        console.debug(
          "[code-panel] code_todo_update dropped: no matching code_session_id",
          code_sid,
        );
      }
      break;
    }
    case "code_mode_state": {
      const p = msg.payload || {};
      if (p.enabled) {
        store.ensure(sid, {
          project_root: p.project_root ?? null,
          project_name: p.project_name ?? "(untitled)",
          code_session_id: p.code_session_id ?? null,
        });
        store.upsert(sid, {
          project_root: p.project_root ?? null,
          project_name: p.project_name ?? "(untitled)",
          code_session_id: p.code_session_id ?? null,
        });
        store.set_active(sid);
      } else {
        store.upsert(sid, { status: "idle" });
      }
      break;
    }
    case "code_sessions_list_response": {
      const items: Array<any> = msg.payload?.items ?? [];
      // P4-S24 followup: backend list is the source of truth. After a
      // delete, the server omits the removed entry — sync our store by
      // pruning any session NOT in `items` (kept "default" since the
      // pet shell uses it). Without this prune, stale tiles linger in
      // the dashboard after a delete on another window.
      const keep = new Set<string>(["default"]);
      // P4-S25 B4 fix: track newly-discovered sids so we can pull
      // their chat history + todos. Without this, persisted projects
      // restored at startup show empty bubbles even though messages
      // exist in SessionDB — ws.onopen's session_messages_load loop
      // ran before the list arrived, so it only fetched "default".
      const newly_added: string[] = [];
      for (const it of items) {
        const bsid = it.base_session_id;
        keep.add(bsid);
        const was_present = !!useSessionsStore.getState().sessions[bsid];
        store.ensure(bsid, {
          base_session_id: bsid,
          code_session_id: it.code_session_id ?? null,
          project_root: it.project_root ?? null,
          project_name: it.project_name ?? "(untitled)",
        });
        // P5-S2 Phase 5 — code session binding events
        // List response carries per-session provider binding; write whatever
        // the backend says (including explicit nulls so a cleared binding
        // round-trips correctly).
        const next_provider_id =
          it.provider_id === undefined ? null : it.provider_id;
        const next_preferred_model =
          it.preferred_model === undefined ? null : it.preferred_model;
        store.upsert(bsid, {
          project_root: it.project_root ?? null,
          project_name: it.project_name ?? "(untitled)",
          provider_id: next_provider_id,
          preferred_model: next_preferred_model,
          // code-session-model-params S2: only when the backend item
          // actually carries it — the list response omits model_params,
          // so a refresh must not clobber an optimistic picker write.
          ...(it.model_params !== undefined
            ? { model_params: it.model_params }
            : {}),
        });
        if (!was_present) newly_added.push(bsid);
      }
      // Fire history + todos load for every newly-discovered session.
      for (const bsid of newly_added) {
        codePanelWS.send({
          type: "session_messages_load",
          payload: { session_id: bsid, limit: 200 },
        });
        codePanelWS.send({
          type: "code_todo_list",
          payload: { session_id: bsid },
        });
      }
      const current = useSessionsStore.getState().sessions;
      for (const sid_existing of Object.keys(current)) {
        if (!keep.has(sid_existing)) {
          store.remove(sid_existing);
        }
      }
      break;
    }
    case "code_session_deleted": {
      // P4-S24 followup: backend confirmed delete. Drop the session
      // from the local store. The accompanying `code_sessions_list_response`
      // (backend re-broadcasts after delete) will also clean up, but
      // doing it here makes the UI feel immediate.
      const target = msg.payload?.base_session_id;
      if (target) {
        store.remove(target);
      }
      break;
    }
    case "session_messages_response": {
      // F5 rehydration response. Backend returned the message list
      // for a given session_id; replace whatever's in the store so
      // we don't end up with duplicates after reconnect.
      //
      // P6 bugfix 2026-05-14 (history persistence): backend rows may now
      // include role='assistant' with tool_calls JSON (means agent called
      // a tool — UI renders as tool_call bubble) or role='tool' with
      // tool_call_id (the tool's reply — render as tool_result bubble).
      // Pre-fix the frontend只 expected user/assistant, so even after the
      // backend persists everything UI would still drop them.
      const target = msg.payload?.session_id || sid;
      const items: any[] = msg.payload?.messages ?? [];
      const restored: any[] = [];
      // P6 bugfix 2026-05-14: build tool_call_id → tool_name map first pass
      // so the subsequent tool reply row can show the right tool name
      // (instead of "(unknown)"). Backend SessionDB schema doesn't store
      // tool_name on the tool row; only the prior assistant row knows it
      // via tool_calls[].function.name.
      const tcid_to_name = new Map<string, string>();
      for (const m of items) {
        const tcs = Array.isArray(m.tool_calls) ? m.tool_calls : null;
        if (!tcs) continue;
        for (const tc of tcs) {
          const id = tc?.id;
          const name = tc?.function?.name || tc?.name;
          if (id && name) tcid_to_name.set(id, name);
        }
      }
      for (const m of items) {
        const base_id = m.id || `r-${Math.random().toString(36).slice(2, 10)}`;
        const ts = m.ts || Date.now();
        const role = m.role || "assistant";
        const tool_calls = Array.isArray(m.tool_calls) ? m.tool_calls : null;

        if (role === "tool") {
          // Tool reply row → tool_result bubble. Reverse-map tool name
          // via the previously-built tcid → name dictionary.
          const tcid: string | undefined = m.tool_call_id;
          restored.push({
            id: base_id,
            role: "tool_result",
            tool_name: (tcid && tcid_to_name.get(tcid)) || undefined,
            tool_ok: true,
            tool_result: m.text || "",
            ts,
          });
          continue;
        }
        if (role === "assistant" && tool_calls && tool_calls.length > 0) {
          // assistant turn that issued one or more tool_calls. Expand each
          // tc into a tool_call bubble; if the row ALSO has text content
          // (rare — content + tool_calls in same turn), prepend that too.
          if (m.text && m.text.length > 0) {
            restored.push({
              id: `${base_id}-pre`,
              role: "assistant",
              text: m.text,
              ts,
            });
          }
          tool_calls.forEach((tc: any, idx: number) => {
            let args: any = tc?.function?.arguments;
            if (typeof args === "string") {
              try { args = JSON.parse(args); } catch { /* keep raw */ }
            }
            restored.push({
              id: `${base_id}-tc${idx}`,
              role: "tool_call",
              tool_name: tc?.function?.name || tc?.name || "unknown",
              tool_args: typeof args === "object" && args !== null ? args : undefined,
              ts,
            });
          });
          continue;
        }
        // Plain user / assistant (text) / etc.
        restored.push({
          id: base_id,
          role,
          text: m.text,
          ts,
        });
      }
      store.set_messages(target, restored);
      break;
    }
    case "permission_request": {
      // Code panel doesn't host the popup (the pet window does), but
      // we mark the session so the tile shows a "🔒 waiting" pill.
      store.upsert(sid, { status: "permission" });
      break;
    }
    case "auto_resume_started": {
      // P5-S2 Phase 5: backend's AutoResumeOrchestrator started a self-healing
      // attempt for this session. Bump auto_resume_attempts so AutoResumeBanner
      // shows "🔄 agent 自愈中... (尝试 N/2)".
      const p = msg.payload || {};
      const target = p.session_id || sid;
      const attempt = typeof p.attempt === "number" ? p.attempt : 1;
      store.ensure(target);
      store.upsert(target, {
        auto_resume_attempts: attempt,
        inflight: true,
        status: "running",
      });
      break;
    }
    case "auto_resume_succeeded": {
      // P5-S2 Phase 5: orchestrator landed a final response. Reset counter so
      // banner dismisses; the regular chat_v2_final (which fires alongside)
      // will handle status/inflight cleanup.
      const p = msg.payload || {};
      const target = p.session_id || sid;
      store.upsert(target, { auto_resume_attempts: 0 });
      break;
    }
    case "auto_resume_exhausted": {
      // P5-S2 Phase 5: orchestrator gave up after max_attempts. Reset counter,
      // surface a red error message so the user sees what failed (mirrors
      // chat_v2_error semantics — supervisor_alert popup may also fire from a
      // separate ws message; here we just guarantee an error bubble lands).
      const p = msg.payload || {};
      const target = p.session_id || sid;
      const final_error = String(p.final_error ?? "auto-resume exhausted");
      const attempts_n = typeof p.attempts === "number" ? p.attempts : 0;
      store.upsert(target, {
        auto_resume_attempts: 0,
        status: "error",
        inflight: false,
      });
      store.push_message(target, {
        role: "error",
        text: `自愈失败（${attempts_n} 次尝试）: ${final_error}`,
      });
      break;
    }
    case "supervisor_alert": {
      // P5-S3: supervisor flagged this session. The pet window owns
      // the bubble UI; the panel uses this to colour the tile border.
      const p = msg.payload || {};
      const target_sid = p.session_id || sid;
      store.ensure(target_sid);
      store.apply_supervisor_alert(target_sid, {
        alert_id: String(p.alert_id || ""),
        severity: (p.severity as "green" | "yellow" | "red") || "yellow",
        action: (p.action as "nudge" | "ask_user") || "nudge",
        diagnosis: String(p.diagnosis || ""),
        user_message: String(p.user_message || ""),
        suggested_buttons: Array.isArray(p.suggested_buttons)
          ? p.suggested_buttons.map((b: any) => String(b)).slice(0, 2)
          : [],
        received_at: Date.now(),
      });
      break;
    }
    case "supervisor_toggle_ack": {
      // Settings panel may listen separately; nothing to do here.
      break;
    }
    // P5-S2 Phase 5 — code session binding events
    case "providers_changed": {
      // Settings-side mutation broadcast. Refresh providersStore + reconcile
      // any per-session bindings whose pinned provider no longer exists in
      // the new list (those cards silently fall back to "Global Chain" so a
      // deleted provider doesn't leave dangling state).
      const incoming = Array.isArray(msg.payload?.providers)
        ? msg.payload.providers
        : [];
      useProvidersStore.getState().set_providers(incoming);
      // Mirror into Phase 4 settings store so SettingsProviders re-renders.
      dispatchProviderEvent(msg);
      const valid_ids = new Set<string>(
        incoming.map((p: any) => String(p?.id)).filter(Boolean),
      );
      const cur_sessions = useSessionsStore.getState().sessions;
      for (const [bsid, s] of Object.entries(cur_sessions)) {
        const pid = s.provider_id;
        if (pid && !valid_ids.has(pid)) {
          const fallback = pickProviderRemovedFallback(pid);
          store.upsert(bsid, { provider_id: fallback.provider_id });
          // Toast surfacing is the panel's job — emit a console hint so the
          // UI listener (or a future toast bus) can pick it up. We avoid
          // pulling a toast library into ws.ts; the message stream already
          // surfaces explicit errors when relevant.
          console.info("[code-panel]", fallback.toast);
        }
      }
      break;
    }
    case "settings_providers_list_response": {
      // Initial provider list (frontend asks on panel mount). Same shape as
      // the broadcast — populate the store without binding reconciliation
      // (no UI state to reconcile yet on first load).
      const incoming = Array.isArray(msg.payload?.providers)
        ? msg.payload.providers
        : [];
      useProvidersStore.getState().set_providers(incoming);
      // Mirror into Phase 4 settings store.
      dispatchProviderEvent(msg);
      break;
    }
    // P5-S2 Phase 4 — individual provider mutation events route to
    // SettingsProviders' internal store only (no per-session reconciliation
    // needed — the upstream `providers_changed` broadcast handles that).
    case "settings_providers_reordered":
    case "settings_providers_added":
    case "settings_providers_updated":
    case "settings_providers_removed":
    case "settings_providers_error": {
      dispatchProviderEvent(msg);
      break;
    }
    case "code_session_provider_set": {
      // Ack from backend for a code_session_set_provider request. Mirror the
      // authoritative provider_id + preferred_model back into the store so
      // the UI reflects what backend actually persisted (in case of clamp /
      // sanitisation differences from the optimistic UI write).
      const p = msg.payload || {};
      const target = p.session_id || sid;
      store.ensure(target);
      store.upsert(target, {
        provider_id: p.provider_id === undefined ? null : p.provider_id,
        preferred_model:
          p.preferred_model === undefined ? null : p.preferred_model,
        // Backend's set_provider path preserves + echoes model_params.
        ...(p.model_params !== undefined
          ? { model_params: p.model_params }
          : {}),
      });
      break;
    }
    case "code_models_list_response": {
      // code-session-model-params: live model catalog + per-model caps.
      const p = msg.payload || {};
      useCodeModelsStore
        .getState()
        .set_catalog(
          Array.isArray(p.models) ? p.models : [],
          typeof p.source === "string" ? p.source : "none",
        );
      break;
    }
    case "code_session_model_set": {
      // Ack from backend for code_session_set_model. Same merge as above —
      // backend echoes provider_id + preferred_model + model_params so we
      // keep all three in sync even if the user only changed the model.
      const p = msg.payload || {};
      const target = p.session_id || sid;
      store.ensure(target);
      store.upsert(target, {
        provider_id: p.provider_id === undefined ? null : p.provider_id,
        preferred_model:
          p.preferred_model === undefined ? null : p.preferred_model,
        // code-session-model-params S2: dict ⇒ active params; null ⇒
        // binding cleared. undefined (legacy backend) ⇒ leave untouched.
        ...(p.model_params !== undefined
          ? { model_params: p.model_params }
          : {}),
      });
      break;
    }
    default:
      // Unknown event types are fine — the pet shell may handle them.
      break;
  }
}

export const codePanelWS: CodePanelWS = {
  send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    } else {
      console.warn("[code-panel] send on closed socket; queueing reconnect");
      schedule_reconnect();
    }
  },
  on_message(fn) {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
  state() {
    return current_state;
  },
};

// P5-S2 Phase 5: vitest hook. Production never imports this; tests use it to
// drive `dispatch` without spinning up a real WebSocket. Exported under a
// `__test_` prefix to make the intent obvious at call sites.
export const __test_dispatch = dispatch;

// Auto-connect on import. Caller doesn't need to do anything.
void open_socket();

// Vite HMR: when this module is hot-replaced, close the existing
// socket cleanly so the backend frees the slot before the new module
// tries to open another. Without this we leak a socket per HMR cycle
// and the backend keeps closing them with code 4002 "session
// replaced", which onclose interprets as needing a reconnect — hence
// the reconnect storm.
if ((import.meta as any).hot) {
  (import.meta as any).hot.dispose(() => {
    if (reconnect_timer != null) {
      clearTimeout(reconnect_timer);
      reconnect_timer = null;
    }
    try {
      ws?.close(1000, "hmr dispose");
    } catch {
      /* noop */
    }
    ws = null;
    listeners.clear();
  });
}
