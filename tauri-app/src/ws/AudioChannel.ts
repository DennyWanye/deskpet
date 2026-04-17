import type { AudioMessage } from "../types/messages";

export type AudioConnectionState = "disconnected" | "connecting" | "connected";

/**
 * Audio WebSocket channel.
 * Send: PCM16 binary frames (microphone recording)
 * Receive: MP3 binary frames (TTS audio) + JSON control messages
 */
export class AudioChannel {
  private ws: WebSocket | null = null;
  private url: string;
  private secret: string;
  private binaryListeners = new Set<(data: ArrayBuffer) => void>();
  private jsonListeners = new Set<(msg: AudioMessage) => void>();
  private stateListeners = new Set<(state: AudioConnectionState) => void>();
  private _state: AudioConnectionState = "disconnected";

  constructor(port: number = 8100, secret: string = "") {
    this.url = `ws://127.0.0.1:${port}/ws/audio`;
    this.secret = secret;
  }

  get state() {
    return this._state;
  }

  private setState(state: AudioConnectionState) {
    this._state = state;
    this.stateListeners.forEach((fn) => fn(state));
  }

  connect(sessionId: string = "default") {
    if (this.ws) return;
    this.setState("connecting");
    const wsUrl = `${this.url}?secret=${encodeURIComponent(this.secret)}&session_id=${sessionId}`;
    this.ws = new WebSocket(wsUrl);
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      this.setState("connected");
    };

    this.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        // P2-2: strip 1-byte type header (0x01=PCM, 0x02=MP3).
        // Listeners receive pure audio data — header is internal.
        const raw = new Uint8Array(event.data);
        if (raw.length < 2) return; // runt frame
        const audioData = event.data.slice(1);
        this.binaryListeners.forEach((fn) => fn(audioData));
      } else {
        try {
          const msg: AudioMessage = JSON.parse(event.data);
          this.jsonListeners.forEach((fn) => fn(msg));
        } catch {
          /* ignore parse errors */
        }
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.setState("disconnected");
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  sendAudio(pcmData: ArrayBuffer) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(pcmData);
    }
  }

  onBinary(fn: (data: ArrayBuffer) => void) {
    this.binaryListeners.add(fn);
    return () => {
      this.binaryListeners.delete(fn);
    };
  }

  onJson(fn: (msg: AudioMessage) => void) {
    this.jsonListeners.add(fn);
    return () => {
      this.jsonListeners.delete(fn);
    };
  }

  onStateChange(fn: (state: AudioConnectionState) => void) {
    this.stateListeners.add(fn);
    return () => {
      this.stateListeners.delete(fn);
    };
  }

  disconnect() {
    this.ws?.close();
    this.ws = null;
    this.setState("disconnected");
  }
}
