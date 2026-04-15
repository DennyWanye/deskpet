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

// --- P2-1-S3 settings / provider test ----------------------------------------

/** Outgoing: SettingsPanel「测试连接」button. Candidate creds travel on the
 * already-authenticated control channel; nothing is persisted backend-side. */
export interface ProviderTestConnectionRequest {
  type: "provider_test_connection";
  payload: { base_url: string; api_key: string; model: string };
}

/** Incoming: backend reply to the request above. */
export interface ProviderTestConnectionResult {
  type: "provider_test_connection_result";
  payload: {
    ok: boolean;
    tested_url?: string;
    /** Present when ok=false; short human-readable reason. */
    error?: string;
  };
}

/**
 * P2-1-S3 <-> P2-1-S8 cross-slice contract: the shape SettingsPanel's
 * 今日使用 section consumes. S3 ships a stub `fetchDailyBudget`; S8 replaces
 * it with the real control-WS roundtrip.
 *
 * Fields are snake_case to match the eventual backend payload verbatim —
 * no translation layer needed when S8 lands.
 */
export interface DailyBudgetStatus {
  spent_today_cny: number;
  daily_budget_cny: number;
  remaining_cny: number;
  /** 0..100. Precomputed by the backend so the UI doesn't have to guard
   * against division-by-zero or stale limit values. */
  percent_used: number;
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
  | MemoryExportResponse
  | ProviderTestConnectionResult;

export type AudioMessage = VADEvent | TranscriptMessage | TTSEndMessage | ErrorMessage;
