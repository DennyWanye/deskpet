/**
 * P4-S20 Wave 1c — usePermissionRequests hook
 *
 * Subscribes to the ControlChannel for `permission_request` messages,
 * exposes a single "currently-shown" request and a resolver callback.
 *
 * Multiple concurrent requests are queued FIFO so the user only sees
 * one popup at a time (the agent loop dispatches tools concurrently
 * via asyncio.gather, but rendering them serially makes the UI
 * predictable).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { ControlChannel } from "../ws/ControlChannel";
import type {
  PermissionRequest,
  PermissionResponse,
} from "../types/skillPlatform";

type Decision = "allow" | "allow_session" | "deny";

export function usePermissionRequests(channel: ControlChannel | null) {
  const [current, setCurrent] = useState<
    PermissionRequest["payload"] | null
  >(null);
  const queueRef = useRef<PermissionRequest["payload"][]>([]);

  const showNext = useCallback(() => {
    const next = queueRef.current.shift();
    setCurrent(next ?? null);
  }, []);

  useEffect(() => {
    if (!channel) return undefined;
    const off = channel.onMessage((msg) => {
      if (msg.type !== "permission_request") return;
      const payload = (msg as PermissionRequest).payload;
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
    (decision: Decision) => {
      if (!current || !channel) return;
      const reply: PermissionResponse = {
        type: "permission_response",
        payload: {
          request_id: current.request_id,
          decision,
        },
      };
      channel.send(reply as unknown as { type: string; payload?: Record<string, unknown> });
      showNext();
    },
    [current, channel, showNext]
  );

  return { current, resolve } as const;
}
