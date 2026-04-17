import { useState, useRef, useCallback, useEffect } from "react";
import type { AudioChannel } from "../ws/AudioChannel";

/**
 * TTS audio player hook.
 *
 * edge-tts streams MP3 in ~4KB chunks, but only the first chunk contains the
 * MP3 header. Browsers' `decodeAudioData` requires a complete MP3 stream, so
 * decoding individual chunks fails silently. We buffer all binary frames per
 * utterance and decode+play once the backend signals `tts_end`.
 *
 * Call `flushAndPlay()` when you receive `tts_end`, or `reset()` to drop the
 * current buffer (used for barge-in).
 */
export function useAudioPlayer(channel: AudioChannel | null) {
  const [isPlaying, setIsPlaying] = useState(false);
  const ctxRef = useRef<AudioContext | null>(null);
  const bufferRef = useRef<Uint8Array[]>([]);
  const currentSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);

  const getContext = useCallback(() => {
    if (!ctxRef.current || ctxRef.current.state === "closed") {
      ctxRef.current = new AudioContext();
      console.log(
        "[AudioPlayer] created AudioContext, state:",
        ctxRef.current.state,
        "sampleRate:",
        ctxRef.current.sampleRate,
      );
    }
    return ctxRef.current;
  }, []);

  // Persistent GainNode for fade-out on barge-in.
  const getGain = useCallback(() => {
    const ctx = getContext();
    if (!gainRef.current) {
      gainRef.current = ctx.createGain();
      gainRef.current.connect(ctx.destination);
    }
    return gainRef.current;
  }, [getContext]);

  /**
   * Warm up the AudioContext — must be called inside a user gesture (click,
   * keydown) so the browser's autoplay policy allows playback later.
   * Otherwise the context stays "suspended" and source.start() produces no
   * audible sound.
   */
  const primeContext = useCallback(async () => {
    const ctx = getContext();
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
        console.log("[AudioPlayer] AudioContext resumed, state:", ctx.state);
      } catch (err) {
        console.warn("[AudioPlayer] AudioContext resume failed:", err);
      }
    }
  }, [getContext]);

  const flushAndPlay = useCallback(async () => {
    if (bufferRef.current.length === 0) {
      console.log("[AudioPlayer] flushAndPlay: empty buffer, nothing to play");
      return;
    }

    // Concatenate all MP3 chunks into a single decodable blob.
    const total = bufferRef.current.reduce((sum, b) => sum + b.byteLength, 0);
    const merged = new Uint8Array(total);
    let offset = 0;
    for (const chunk of bufferRef.current) {
      merged.set(chunk, offset);
      offset += chunk.byteLength;
    }
    bufferRef.current = [];
    console.log(
      "[AudioPlayer] flushAndPlay: decoding",
      total,
      "bytes from",
      bufferRef.current.length,
      "chunks",
    );

    const ctx = getContext();
    try {
      if (ctx.state === "suspended") {
        console.warn(
          "[AudioPlayer] AudioContext is suspended — attempting resume. If this fails, no audio will be heard. Make sure primeContext() was called on user gesture.",
        );
        await ctx.resume();
      }

      const audioBuffer = await ctx.decodeAudioData(merged.buffer.slice(0));
      console.log(
        "[AudioPlayer] decoded:",
        audioBuffer.duration.toFixed(2),
        "s,",
        audioBuffer.numberOfChannels,
        "ch,",
        audioBuffer.sampleRate,
        "Hz",
      );
      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(getGain());
      currentSourceRef.current = source;

      source.onended = () => {
        if (currentSourceRef.current === source) {
          currentSourceRef.current = null;
          setIsPlaying(false);
          console.log("[AudioPlayer] playback ended");
        }
      };

      setIsPlaying(true);
      source.start();
      console.log(
        "[AudioPlayer] playback started (ctx.state:",
        ctx.state,
        ")",
      );
    } catch (err) {
      console.warn("[AudioPlayer] decode failed:", err, "bytes:", total);
      setIsPlaying(false);
    }
  }, [getContext]);

  const reset = useCallback(() => {
    bufferRef.current = [];
  }, []);

  const stop = useCallback(() => {
    bufferRef.current = [];
    try {
      currentSourceRef.current?.stop();
    } catch {
      /* already stopped */
    }
    currentSourceRef.current = null;
    setIsPlaying(false);
  }, []);

  const bargeIn = useCallback(() => {
    console.log("[AudioPlayer] barge-in triggered");
    bufferRef.current = [];
    const ctx = ctxRef.current;
    const gain = gainRef.current;
    if (ctx && gain && currentSourceRef.current) {
      // 50ms fade-out to avoid pop
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.05);
      setTimeout(() => {
        try {
          currentSourceRef.current?.stop();
        } catch {
          /* already stopped */
        }
        currentSourceRef.current = null;
        setIsPlaying(false);
        if (gain) gain.gain.value = 1.0;
        console.log("[AudioPlayer] barge-in complete");
      }, 60);
    } else {
      try {
        currentSourceRef.current?.stop();
      } catch {
        /* already stopped */
      }
      currentSourceRef.current = null;
      setIsPlaying(false);
    }
  }, []);

  // Subscribe to binary audio from the channel — just accumulate, don't decode.
  useEffect(() => {
    if (!channel) return;
    const unsub = channel.onBinary((data) => {
      bufferRef.current.push(new Uint8Array(data));
    });
    return unsub;
  }, [channel]);

  return { isPlaying, stop, flushAndPlay, reset, primeContext, bargeIn };
}
