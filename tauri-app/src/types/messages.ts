export interface ControlMessage {
  type: string;
  payload?: Record<string, unknown>;
}

export interface ChatResponse {
  type: "chat_response";
  payload: { text: string };
}

export interface PongMessage {
  type: "pong";
}

export interface ErrorMessage {
  type: "error";
  payload: { message: string };
}

// --- Audio channel message types ---

export interface VADEvent {
  type: "vad_event";
  payload: { status: "speech_start" | "speech_end" };
}

export interface TranscriptMessage {
  type: "transcript";
  payload: { text: string; role: "user" | "assistant" };
}

export interface LipSyncMessage {
  type: "lip_sync";
  payload: { chunk_index: number; amplitude: number };
}

export interface TTSEndMessage {
  type: "tts_end";
  payload: Record<string, never>;
}

export type IncomingMessage = ChatResponse | PongMessage | ErrorMessage;

export type AudioMessage = VADEvent | TranscriptMessage | TTSEndMessage | ErrorMessage;
