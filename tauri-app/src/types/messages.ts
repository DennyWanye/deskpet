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

export type IncomingMessage = ChatResponse | PongMessage | ErrorMessage;
