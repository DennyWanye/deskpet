import type { ControlMessage, IncomingMessage } from "../types/messages";

export type ConnectionState = "disconnected" | "connecting" | "connected";

export class ControlChannel {
  private ws: WebSocket | null = null;
  private url: string;
  private secret: string;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners = new Set<(msg: IncomingMessage) => void>();
  private stateListeners = new Set<(state: ConnectionState) => void>();
  private _state: ConnectionState = "disconnected";

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
    this.setState("connecting");
    const wsUrl = `${this.url}?secret=${encodeURIComponent(this.secret)}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.setState("connected");
    };

    this.ws.onmessage = (event) => {
      try {
        const msg: IncomingMessage = JSON.parse(event.data);
        this.listeners.forEach((fn) => fn(msg));
      } catch {
        console.error("Failed to parse:", event.data);
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.setState("disconnected");
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  disconnect() {
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

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }
}
