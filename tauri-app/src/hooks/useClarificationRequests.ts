// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useRef, useState } from "react";

import type { ControlChannel } from "../ws/ControlChannel";
import type {
  ClarificationRequest,
  ClarificationResponse,
} from "../types/skillPlatform";

export function useClarificationRequests(channel: ControlChannel | null) {
  const [current, setCurrent] = useState<
    ClarificationRequest["payload"] | null
  >(null);
  const queueRef = useRef<ClarificationRequest["payload"][]>([]);

  const showNext = useCallback(() => {
    const next = queueRef.current.shift();
    setCurrent(next ?? null);
  }, []);

  useEffect(() => {
    if (!channel) return undefined;
    const off = channel.onMessage((msg) => {
      if (msg.type !== "clarification_request") return;
      const payload = (msg as ClarificationRequest).payload;
      if (!payload) return;
      if (current === null) {
        setCurrent(payload);
      } else {
        queueRef.current.push(payload);
      }
    });
    return () => {
      off();
    };
  }, [channel, current]);

  const resolve = useCallback(
    (answer: string) => {
      if (!current || !channel) return;
      const reply: ClarificationResponse = {
        type: "clarification_response",
        payload: {
          request_id: current.request_id,
          answer,
        },
      };
      channel.send(reply as unknown as { type: string; payload?: Record<string, unknown> });
      showNext();
    },
    [current, channel, showNext]
  );

  return { current, resolve } as const;
}
