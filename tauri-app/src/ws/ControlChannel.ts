// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ControlMessage, IncomingMessage } from "../types/messages";

export type ConnectionState = "disconnected" | "connecting" | "connected";

/**
 * Pet Animation UX v2 — feature-negotiation contract (PRD §6.3).
 * Client announces what v2 features it supports; backend responds with what
 * it has wired. Old backends (pre-v2) silently ignore client_hello and never
 * emit server_hello → features stay empty → client falls back to all-fallback
 * paths (frontend phoneme estimator for viseme, keyword voting for emotion,
 * etc.). PRD §6.3 + GOAL.md.
 */
export const V2_CLIENT_VERSION = "v2.0";
export const V2_CLIENT_SUPPORTS = [
  "viseme",
  "emotion",
  "milestone",
  "dnd",
  "occlusion",
] as const;
export type V2Feature = (typeof V2_CLIENT_SUPPORTS)[number];

export class ControlChannel {
  private ws: WebSocket | null = null;
  private url: string;
  private secret: string;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  // disconnect() 是用户主动断开的信号。置位后 onclose 不再
  // 调度重连 —— 否则 useEffect cleanup 断开的旧 channel
  // （例如 secret 从空更新到真值时）会在 3s 后自己复活，
  // 形成永远用旧 secret 的僵尸重连风暴。
  private closing = false;
  private listeners = new Set<(msg: IncomingMessage) => void>();
  private stateListeners = new Set<(state: ConnectionState) => void>();
  private _state: ConnectionState = "disconnected";
  /** Features the backend advertised via server_hello. Empty until handshake completes. */
  private _serverFeatures = new Set<V2Feature>();
  private _serverVersion: string | null = null;

  constructor(port: number = 8100, secret: string = "") {
    this.url = `ws://127.0.0.1:${port}/ws/control`;
    this.secret = secret;
  }

  get state() {
    return this._state;
  }

  private setState(state: ConnectionState) {
    this._state = state;
    this.stateListeners.forEach((fn) => fn(state));
  }

  connect() {
    if (this.ws) return;
    this.closing = false;
    this.setState("connecting");
    const wsUrl = `${this.url}?secret=${encodeURIComponent(this.secret)}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.setState("connected");
      // PRD §6.3: send client_hello on connect so the backend can advertise
      // its v2 feature set via server_hello. Old backends silently ignore
      // unknown message types → we proceed with empty server features.
      this._serverFeatures.clear();
      this._serverVersion = null;
      this.send({
        type: "client_hello",
        payload: {
          version: V2_CLIENT_VERSION,
          supports: [...V2_CLIENT_SUPPORTS],
        },
      });
    };

    this.ws.onmessage = (event) => {
      try {
        const msg: IncomingMessage = JSON.parse(event.data);
        // PRD §6.3: intercept server_hello to populate the v2 feature set.
        // This is layered ON TOP of normal listener fan-out so debug UIs that
        // listen for server_hello (S2 diagnostics) still see the message.
        const anyMsg = msg as unknown as { type?: string; payload?: { features?: unknown; version?: unknown } };
        if (anyMsg.type === "server_hello") {
          const features = Array.isArray(anyMsg.payload?.features) ? anyMsg.payload!.features as unknown[] : [];
          this._serverFeatures.clear();
          for (const f of features) {
            if (typeof f === "string" && (V2_CLIENT_SUPPORTS as readonly string[]).includes(f)) {
              this._serverFeatures.add(f as V2Feature);
            }
          }
          if (typeof anyMsg.payload?.version === "string") {
            this._serverVersion = anyMsg.payload.version;
          }
        }
        this.listeners.forEach((fn) => fn(msg));
      } catch {
        console.error("Failed to parse:", event.data);
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.setState("disconnected");
      if (!this.closing) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  disconnect() {
    this.closing = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.setState("disconnected");
  }

  send(msg: ControlMessage) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  sendChat(text: string) {
    this.send({ type: "chat", payload: { text } });
  }

  /** P4-S20: send via the new tool_use loop path. */
  sendChatV2(text: string) {
    this.send({ type: "chat_v2", payload: { text } });
  }

  sendInterrupt() {
    this.send({ type: "interrupt" });
  }

  onMessage(fn: (msg: IncomingMessage) => void) {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }

  onStateChange(fn: (state: ConnectionState) => void) {
    this.stateListeners.add(fn);
    return () => {
      this.stateListeners.delete(fn);
    };
  }

  /** PRD §6.3: query whether backend has wired a v2 feature. Empty before handshake. */
  hasServerFeature(feature: V2Feature): boolean {
    return this._serverFeatures.has(feature);
  }

  /** PRD §6.3: full feature set + version snapshot (mainly for diagnostics UI). */
  getServerHello(): { version: string | null; features: V2Feature[] } {
    return {
      version: this._serverVersion,
      features: Array.from(this._serverFeatures),
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }
}
