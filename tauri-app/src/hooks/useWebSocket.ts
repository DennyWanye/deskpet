import { useEffect, useRef, useState, useCallback } from "react";
import { ControlChannel, type ConnectionState } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";

export function useControlChannel(port: number = 8100, secret: string = "") {
  const channelRef = useRef<ControlChannel | null>(null);
  const [state, setState] = useState<ConnectionState>("disconnected");
  const [lastMessage, setLastMessage] = useState<IncomingMessage | null>(null);

  useEffect(() => {
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

  const sendChatV2 = useCallback((text: string) => {
    channelRef.current?.sendChatV2(text);
  }, []);

  const sendInterrupt = useCallback(() => {
    channelRef.current?.sendInterrupt();
  }, []);

  // Expose the underlying channel so feature panels (memory management, etc.)
  // can attach their own listeners / send custom messages without reaching
  // into the control-channel transport directly.
  const getChannel = useCallback(() => channelRef.current, []);

  return { state, lastMessage, sendChat, sendChatV2, sendInterrupt, getChannel };
}
