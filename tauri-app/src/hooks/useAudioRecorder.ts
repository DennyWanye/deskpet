import { useState, useRef, useCallback } from "react";

const TARGET_SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 512; // 32ms at 16kHz — Silero VAD requirement

/**
 * AudioWorklet processor: passes raw native-SR audio to the main thread.
 * Resampling and 16kHz framing happen on the main thread so we can emit
 * exact 512-sample @ 16kHz frames that Silero VAD requires.
 *
 * Inlined as Blob URL to avoid WebView2 file-path issues with
 * audioWorklet.addModule().
 */
const WORKLET_SRC = `
class RawPassthrough extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch && ch.length) {
      // Copy — underlying buffer is recycled next call.
      this.port.postMessage(new Float32Array(ch));
    }
    return true;
  }
}
registerProcessor('raw-passthrough', RawPassthrough);
`;

/**
 * Microphone recording hook.
 * Captures audio via AudioWorklet, resamples to 16kHz, emits 512-sample
 * PCM16 frames (32ms each) matching the Silero VAD frame contract.
 */
export function useAudioRecorder(onFrame: (pcm: ArrayBuffer) => void) {
  const [isRecording, setIsRecording] = useState(false);
  const contextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);

  const startRecording = useCallback(async () => {
    if (isRecording) return;
    console.log("[Recorder] requesting mic access...");

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: { ideal: TARGET_SAMPLE_RATE },
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      console.log("[Recorder] mic granted:", stream.getAudioTracks()[0].getSettings());
    } catch (err) {
      console.error("[Recorder] getUserMedia FAILED:", err);
      alert(
        `麦克风访问失败: ${err}\n\n可能原因：\n1. Tauri WebView2 未授权麦克风\n2. 系统设置屏蔽了麦克风\n3. 没有可用麦克风`,
      );
      return;
    }
    streamRef.current = stream;

    const nativeSR =
      stream.getAudioTracks()[0].getSettings().sampleRate || 48000;
    const ctx = new AudioContext({ sampleRate: nativeSR });
    contextRef.current = ctx;

    // Load worklet via Blob URL — avoids WebView2 module path issues.
    const blob = new Blob([WORKLET_SRC], { type: "application/javascript" });
    const blobUrl = URL.createObjectURL(blob);
    await ctx.audioWorklet.addModule(blobUrl);
    URL.revokeObjectURL(blobUrl);

    const source = ctx.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(ctx, "raw-passthrough");

    const ratio = nativeSR / TARGET_SAMPLE_RATE;
    // Accumulator for 16kHz samples across worklet messages, sliced into
    // exact 512-sample frames before sending to backend.
    let resampleBuffer = new Float32Array(0);
    let frameCount = 0;
    let maxAmp = 0;
    let lastLog = Date.now();

    worklet.port.onmessage = (e: MessageEvent<Float32Array>) => {
      const raw = e.data;

      // Amplitude tracking for debug
      for (let i = 0; i < raw.length; i++) {
        const a = Math.abs(raw[i]);
        if (a > maxAmp) maxAmp = a;
      }
      const now = Date.now();
      if (now - lastLog > 1000) {
        console.log(
          `[Recorder] frames: ${frameCount}, max amp: ${maxAmp.toFixed(4)} ${maxAmp < 0.01 ? "(SILENT)" : "(OK)"}`,
        );
        maxAmp = 0;
        lastLog = now;
      }

      // Resample native-SR chunk → 16kHz (linear interpolation).
      const outLen = Math.floor(raw.length / ratio);
      const resampled = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) {
        const srcIdx = i * ratio;
        const idx = Math.floor(srcIdx);
        const frac = srcIdx - idx;
        const a = raw[idx] ?? 0;
        const b = raw[Math.min(idx + 1, raw.length - 1)] ?? 0;
        resampled[i] = a + frac * (b - a);
      }

      // Append to rolling 16kHz buffer, slice into 512-sample frames.
      const combined = new Float32Array(resampleBuffer.length + resampled.length);
      combined.set(resampleBuffer);
      combined.set(resampled, resampleBuffer.length);

      let off = 0;
      while (off + FRAME_SAMPLES <= combined.length) {
        const pcm16 = new Int16Array(FRAME_SAMPLES);
        for (let j = 0; j < FRAME_SAMPLES; j++) {
          const s = Math.max(-1, Math.min(1, combined[off + j]));
          pcm16[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        onFrame(pcm16.buffer);
        frameCount++;
        off += FRAME_SAMPLES;
      }
      resampleBuffer = combined.subarray(off);
    };

    source.connect(worklet);
    // AudioWorklet doesn't need to connect to destination — it's input-only.
    workletRef.current = worklet;
    setIsRecording(true);
    console.log("[Recorder] AudioWorklet recording started");
  }, [isRecording, onFrame]);

  const stopRecording = useCallback(() => {
    console.log("[Recorder] stopping");
    workletRef.current?.disconnect();
    workletRef.current = null;
    contextRef.current?.close();
    contextRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setIsRecording(false);
  }, []);

  return { isRecording, startRecording, stopRecording };
}
