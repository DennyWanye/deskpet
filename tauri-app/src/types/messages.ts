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

// --- S14 memory management (control channel) ---

export interface StoredTurn {
  id: number;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
}

export interface SessionSummary {
  session_id: string;
  turn_count: number;
  last_message_at: number;
}

export interface MemoryListResponse {
  type: "memory_list_response";
  payload: {
    scope: "session" | "all";
    session_id: string | null;
    turns: StoredTurn[];
  };
}

export interface MemoryDeleteAck {
  type: "memory_delete_ack";
  payload: { id: number; deleted: boolean };
}

export interface MemoryClearAck {
  type: "memory_clear_ack";
  payload: { scope: "session" | "all"; session_id?: string; removed?: number };
}

export interface MemoryExportResponse {
  type: "memory_export_response";
  payload: {
    exported_at: number;
    sessions: SessionSummary[];
    turns: StoredTurn[];
  };
}

export type IncomingMessage =
  | ChatResponse
  | PongMessage
  | ErrorMessage
  | LipSyncMessage
  | EmotionChangeMessage
  | ActionTriggerMessage
  | MemoryListResponse
  | MemoryDeleteAck
  | MemoryClearAck
  | MemoryExportResponse;

export type AudioMessage = VADEvent | TranscriptMessage | TTSEndMessage | ErrorMessage;
