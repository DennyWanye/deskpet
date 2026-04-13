import { useEffect, useRef, useState, useCallback } from "react";
import { ControlChannel, type ConnectionState } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";

export function useControlChannel(port: number = 8100, secret: string = "") {
  const channelRef = useRef<ControlChannel | null>(null);
  const [state, setState] = useState<ConnectionState>("disconnected");
  const [lastMessage, setLastMessage] = useState<IncomingMessage | null>(null);

  useEffect(() => {
    // Don't connect until we have a valid secret
    if (!secret) return;

    const channel = new ControlChannel(port, secret);
    channelRef.current = channel;

    const unsubState = channel.onStateChange(setState);
    const unsubMsg = channel.onMessage(setLastMessage);

    channel.connect();

    return () => {
      unsubState();
      unsubMsg();
      channel.disconnect();
      channelRef.current = null;
    };
  }, [port, secret]);

  const sendChat = useCallback((text: string) => {
    channelRef.current?.sendChat(text);
  }, []);

  const sendInterrupt = useCallback(() => {
    channelRef.current?.sendInterrupt();
  }, []);

  return { state, lastMessage, sendChat, sendInterrupt };
}
