import { useState, useRef, useCallback } from "react";

const TARGET_SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 512; // 32ms at 16kHz

/**
 * Microphone recording hook.
 * Captures audio, resamples to 16kHz, and sends PCM16 frames
 * via the provided callback.
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
      const settings = stream.getAudioTracks()[0].getSettings();
      console.log("[Recorder] mic granted, settings:", settings);
    } catch (err) {
      console.error("[Recorder] getUserMedia FAILED:", err);
      alert(
        `麦克风访问失败: ${err}\n\n可能原因：\n1. Tauri WebView2 未授权麦克风\n2. 系统设置屏蔽了麦克风\n3. 没有可用麦克风`,
      );
      return;
    }
    streamRef.current = stream;

    const ctx = new AudioContext({
      sampleRate:
        stream.getAudioTracks()[0].getSettings().sampleRate || 48000,
    });
    contextRef.current = ctx;

    const source = ctx.createMediaStreamSource(stream);
    const processor = ctx.createScriptProcessor(4096, 1, 1);

    const nativeSR = ctx.sampleRate;
    const ratio = nativeSR / TARGET_SAMPLE_RATE;
    let resampleBuffer = new Float32Array(0);

    let frameCount = 0;
    let maxAmplitudeInSecond = 0;
    let lastAmpLog = Date.now();

    processor.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);

      // Track input amplitude for debugging
      for (let i = 0; i < input.length; i++) {
        const a = Math.abs(input[i]);
        if (a > maxAmplitudeInSecond) maxAmplitudeInSecond = a;
      }
      const now = Date.now();
      if (now - lastAmpLog > 1000) {
        console.log(
          `[Recorder] frames sent: ${frameCount}, max amp last 1s: ${maxAmplitudeInSecond.toFixed(4)} ${maxAmplitudeInSecond < 0.01 ? "(SILENT — mic may not work)" : "(OK)"}`,
        );
        maxAmplitudeInSecond = 0;
        lastAmpLog = now;
      }

      const outputLen = Math.floor(input.length / ratio);
      const resampled = new Float32Array(outputLen);
      for (let i = 0; i < outputLen; i++) {
        const srcIndex = i * ratio;
        const idx = Math.floor(srcIndex);
        const frac = srcIndex - idx;
        const a = input[idx] || 0;
        const b = input[Math.min(idx + 1, input.length - 1)] || 0;
        resampled[i] = a + frac * (b - a);
      }

      const combined = new Float32Array(
        resampleBuffer.length + resampled.length,
      );
      combined.set(resampleBuffer);
      combined.set(resampled, resampleBuffer.length);

      let offset = 0;
      while (offset + FRAME_SAMPLES <= combined.length) {
        const frame = combined.subarray(offset, offset + FRAME_SAMPLES);
        const pcm16 = new Int16Array(FRAME_SAMPLES);
        for (let j = 0; j < FRAME_SAMPLES; j++) {
          const s = Math.max(-1, Math.min(1, frame[j]));
          pcm16[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        onFrame(pcm16.buffer);
        frameCount++;
        offset += FRAME_SAMPLES;
      }
      resampleBuffer = combined.subarray(offset);
    };

    source.connect(processor);
    processor.connect(ctx.destination);
    workletRef.current = processor as unknown as AudioWorkletNode;
    setIsRecording(true);
    console.log("[Recorder] recording started");
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
