import { useEffect, useRef, useState, useCallback } from "react";
import { AudioChannel, type AudioConnectionState } from "../ws/AudioChannel";
import type { AudioMessage } from "../types/messages";

export function useAudioChannel(port: number = 8100, secret: string = "") {
  const channelRef = useRef<AudioChannel | null>(null);
  const [state, setState] = useState<AudioConnectionState>("disconnected");
  const [lastMessage, setLastMessage] = useState<AudioMessage | null>(null);

  useEffect(() => {
    const channel = new AudioChannel(port, secret);
    channelRef.current = channel;

    const unsubState = channel.onStateChange(setState);
    const unsubMsg = channel.onJson(setLastMessage);

    channel.connect();

    return () => {
      unsubState();
      unsubMsg();
      channel.disconnect();
      channelRef.current = null;
    };
  }, [port, secret]);

  const sendAudio = useCallback((pcmData: ArrayBuffer) => {
    channelRef.current?.sendAudio(pcmData);
  }, []);

  const getChannel = useCallback(() => channelRef.current, []);

  return { state, lastMessage, sendAudio, getChannel };
}
