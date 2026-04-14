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

// --- Emotion / action events (S1) ---
// Emitted by backend pipeline when LLM output contains
// [emotion:xxx] or [action:xxx] tags. Frontend drives Live2D accordingly.

export interface EmotionChangeMessage {
  type: "emotion_change";
  payload: { value: string };
}

export interface ActionTriggerMessage {
  type: "action_trigger";
  payload: { value: string };
}

export type IncomingMessage =
  | ChatResponse
  | PongMessage
  | ErrorMessage
  | LipSyncMessage
  | EmotionChangeMessage
  | ActionTriggerMessage;

export type AudioMessage = VADEvent | TranscriptMessage | TTSEndMessage | ErrorMessage;
