import { useState, useRef, useCallback, useEffect } from "react";
import type { AudioChannel } from "../ws/AudioChannel";

const SAMPLE_RATE = 24000;
// 收到多少个 PCM chunk 再开播 —— 缓冲 2 块 (≈340ms @ 170ms/块) 够对抗
// WS 抖动又不让首音延迟明显。
const JITTER_BUFFER_SIZE = 2;

/**
 * P2-2-M2: PCM 流式播放器（替代 M1 的"累积 MP3 → tts_end 再一次性 decode"
 * 模式）。
 *
 * 后端走 EdgeTTSProvider.synthesize_pcm_stream 产出 PCM16 24kHz mono，
 * 每块 8192 bytes (4096 samples, ~170ms)。本 hook 把每块 decode 成
 * AudioBuffer 挂在 WebAudio 时间轴上连续播放，形成流式播放；bargeIn()
 * 做 50ms 线性 fade-out + 停所有在途 source + 清队列。
 *
 * 接收侧约定：AudioChannel.onBinary 已剥掉 1 字节 type header，送进来
 * 的 ArrayBuffer 全是 PCM16 字节。
 */
export function useAudioPlayer(channel: AudioChannel | null) {
  const [isPlaying, setIsPlaying] = useState(false);
  const ctxRef = useRef<AudioContext | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  // 在途排好时间轴的 source 节点 —— bargeIn 时逐个 stop
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  // WebAudio 时间轴上"下一块应开始"的时刻
  const nextTimeRef = useRef(0);
  // 抖动缓冲 —— 未到阈值前先攒，够了一把 flush
  const pendingRef = useRef<Int16Array[]>([]);
  const startedRef = useRef(false);

  const getCtx = useCallback(() => {
    if (!ctxRef.current || ctxRef.current.state === "closed") {
      // 强制 24kHz —— 避免浏览器默认 48kHz 时我们 createBuffer 的 PCM
      // 被误以为是 24kHz 半速播放导致"变声"。
      ctxRef.current = new AudioContext({ sampleRate: SAMPLE_RATE });
      console.log(
        "[AudioPlayer] AudioContext created @",
        ctxRef.current.sampleRate,
        "Hz, state:",
        ctxRef.current.state,
      );
    }
    return ctxRef.current;
  }, []);

  const getGain = useCallback(() => {
    const ctx = getCtx();
    if (!gainRef.current) {
      gainRef.current = ctx.createGain();
      gainRef.current.connect(ctx.destination);
    }
    return gainRef.current;
  }, [getCtx]);

  const primeContext = useCallback(async () => {
    const ctx = getCtx();
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
        console.log("[AudioPlayer] AudioContext resumed:", ctx.state);
      } catch (err) {
        console.warn("[AudioPlayer] AudioContext resume failed:", err);
      }
    }
  }, [getCtx]);

  const scheduleChunk = useCallback(
    (pcm16: Int16Array) => {
      const ctx = getCtx();
      const gain = getGain();
      const buf = ctx.createBuffer(1, pcm16.length, SAMPLE_RATE);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < pcm16.length; i++) ch[i] = pcm16[i] / 32768;

      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(gain);

      // 若时间轴已落后当前 currentTime（首块、或 tts_end 后重启），向前
      // 推 10ms 作为新起点。否则直接接续 nextTimeRef。
      const now = ctx.currentTime;
      if (nextTimeRef.current < now - 0.05) {
        nextTimeRef.current = now + 0.01;
      }
      src.start(nextTimeRef.current);
      nextTimeRef.current += buf.duration;

      sourcesRef.current.push(src);
      src.onended = () => {
        sourcesRef.current = sourcesRef.current.filter((s) => s !== src);
        if (sourcesRef.current.length === 0 && !startedRef.current) {
          // 所有排队 source 都播完且没再来新的 —— 回到 idle
          setIsPlaying(false);
        }
      };
      setIsPlaying(true);
    },
    [getCtx, getGain],
  );

  const onPCMChunk = useCallback(
    (data: ArrayBuffer) => {
      const pcm16 = new Int16Array(data);
      if (!startedRef.current) {
        pendingRef.current.push(pcm16);
        if (pendingRef.current.length >= JITTER_BUFFER_SIZE) {
          startedRef.current = true;
          while (pendingRef.current.length > 0) {
            scheduleChunk(pendingRef.current.shift()!);
          }
        }
      } else {
        scheduleChunk(pcm16);
      }
    },
    [scheduleChunk],
  );

  const _clearState = useCallback(() => {
    sourcesRef.current.forEach((s) => {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    });
    sourcesRef.current = [];
    pendingRef.current = [];
    startedRef.current = false;
    nextTimeRef.current = 0;
    setIsPlaying(false);
  }, []);

  const bargeIn = useCallback(() => {
    console.log("[AudioPlayer] barge-in");
    const ctx = ctxRef.current;
    const gain = gainRef.current;
    if (ctx && gain) {
      // 50ms 线性 ramp 到 0，再真正 stop —— 避免 source.stop 的爆音
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.05);
      setTimeout(() => {
        _clearState();
        if (gain) gain.gain.value = 1.0;
      }, 60);
    } else {
      _clearState();
    }
  }, [_clearState]);

  // stop() 语义等同 bargeIn() —— 一个公开别名，避免上层改名
  const stop = useCallback(() => bargeIn(), [bargeIn]);

  // reset() 只清 pending + 复位时间轴，不动在途 source —— 供"上一段
  // utterance 已自然播完、下一段即将开播"的衔接点使用
  const reset = useCallback(() => {
    pendingRef.current = [];
    startedRef.current = false;
    nextTimeRef.current = 0;
  }, []);

  // 订阅二进制 PCM —— AudioChannel 已剥 1 字节 type header
  useEffect(() => {
    if (!channel) return;
    return channel.onBinary((data) => onPCMChunk(data));
  }, [channel, onPCMChunk]);

  // tts_end：让 startedRef 复位，下一段 utterance 重新等满 jitter buffer
  useEffect(() => {
    if (!channel) return;
    return channel.onJson((msg) => {
      if (msg.type === "tts_end") {
        startedRef.current = false;
      }
    });
  }, [channel]);

  return { isPlaying, stop, bargeIn, reset, primeContext };
}
