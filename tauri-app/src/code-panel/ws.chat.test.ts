// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async () => ""),
}));

import { useSessionsStore } from "../stores/sessionsStore";
import { __test_dispatch } from "./ws";

function resetStore() {
  useSessionsStore.setState((s) => ({
    ...s,
    active_sid: "default",
    sessions: {
      default: {
        ...s.sessions.default,
        messages: [],
        status: "idle" as const,
        inflight: false,
      },
    },
  }));
}

describe("ws.dispatch chat final dedupe", () => {
  beforeEach(resetStore);

  it("does not append a duplicate assistant bubble when chat_response already showed the final text", () => {
    __test_dispatch({
      type: "chat_v2_user_echo",
      payload: { session_id: "default", text: "please use a tool" },
    });
    __test_dispatch({
      type: "chat_response",
      payload: { session_id: "default", text: "same assistant answer" },
    });
    __test_dispatch({
      type: "tool_call",
      payload: {
        session_id: "default",
        name: "example_tool",
        arguments: { ok: true },
      },
    });

    __test_dispatch({
      type: "chat_v2_final",
      payload: { session_id: "default", text: "same assistant answer" },
    });

    const messages = useSessionsStore.getState().sessions.default.messages;
    expect(messages.filter((m) => m.role === "assistant")).toHaveLength(1);
    expect(messages.some((m) => m.role === "tool_call")).toBe(true);
    expect(messages[messages.length - 1]).toMatchObject({
      role: "assistant",
      text: "same assistant answer",
    });
  });
});
