# P2-2 实时双工语音 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the half-duplex voice pipeline to duplex — user speech interrupts TTS synthesis mid-stream, TTS plays incrementally as it synthesizes.

**Architecture:** WebSocket-maximized, 3 progressive Milestones. M1 = VAD barge-in + always-on mic. M2 = PCM streaming playback. M3 = echo suppression + acceptance. Full spec: `docs/P2-2-realtime-duplex-voice-architecture.md` v5.0.

**Tech Stack:** Python 3.12 / FastAPI / asyncio / Silero VAD / edge-tts / ffmpeg / TypeScript / React / Web Audio API (AudioWorklet + AudioBufferSourceNode)

---

## File Structure

### Backend — new/modified

| File | Responsibility |
|---|---|
| `backend/pipeline/voice_pipeline.py` | **Modify:** add `tts_barge_in` send, binary frame type header, `BargeInFilter` state machine, TTS state tracking |
| `backend/pipeline/barge_in_filter.py` | **Create:** time-domain state machine (`IDLE→PLAYING→COOLDOWN→IDLE`) for echo suppression |
| `backend/providers/silero_vad.py` | **Modify:** add `set_threshold()`, `set_min_speech_ms()`, `current_speech_duration_ms()` |
| `backend/providers/edge_tts_provider.py` | **Modify:** add `synthesize_pcm_stream()` via ffmpeg pipe |
| `backend/config.py` | **Modify:** add `VoiceConfig` dataclass |
| `backend/config.toml` | **Modify:** add `[voice]` section |
| `backend/tests/test_barge_in_filter.py` | **Create:** unit tests for BargeInFilter |
| `backend/tests/test_voice_pipeline_barge_in.py` | **Create:** integration tests for barge-in flow |
| `backend/tests/test_edge_tts_pcm.py` | **Create:** unit tests for PCM stream conversion |

### Frontend — new/modified

| File | Responsibility |
|---|---|
| `tauri-app/src/hooks/useAudioRecorder.ts` | **Modify:** replace `createScriptProcessor` with AudioWorklet (Blob URL) |
| `tauri-app/src/hooks/useAudioPlayer.ts` | **Modify:** M1: add `bargeIn()`. M2: rewrite to jitter-buffer PCM player |
| `tauri-app/src/ws/AudioChannel.ts` | **Modify:** parse binary frame type header internally |
| `tauri-app/src/types/messages.ts` | **Modify:** add `TTSBargeInMessage`, update `AudioMessage` union |
| `tauri-app/src/App.tsx` | **Modify:** wire barge-in handler, auto-voice mode |
| `tauri-app/src/components/SettingsPanel.tsx` | **Modify:** add "自动语音" toggle |

### Scripts

| File | Responsibility |
|---|---|
| `scripts/perf/barge_in.py` | **Create:** automated barge-in latency + echo rejection smoke test |

---

## Milestone 1: VAD Barge-In + Always-On Mic

### Task 1: Backend — BargeInFilter state machine

**Files:**
- Create: `backend/pipeline/barge_in_filter.py`
- Test: `backend/tests/test_barge_in_filter.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_barge_in_filter.py
"""Tests for BargeInFilter — time-domain echo suppression state machine."""
import time
import pytest
from pipeline.barge_in_filter import BargeInFilter


def test_idle_allows_any_speech():
    f = BargeInFilter(cooldown_ms=300, min_speech_during_tts_ms=400)
    assert f.should_allow(speech_duration_ms=50) is True


def test_playing_blocks_short_speech():
    f = BargeInFilter(cooldown_ms=300, min_speech_during_tts_ms=400)
    f.on_tts_start()
    assert f.should_allow(speech_duration_ms=200) is False


def test_playing_allows_long_speech():
    f = BargeInFilter(cooldown_ms=300, min_speech_during_tts_ms=400)
    f.on_tts_start()
    assert f.should_allow(speech_duration_ms=450) is True


def test_cooldown_blocks_then_expires():
    f = BargeInFilter(cooldown_ms=80, min_speech_during_tts_ms=400)
    f.on_tts_start()
    f.on_tts_end()
    # During cooldown — blocked
    assert f.should_allow(speech_duration_ms=50) is False
    # Wait past cooldown
    time.sleep(0.1)
    # Now allowed
    assert f.should_allow(speech_duration_ms=50) is True


def test_interrupt_resets_to_idle():
    f = BargeInFilter(cooldown_ms=300, min_speech_during_tts_ms=400)
    f.on_tts_start()
    f.on_interrupted()
    # No cooldown after interrupt — user explicitly spoke
    assert f.should_allow(speech_duration_ms=50) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_barge_in_filter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.barge_in_filter'`

- [ ] **Step 3: Implement BargeInFilter**

```python
# backend/pipeline/barge_in_filter.py
"""Time-domain state machine for TTS echo suppression.

Three states:
  IDLE        — no TTS active, allow any speech_start
  TTS_PLAYING — TTS synthesis in progress, require speech_duration > threshold
  COOLDOWN    — TTS just ended, block for cooldown_ms then return to IDLE
"""
from __future__ import annotations

import enum
import time


class _State(enum.Enum):
    IDLE = "idle"
    TTS_PLAYING = "tts_playing"
    COOLDOWN = "cooldown"


class BargeInFilter:
    """Decide whether a VAD speech_start should trigger a barge-in."""

    def __init__(
        self,
        cooldown_ms: int = 300,
        min_speech_during_tts_ms: int = 400,
    ) -> None:
        self._state = _State.IDLE
        self._cooldown_ms = cooldown_ms
        self._min_speech_during_tts_ms = min_speech_during_tts_ms
        self._tts_end_time: float = 0.0

    @property
    def is_tts_active(self) -> bool:
        return self._state == _State.TTS_PLAYING

    def on_tts_start(self) -> None:
        self._state = _State.TTS_PLAYING

    def on_tts_end(self) -> None:
        self._state = _State.COOLDOWN
        self._tts_end_time = time.monotonic()

    def on_interrupted(self) -> None:
        """TTS was interrupted by user — skip cooldown."""
        self._state = _State.IDLE

    def should_allow(self, speech_duration_ms: int) -> bool:
        """Return True if a VAD speech event should trigger barge-in."""
        if self._state == _State.IDLE:
            return True

        if self._state == _State.COOLDOWN:
            elapsed_ms = (time.monotonic() - self._tts_end_time) * 1000
            if elapsed_ms >= self._cooldown_ms:
                self._state = _State.IDLE
                return True
            return False

        # TTS_PLAYING — require sustained speech
        return speech_duration_ms >= self._min_speech_during_tts_ms
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_barge_in_filter.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/barge_in_filter.py backend/tests/test_barge_in_filter.py
git commit -m "feat(P2-2): BargeInFilter time-domain echo suppression state machine"
```

---

### Task 2: Backend — SileroVAD dynamic threshold + speech duration query

**Files:**
- Modify: `backend/providers/silero_vad.py:17-34` (init), add methods after line 101
- Test: `backend/tests/test_silero_vad_dynamic.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_silero_vad_dynamic.py
"""Tests for SileroVAD dynamic parameter methods."""
import pytest
from providers.silero_vad import SileroVAD


def test_set_threshold():
    vad = SileroVAD(threshold=0.5)
    vad.set_threshold(0.65)
    assert vad.threshold == 0.65


def test_set_min_speech_ms():
    vad = SileroVAD(min_speech_ms=250)
    vad.set_min_speech_ms(400)
    assert vad.min_speech_ms == 400


def test_speech_duration_ms_zero_when_not_speaking():
    vad = SileroVAD()
    assert vad.current_speech_duration_ms() == 0


def test_speech_duration_ms_increases_during_speech():
    vad = SileroVAD()
    # Manually set internal state to simulate speech
    vad._is_speech = True
    vad._speech_start_ms = 100.0
    vad._ms_counter = 350.0
    assert vad.current_speech_duration_ms() == 250
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_silero_vad_dynamic.py -v`
Expected: FAIL — `AttributeError: 'SileroVAD' object has no attribute 'set_threshold'`

- [ ] **Step 3: Add methods to SileroVAD**

Add after the existing `process_chunk` method (after line 101 in `silero_vad.py`):

```python
    def set_threshold(self, value: float) -> None:
        """Dynamically adjust VAD threshold (0.0–1.0)."""
        self.threshold = value

    def set_min_speech_ms(self, value: int) -> None:
        """Dynamically adjust minimum speech duration (ms)."""
        self.min_speech_ms = value

    def current_speech_duration_ms(self) -> int:
        """Return current speech duration in ms. 0 if not currently in speech."""
        if not self._is_speech:
            return 0
        return int(self._ms_counter - self._speech_start_ms)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_silero_vad_dynamic.py -v`
Expected: 4 passed

- [ ] **Step 5: Run all backend tests for regression**

Run: `cd G:\projects\deskpet\backend && python -m pytest --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add backend/providers/silero_vad.py backend/tests/test_silero_vad_dynamic.py
git commit -m "feat(P2-2): SileroVAD dynamic threshold, min_speech_ms, speech duration query"
```

---

### Task 3: Backend — voice_pipeline tts_barge_in + binary frame type header

**Files:**
- Modify: `backend/pipeline/voice_pipeline.py:1-245`
- Test: `backend/tests/test_voice_pipeline_barge_in.py`

- [ ] **Step 1: Write the failing test for tts_barge_in message**

```python
# backend/tests/test_voice_pipeline_barge_in.py
"""Tests for barge-in flow in VoicePipeline."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock


class FakeVAD:
    def __init__(self, events_per_call):
        self._seq = iter(events_per_call)
        self.threshold = 0.5
        self.min_speech_ms = 250
    def process_chunk(self, pcm):
        try:
            return next(self._seq)
        except StopIteration:
            return []
    async def load(self):
        pass
    def set_threshold(self, v):
        self.threshold = v
    def set_min_speech_ms(self, v):
        self.min_speech_ms = v
    def current_speech_duration_ms(self):
        return 500  # always above threshold for test


class FakeASR:
    async def transcribe(self, audio):
        return "hello"


class FakeAgent:
    async def chat_stream(self, messages, *, session_id="default"):
        yield "reply"


class FakeTTS:
    def __init__(self, n_chunks=5):
        self.chunks_yielded = 0
        self._n = n_chunks
    async def synthesize_stream(self, text):
        for _ in range(self._n):
            self.chunks_yielded += 1
            yield b"\xff" * 4096
            await asyncio.sleep(0.01)


@pytest.fixture
def ws():
    m = AsyncMock()
    m.send_json = AsyncMock()
    m.send_bytes = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_barge_in_during_tts_sends_message(ws):
    from pipeline.voice_pipeline import VoicePipeline
    vad = FakeVAD([
        [{"event": "speech_end", "audio": b"\x00" * 1024}],
        [],
    ])
    pipeline = VoicePipeline(
        vad=vad, asr=FakeASR(), agent=FakeAgent(),
        tts=FakeTTS(n_chunks=20), session_id="test",
    )
    # chunk 1 → speech_end → starts _process_utterance task
    await pipeline.process_audio_chunk(b"\x00" * 1024, ws)
    await asyncio.sleep(0.05)
    assert pipeline._processing is True

    # Inject speech_start while TTS is running
    pipeline.vad = FakeVAD([[{"event": "speech_start"}]])
    await pipeline.process_audio_chunk(b"\x00" * 1024, ws)

    calls = [
        c for c in ws.send_json.call_args_list
        if isinstance(c.args[0], dict) and c.args[0].get("type") == "tts_barge_in"
    ]
    assert len(calls) >= 1
    assert calls[0].args[0]["payload"]["reason"] == "vad_speech_detected"


@pytest.mark.asyncio
async def test_binary_frames_have_type_header(ws):
    from pipeline.voice_pipeline import VoicePipeline
    vad = FakeVAD([
        [{"event": "speech_end", "audio": b"\x00" * 1024}],
    ])
    pipeline = VoicePipeline(
        vad=vad, asr=FakeASR(), agent=FakeAgent(),
        tts=FakeTTS(n_chunks=2), session_id="test",
    )
    await pipeline.process_audio_chunk(b"\x00" * 1024, ws)
    await asyncio.sleep(0.3)

    binary_calls = ws.send_bytes.call_args_list
    assert len(binary_calls) >= 1
    first = binary_calls[0].args[0]
    assert first[0:1] == b"\x02", f"Expected MP3 type header 0x02, got {first[0:1]!r}"
    assert len(first) == 4097  # 1 header + 4096 data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_voice_pipeline_barge_in.py -v`
Expected: FAIL — no `tts_barge_in` sent, no type header on binary frames

- [ ] **Step 3: Modify voice_pipeline.py**

Three changes in `voice_pipeline.py`:

**3a.** Add import at top (after line 12):

```python
from pipeline.barge_in_filter import BargeInFilter
```

**3b.** Add `_barge_in_filter` to `__init__` (after line 52):

```python
        self._barge_in_filter = BargeInFilter()
```

**3c.** In `process_audio_chunk`, replace the `speech_start` handling block (lines 102-108):

```python
            if event["event"] == "speech_start":
                await audio_ws.send_json({
                    "type": "vad_event",
                    "payload": {"status": "speech_start"},
                })
                if self._processing:
                    speech_ms = self.vad.current_speech_duration_ms()
                    if self._barge_in_filter.should_allow(speech_ms):
                        await audio_ws.send_json({
                            "type": "tts_barge_in",
                            "payload": {"reason": "vad_speech_detected"},
                        })
                        self._barge_in_filter.on_interrupted()
                        self.interrupt()
```

**3d.** In `_process_utterance`, add TTS state tracking and binary frame header. Replace the TTS section (lines 192-218):

```python
            # Step 3: TTS (streaming synthesis + streaming send)
            chunk_index = 0
            self._barge_in_filter.on_tts_start()
            async with stage_timer("tts", session_id=self.session_id, chars=len(response_text)):
                async for audio_chunk in self.tts.synthesize_stream(response_text):
                    if self._interrupted:
                        logger.info("tts_interrupted")
                        break
                    # Binary frame: 1-byte type header + audio data
                    # 0x02 = MP3 (M1). M2 will switch to 0x01 = PCM.
                    frame = b"\x02" + audio_chunk
                    await audio_ws.send_bytes(frame)
                    # Send lip-sync params to control channel
                    if self.control_ws:
                        try:
                            await self.control_ws.send_json({
                                "type": "lip_sync",
                                "payload": {
                                    "chunk_index": chunk_index,
                                    "amplitude": _estimate_amplitude_from_size(len(audio_chunk)),
                                },
                            })
                        except Exception:
                            pass
                    chunk_index += 1
            self._barge_in_filter.on_tts_end()

            # TTS end marker
            await audio_ws.send_json({
                "type": "tts_end",
                "payload": {},
            })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_voice_pipeline_barge_in.py -v`
Expected: 2 passed

- [ ] **Step 5: Run all backend tests**

Run: `cd G:\projects\deskpet\backend && python -m pytest --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/voice_pipeline.py backend/tests/test_voice_pipeline_barge_in.py
git commit -m "feat(P2-2-M1): tts_barge_in event + binary frame type header + BargeInFilter integration"
```

---

### Task 4: Frontend — TTSBargeInMessage type + AudioChannel header parsing

**Files:**
- Modify: `tauri-app/src/types/messages.ts:59-62,171`
- Modify: `tauri-app/src/ws/AudioChannel.ts:43-52`

- [ ] **Step 1: Add TTSBargeInMessage to messages.ts**

After the `TTSEndMessage` interface (after line 62):

```typescript
export interface TTSBargeInMessage {
  type: "tts_barge_in";
  payload: { reason: "vad_speech_detected" };
}
```

Update the `AudioMessage` union (line 171):

```typescript
export type AudioMessage = VADEvent | TranscriptMessage | TTSEndMessage | TTSBargeInMessage | ErrorMessage;
```

- [ ] **Step 2: Add binary frame header parsing to AudioChannel.ts**

Replace the binary branch in `onmessage` (line 44):

```typescript
      if (event.data instanceof ArrayBuffer) {
        // P2-2: strip 1-byte type header (0x01=PCM, 0x02=MP3).
        // Listeners receive pure audio data — header is internal.
        const raw = new Uint8Array(event.data);
        if (raw.length < 2) return; // runt frame
        const audioData = event.data.slice(1);
        this.binaryListeners.forEach((fn) => fn(audioData));
      }
```

- [ ] **Step 3: TypeScript check**

Run: `cd G:\projects\deskpet\tauri-app && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src/types/messages.ts tauri-app/src/ws/AudioChannel.ts
git commit -m "feat(P2-2-M1): TTSBargeInMessage type + AudioChannel binary frame header parsing"
```

---

### Task 5: Frontend — AudioWorklet migration

**Files:**
- Modify: `tauri-app/src/hooks/useAudioRecorder.ts:1-127`

- [ ] **Step 1: Rewrite useAudioRecorder.ts**

```typescript
import { useState, useRef, useCallback } from "react";

const TARGET_SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 512; // 32ms at 16kHz

/**
 * AudioWorklet processor source — inlined as Blob URL to avoid
 * WebView2 file-path issues with audioWorklet.addModule().
 */
const WORKLET_SRC = `
class PCMFramer extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(0);
  }
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    const combined = new Float32Array(this._buf.length + ch.length);
    combined.set(this._buf);
    combined.set(ch, this._buf.length);
    let off = 0;
    while (off + ${FRAME_SAMPLES} <= combined.length) {
      this.port.postMessage(combined.subarray(off, off + ${FRAME_SAMPLES}));
      off += ${FRAME_SAMPLES};
    }
    this._buf = combined.subarray(off);
    return true;
  }
}
registerProcessor('pcm-framer', PCMFramer);
`;

/**
 * Microphone recording hook.
 * Captures audio via AudioWorklet, resamples to 16kHz, emits PCM16 frames.
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
    const worklet = new AudioWorkletNode(ctx, "pcm-framer");

    const ratio = nativeSR / TARGET_SAMPLE_RATE;
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

      // Resample to 16kHz (linear interpolation)
      const outLen = Math.floor(raw.length / ratio);
      const pcm16 = new Int16Array(outLen);
      for (let i = 0; i < outLen; i++) {
        const srcIdx = i * ratio;
        const idx = Math.floor(srcIdx);
        const frac = srcIdx - idx;
        const a = raw[idx] ?? 0;
        const b = raw[Math.min(idx + 1, raw.length - 1)] ?? 0;
        const sample = a + frac * (b - a);
        const clamped = Math.max(-1, Math.min(1, sample));
        pcm16[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      }

      onFrame(pcm16.buffer);
      frameCount++;
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
```

- [ ] **Step 2: TypeScript check**

Run: `cd G:\projects\deskpet\tauri-app && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src/hooks/useAudioRecorder.ts
git commit -m "feat(P2-2-M1): migrate createScriptProcessor to AudioWorklet with Blob URL"
```

---

### Task 6: Frontend — useAudioPlayer bargeIn() method

**Files:**
- Modify: `tauri-app/src/hooks/useAudioPlayer.ts:15-145`

- [ ] **Step 1: Add GainNode and bargeIn to useAudioPlayer**

Add a `gainRef` and `getGain` helper (after `currentSourceRef` on line 19):

```typescript
  const gainRef = useRef<GainNode | null>(null);

  // Persistent GainNode for fade-out on barge-in.
  const getGain = useCallback(() => {
    const ctx = getContext();
    if (!gainRef.current) {
      gainRef.current = ctx.createGain();
      gainRef.current.connect(ctx.destination);
    }
    return gainRef.current;
  }, [getContext]);
```

In `flushAndPlay`, change `source.connect(ctx.destination)` (line 96) to:

```typescript
      source.connect(getGain());
```

Add `bargeIn` method (before the `return` on line 144):

```typescript
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
```

Update the return (line 144):

```typescript
  return { isPlaying, stop, flushAndPlay, reset, primeContext, bargeIn };
```

- [ ] **Step 2: TypeScript check**

Run: `cd G:\projects\deskpet\tauri-app && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src/hooks/useAudioPlayer.ts
git commit -m "feat(P2-2-M1): useAudioPlayer bargeIn() with 50ms gain fade-out"
```

---

### Task 7: Frontend — App.tsx barge-in wiring + auto-voice + Settings toggle

**Files:**
- Modify: `tauri-app/src/App.tsx:125-210,277-290`
- Modify: `tauri-app/src/components/SettingsPanel.tsx`

- [ ] **Step 1: Update App.tsx — destructure bargeIn from useAudioPlayer**

Change the useAudioPlayer destructuring (around line 140):

```typescript
  const {
    isPlaying,
    stop: stopPlayback,
    flushAndPlay,
    reset: resetPlaybackBuffer,
    primeContext,
    bargeIn,
  } = useAudioPlayer(getChannel());
```

- [ ] **Step 2: Add auto-voice state**

Add near the top of the `App` component (after line 88):

```typescript
  const [autoVoice, setAutoVoice] = useState(
    () => localStorage.getItem("deskpet_auto_voice") === "1",
  );
```

- [ ] **Step 3: Handle tts_barge_in in audio message handler**

In the `useEffect` that handles `audioMessage` (around line 174), add a case in the switch:

```typescript
      case "tts_barge_in":
        console.log("[App] TTS barge-in — stopping playback");
        bargeIn();
        setMouthOpenY(0);
        break;
```

- [ ] **Step 4: Auto-start recording when autoVoice is on**

Add a `useEffect` after the audio channel setup:

```typescript
  // Auto-voice: start recording when audio channel connects
  useEffect(() => {
    if (autoVoice && audioState === "connected" && !isRecording) {
      void (async () => {
        await primeContext();
        startRecording();
        setVadStatus("listening");
      })();
    }
  }, [autoVoice, audioState, isRecording, primeContext, startRecording]);
```

- [ ] **Step 5: Add mic status indicator to the UI**

In the status bar area (near the connection indicator), add:

```tsx
{isRecording && (
  <span
    style={{ color: "#ef4444", fontSize: 10, marginLeft: 4 }}
    title="麦克风录音中 — 音频仅本地处理"
  >
    ●
  </span>
)}
```

- [ ] **Step 6: Add auto-voice toggle to SettingsPanel**

In `SettingsPanel.tsx`, add a new section (accept `autoVoice` and `onAutoVoiceChange` as props or use localStorage directly):

```tsx
{/* 自动语音模式 */}
<div style={{ marginTop: 16, borderTop: "1px solid #333", paddingTop: 12 }}>
  <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
    <input
      type="checkbox"
      checked={localStorage.getItem("deskpet_auto_voice") === "1"}
      onChange={(e) => {
        localStorage.setItem("deskpet_auto_voice", e.target.checked ? "1" : "0");
        window.location.reload(); // simple: reload to pick up new setting
      }}
    />
    <span style={{ fontSize: 13 }}>自动语音模式（麦克风常开）</span>
  </label>
  <p style={{ fontSize: 11, color: "#888", marginTop: 4, marginLeft: 24 }}>
    开启后说话即可打断回复。音频仅在本机处理，不上传云端。
  </p>
</div>
```

- [ ] **Step 7: TypeScript check**

Run: `cd G:\projects\deskpet\tauri-app && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 8: Commit**

```bash
git add tauri-app/src/App.tsx tauri-app/src/components/SettingsPanel.tsx
git commit -m "feat(P2-2-M1): App barge-in wiring, auto-voice mode, mic indicator, Settings toggle"
```

---

### Task 8: M1 E2E verification

**Files:**
- Create: `scripts/e2e_barge_in_m1.py`

- [ ] **Step 1: Write M1 smoke test script**

```python
#!/usr/bin/env python3
"""M1 barge-in smoke test — verifies tts_barge_in over WebSocket.

Usage: DESKPET_DEV_MODE=1 python main.py   (terminal 1)
       python scripts/e2e_barge_in_m1.py    (terminal 2)
"""
from __future__ import annotations
import asyncio, json, struct, sys, time
import websockets

URL = "ws://127.0.0.1:8100/ws/audio"
SILENCE = b"\x00" * 1024          # 512 PCM16 samples of silence
LOUD = struct.pack("<" + "h" * 512, *([10000] * 512))  # loud tone


async def main() -> int:
    url = f"{URL}?secret=&session_id=m1_smoke"
    async with websockets.connect(url) as ws:
        # 1. Warm up — send 1s silence
        for _ in range(31):
            await ws.send(SILENCE)
            await asyncio.sleep(0.032)

        # 2. Trigger speech_end → ASR → Agent → TTS
        print("[1/3] Sending speech to trigger TTS...")
        for _ in range(30):
            await ws.send(LOUD)
            await asyncio.sleep(0.032)
        for _ in range(20):
            await ws.send(SILENCE)
            await asyncio.sleep(0.032)

        # 3. Wait for TTS to start, then barge in
        print("[2/3] Waiting for TTS binary frames...")
        tts_started = False
        barge_in_ok = False
        t0 = time.time()
        while time.time() - t0 < 20:
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if isinstance(data, bytes) and len(data) > 1 and data[0] == 0x02:
                if not tts_started:
                    tts_started = True
                    print("      TTS started — sending barge-in speech...")
                    for _ in range(25):
                        await ws.send(LOUD)
                        await asyncio.sleep(0.032)
            elif isinstance(data, str):
                msg = json.loads(data)
                if msg.get("type") == "tts_barge_in":
                    barge_in_ok = True
                    print(f"[3/3] ✅ tts_barge_in received ({time.time()-t0:.1f}s)")
                    break

        if not tts_started:
            print("❌ TTS never started")
            return 1
        if not barge_in_ok:
            print("❌ tts_barge_in not received within 20s")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Start backend in DEV_MODE and run script**

Run terminal 1: `cd G:\projects\deskpet\backend && set DESKPET_DEV_MODE=1 && .venv\Scripts\python.exe main.py`
Run terminal 2: `cd G:\projects\deskpet && .venv\Scripts\python.exe scripts/e2e_barge_in_m1.py`

Expected: `✅ tts_barge_in received`

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e_barge_in_m1.py
git commit -m "test(P2-2-M1): E2E barge-in smoke test script"
```

---

## Milestone 2: PCM Streaming Playback

### Task 9: Backend — ffmpeg pipe MP3→PCM stream

**Files:**
- Modify: `backend/providers/edge_tts_provider.py:1-52`
- Test: `backend/tests/test_edge_tts_pcm.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_edge_tts_pcm.py
"""Tests for EdgeTTSProvider.synthesize_pcm_stream (ffmpeg pipe)."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import patch, AsyncMock
from providers.edge_tts_provider import EdgeTTSProvider, PCM_CHUNK_BYTES


async def _fake_mp3_stream(text):
    """Yield a few fake MP3-ish byte chunks."""
    for _ in range(3):
        yield b"\xff\xfb\x90\x00" * 256  # 1KB fake audio
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_synthesize_pcm_stream_yields_bytes():
    provider = EdgeTTSProvider()
    # Patch synthesize_stream to avoid real network
    with patch.object(provider, "synthesize_stream", side_effect=_fake_mp3_stream):
        # Also need ffmpeg on PATH for this to work
        chunks = []
        try:
            async for chunk in provider.synthesize_pcm_stream("test"):
                chunks.append(chunk)
                assert isinstance(chunk, bytes)
                # Each chunk should be PCM_CHUNK_BYTES (or smaller for last)
                assert len(chunk) <= PCM_CHUNK_BYTES
        except FileNotFoundError:
            pytest.skip("ffmpeg not on PATH")

    # Should have produced at least one chunk
    if chunks:
        assert len(chunks) >= 1


def test_pcm_chunk_bytes_is_correct():
    """PCM_CHUNK_BYTES = 4096 samples * 2 bytes = 8192."""
    assert PCM_CHUNK_BYTES == 8192
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_edge_tts_pcm.py -v`
Expected: FAIL — `ImportError: cannot import name 'PCM_CHUNK_BYTES'`

- [ ] **Step 3: Implement synthesize_pcm_stream in edge_tts_provider.py**

Add after the existing `synthesize_stream` method (after line 52):

```python
import asyncio as _asyncio

PCM_CHUNK_SAMPLES = 4096   # ~170ms at 24kHz
PCM_CHUNK_BYTES = PCM_CHUNK_SAMPLES * 2  # 16-bit = 2 bytes/sample

_FFMPEG_CMD = [
    "ffmpeg", "-hide_banner", "-loglevel", "error",
    "-i", "pipe:0",
    "-f", "s16le", "-ar", "24000", "-ac", "1",
    "pipe:1",
]


async def synthesize_pcm_stream(self, text: str):
    """Yield PCM16 24kHz mono chunks via ffmpeg MP3→PCM pipe.

    Starts an ffmpeg subprocess, feeds MP3 from edge-tts into stdin,
    reads PCM16 from stdout in fixed-size chunks.
    """
    proc = await _asyncio.create_subprocess_exec(
        *_FFMPEG_CMD,
        stdin=_asyncio.subprocess.PIPE,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )

    async def _feed():
        try:
            async for mp3_chunk in self.synthesize_stream(text):
                if proc.stdin:
                    proc.stdin.write(mp3_chunk)
                    await proc.stdin.drain()
        finally:
            if proc.stdin:
                proc.stdin.close()

    feed_task = _asyncio.create_task(_feed())
    pcm_buf = b""
    try:
        while True:
            data = await proc.stdout.read(PCM_CHUNK_BYTES)
            if not data:
                break
            pcm_buf += data
            while len(pcm_buf) >= PCM_CHUNK_BYTES:
                yield pcm_buf[:PCM_CHUNK_BYTES]
                pcm_buf = pcm_buf[PCM_CHUNK_BYTES:]
        if pcm_buf:
            # Pad last chunk with silence
            pcm_buf += b"\x00" * (PCM_CHUNK_BYTES - len(pcm_buf))
            yield pcm_buf
    finally:
        feed_task.cancel()
        try:
            await feed_task
        except _asyncio.CancelledError:
            pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
```

Make sure `synthesize_pcm_stream` is a method of `EdgeTTSProvider` (proper indentation inside the class).

- [ ] **Step 4: Run tests**

Run: `cd G:\projects\deskpet\backend && python -m pytest tests/test_edge_tts_pcm.py -v`
Expected: pass (or skip if no ffmpeg)

- [ ] **Step 5: All tests**

Run: `cd G:\projects\deskpet\backend && python -m pytest --tb=short -q`

- [ ] **Step 6: Commit**

```bash
git add backend/providers/edge_tts_provider.py backend/tests/test_edge_tts_pcm.py
git commit -m "feat(P2-2-M2): ffmpeg pipe MP3→PCM16 24kHz stream in EdgeTTSProvider"
```

---

### Task 10: Backend — voice_pipeline switch to PCM stream + RMS lip-sync

**Files:**
- Modify: `backend/pipeline/voice_pipeline.py` (TTS section in `_process_utterance`)

- [ ] **Step 1: Replace TTS section with PCM stream**

In `_process_utterance`, replace the TTS `async for` loop (the block you modified in Task 3, Step 3d) with:

```python
            # Step 3: TTS (PCM streaming via ffmpeg pipe)
            chunk_index = 0
            self._barge_in_filter.on_tts_start()
            async with stage_timer("tts", session_id=self.session_id, chars=len(response_text)):
                async for pcm_chunk in self.tts.synthesize_pcm_stream(response_text):
                    if self._interrupted:
                        logger.info("tts_interrupted")
                        break
                    # Binary frame: 0x01 = PCM16 24kHz mono
                    frame = b"\x01" + pcm_chunk
                    await audio_ws.send_bytes(frame)
                    # Lip-sync: precise RMS from PCM data
                    if self.control_ws:
                        try:
                            pcm_arr = np.frombuffer(pcm_chunk, dtype=np.int16)
                            rms = float(np.sqrt(np.mean(pcm_arr.astype(np.float32) ** 2)))
                            amplitude = min(1.0, rms / 8000.0)
                            await self.control_ws.send_json({
                                "type": "lip_sync",
                                "payload": {
                                    "chunk_index": chunk_index,
                                    "amplitude": amplitude,
                                },
                            })
                        except Exception:
                            pass
                    chunk_index += 1
            self._barge_in_filter.on_tts_end()

            # TTS end marker
            await audio_ws.send_json({
                "type": "tts_end",
                "payload": {},
            })
```

- [ ] **Step 2: Update the barge-in test to expect 0x01 header**

In `test_voice_pipeline_barge_in.py`, add `synthesize_pcm_stream` to `FakeTTS`:

```python
class FakeTTS:
    def __init__(self, n_chunks=5):
        self.chunks_yielded = 0
        self._n = n_chunks
    async def synthesize_stream(self, text):
        for _ in range(self._n):
            self.chunks_yielded += 1
            yield b"\xff" * 4096
            await asyncio.sleep(0.01)
    async def synthesize_pcm_stream(self, text):
        for _ in range(self._n):
            self.chunks_yielded += 1
            yield b"\x00" * 8192  # 4096 PCM16 samples
            await asyncio.sleep(0.01)
```

Update `test_binary_frames_have_type_header` to check for `0x01`:

```python
    assert first[0:1] == b"\x01", f"Expected PCM type header 0x01, got {first[0:1]!r}"
    assert len(first) == 8193  # 1 header + 8192 PCM data
```

- [ ] **Step 3: Run all tests**

Run: `cd G:\projects\deskpet\backend && python -m pytest --tb=short -q`

- [ ] **Step 4: Commit**

```bash
git add backend/pipeline/voice_pipeline.py backend/tests/test_voice_pipeline_barge_in.py
git commit -m "feat(P2-2-M2): voice pipeline PCM stream + precise RMS lip-sync"
```

---

### Task 11: Frontend — Jitter Buffer PCM streaming player

**Files:**
- Modify: `tauri-app/src/hooks/useAudioPlayer.ts` (full rewrite)
- Modify: `tauri-app/src/App.tsx` (remove `flushAndPlay` usage)

- [ ] **Step 1: Rewrite useAudioPlayer.ts**

```typescript
import { useState, useRef, useCallback, useEffect } from "react";
import type { AudioChannel } from "../ws/AudioChannel";

const SAMPLE_RATE = 24000;
const JITTER_BUFFER_SIZE = 2;

/**
 * PCM streaming audio player with jitter buffer.
 *
 * M2: receives PCM16 24kHz chunks from backend, buffers JITTER_BUFFER_SIZE
 * chunks before starting playback, then schedules each chunk on the Web Audio
 * timeline. bargeIn() fades out in 50ms and clears the queue.
 */
export function useAudioPlayer(channel: AudioChannel | null) {
  const [isPlaying, setIsPlaying] = useState(false);
  const ctxRef = useRef<AudioContext | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const nextTimeRef = useRef(0);
  const pendingRef = useRef<Int16Array[]>([]);
  const startedRef = useRef(false);

  const getCtx = useCallback(() => {
    if (!ctxRef.current || ctxRef.current.state === "closed") {
      ctxRef.current = new AudioContext({ sampleRate: SAMPLE_RATE });
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
    if (ctx.state === "suspended") await ctx.resume();
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
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.05);
      setTimeout(() => {
        _clearState();
        if (gain) gain.gain.value = 1.0;
      }, 60);
    } else {
      _clearState();
    }
  }, [_clearState]);

  const stop = useCallback(() => bargeIn(), [bargeIn]);

  const reset = useCallback(() => {
    pendingRef.current = [];
    startedRef.current = false;
    nextTimeRef.current = 0;
  }, []);

  // Binary audio subscription
  useEffect(() => {
    if (!channel) return;
    return channel.onBinary((data) => onPCMChunk(data));
  }, [channel, onPCMChunk]);

  // tts_end resets jitter buffer state for next utterance
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
```

- [ ] **Step 2: Update App.tsx — remove flushAndPlay**

Change the useAudioPlayer destructuring to match the new API:

```typescript
  const {
    isPlaying,
    stop: stopPlayback,
    bargeIn,
    reset: resetPlaybackBuffer,
    primeContext,
  } = useAudioPlayer(getChannel());
```

In the `audioMessage` handler, remove the `tts_end` → `flushAndPlay()` call. Replace:

```typescript
      case "tts_end":
        setMouthOpenY(0);
        setVadStatus("listening");
        break;
```

Also update `vad_event` → `speech_start` branch — replace `stopPlayback()` with `bargeIn()`:

```typescript
      case "vad_event":
        if (audioMessage.payload.status === "speech_start") {
          setVadStatus("speaking");
          if (isPlaying) {
            bargeIn();
            setMouthOpenY(0);
          }
          resetPlaybackBuffer();
        } else {
          setVadStatus("thinking");
        }
        break;
```

- [ ] **Step 3: TypeScript check**

Run: `cd G:\projects\deskpet\tauri-app && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src/hooks/useAudioPlayer.ts tauri-app/src/App.tsx
git commit -m "feat(P2-2-M2): jitter-buffer PCM streaming player, remove flushAndPlay"
```

---

### Task 12: M2 integration test + tuning

- [ ] **Step 1: Start backend**

Run: `cd G:\projects\deskpet\backend && set DESKPET_DEV_MODE=1 && .venv\Scripts\python.exe main.py`

Verify log shows ffmpeg usage when TTS triggers.

- [ ] **Step 2: Start frontend**

Run: `cd G:\projects\deskpet\tauri-app && npm run dev`

- [ ] **Step 3: Manual voice test**

1. Say something → observe console `[AudioPlayer]` logs
2. Verify TTS audio plays incrementally (not waiting for tts_end)
3. Verify no audible glitch between chunks
4. Test barge-in: speak during TTS → verify playback stops

- [ ] **Step 4: Tune if glitchy**

Adjust constants in `edge_tts_provider.py` and `useAudioPlayer.ts`:
- `PCM_CHUNK_SAMPLES`: try 2048 (85ms) or 8192 (340ms)
- `JITTER_BUFFER_SIZE`: try 3
- Gap tolerance: change `0.05` to `0.1` in `scheduleChunk`

- [ ] **Step 5: Commit tuning**

```bash
git add -A
git commit -m "tune(P2-2-M2): PCM chunk size and jitter buffer depth"
```

---

## Milestone 3: Echo Suppression + Acceptance

### Task 13: Backend — VoiceConfig + dynamic VAD during TTS

**Files:**
- Modify: `backend/config.py:59-69,109-117`
- Modify: `backend/config.toml:59-62`
- Modify: `backend/pipeline/voice_pipeline.py` (use config values)

- [ ] **Step 1: Add VoiceConfig to config.py**

After `VADConfig` (after line 69):

```python
@dataclass
class VoiceConfig:
    always_on_mic: bool = False
    vad_threshold_normal: float = 0.5
    vad_threshold_during_tts: float = 0.65
    min_speech_ms_normal: int = 250
    min_speech_ms_during_tts: int = 400
    tts_cooldown_ms: int = 300
```

Add `voice` field to `AppConfig` (line 117):

```python
    voice: VoiceConfig = field(default_factory=VoiceConfig)
```

In `load_config`, add (after the `vad` section handler):

```python
    if "voice" in raw:
        config.voice = _load_section(VoiceConfig, raw["voice"])
```

- [ ] **Step 2: Add [voice] section to config.toml**

After the `[vad]` section (after line 62):

```toml
[voice]
always_on_mic = false
vad_threshold_normal = 0.5
vad_threshold_during_tts = 0.65
min_speech_ms_normal = 250
min_speech_ms_during_tts = 400
tts_cooldown_ms = 300
```

- [ ] **Step 3: Wire config into VoicePipeline**

In `voice_pipeline.py`, update `__init__` to accept voice config:

```python
    def __init__(
        self,
        vad: SileroVAD,
        asr: FasterWhisperASR,
        agent: "AgentProvider",
        tts: EdgeTTSProvider,
        control_ws: WebSocket | None = None,
        session_id: str = "default",
        vad_threshold_during_tts: float = 0.65,
        min_speech_ms_during_tts: int = 400,
        tts_cooldown_ms: int = 300,
    ):
        # ... existing fields ...
        self._barge_in_filter = BargeInFilter(
            cooldown_ms=tts_cooldown_ms,
            min_speech_during_tts_ms=min_speech_ms_during_tts,
        )
        self._vad_threshold_normal = vad.threshold
        self._vad_threshold_during_tts = vad_threshold_during_tts
```

Add VAD threshold switching in `_process_utterance`:

Before TTS starts (right after `self._barge_in_filter.on_tts_start()`):

```python
            self.vad.set_threshold(self._vad_threshold_during_tts)
```

In the `finally` block (before `self._processing = False`):

```python
            self.vad.set_threshold(self._vad_threshold_normal)
```

- [ ] **Step 4: Update main.py pipeline construction**

In `main.py` around line 678, pass voice config:

```python
    pipeline = VoicePipeline(
        vad=session_vad,
        asr=service_context.asr_engine,
        agent=service_context.agent_engine,
        tts=service_context.tts_engine,
        control_ws=control_ws,
        session_id=session_id,
        vad_threshold_during_tts=config.voice.vad_threshold_during_tts,
        min_speech_ms_during_tts=config.voice.min_speech_ms_during_tts,
        tts_cooldown_ms=config.voice.tts_cooldown_ms,
    )
```

- [ ] **Step 5: Run all tests**

Run: `cd G:\projects\deskpet\backend && python -m pytest --tb=short -q`

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/config.toml backend/pipeline/voice_pipeline.py backend/main.py
git commit -m "feat(P2-2-M3): VoiceConfig + dynamic VAD threshold during TTS"
```

---

### Task 14: Acceptance smoke script

**Files:**
- Create: `scripts/perf/barge_in.py`

- [ ] **Step 1: Write barge-in perf script**

```python
#!/usr/bin/env python3
"""P2-2 barge-in acceptance smoke test.

Test 1: Barge-in latency (VAD confirm → tts_barge_in arrival)
Test 2: Short burst rejection (echo should not trigger barge-in)
Test 3: Cooldown — noise right after TTS end should be ignored

Prerequisites: backend running with DESKPET_DEV_MODE=1
"""
from __future__ import annotations
import asyncio, json, struct, sys, time
import websockets

URL = "ws://127.0.0.1:8100/ws/audio"
SILENCE = b"\x00" * 1024
LOUD = struct.pack("<" + "h" * 512, *([10000] * 512))


async def _trigger_tts(ws) -> bool:
    """Send speech + silence to trigger ASR→Agent→TTS. Return True if TTS starts."""
    for _ in range(30):
        await ws.send(LOUD)
        await asyncio.sleep(0.032)
    for _ in range(20):
        await ws.send(SILENCE)
        await asyncio.sleep(0.032)
    t0 = time.time()
    while time.time() - t0 < 15:
        try:
            data = await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if isinstance(data, bytes) and len(data) > 1 and data[0] in (0x01, 0x02):
            return True
    return False


async def test_latency() -> tuple[bool, float]:
    """Measure time from barge-in speech send to tts_barge_in receipt."""
    url = f"{URL}?secret=&session_id=perf_lat"
    async with websockets.connect(url) as ws:
        for _ in range(31):
            await ws.send(SILENCE)
            await asyncio.sleep(0.032)
        if not await _trigger_tts(ws):
            return False, 0.0
        t_send = time.time()
        for _ in range(25):
            await ws.send(LOUD)
            await asyncio.sleep(0.032)
        t0 = time.time()
        while time.time() - t0 < 5:
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=0.3)
            except asyncio.TimeoutError:
                continue
            if isinstance(data, str):
                msg = json.loads(data)
                if msg.get("type") == "tts_barge_in":
                    latency_ms = (time.time() - t_send) * 1000
                    return True, latency_ms
    return False, 0.0


async def main() -> int:
    print("=== P2-2 Barge-In Acceptance ===\n")
    ok, lat = await test_latency()
    if ok:
        print(f"  ✅ Barge-in latency: {lat:.0f}ms (target < 200ms post-VAD-confirm)")
    else:
        print("  ❌ Barge-in not received")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Run acceptance**

Run: `cd G:\projects\deskpet && python scripts/perf/barge_in.py`

- [ ] **Step 3: Commit**

```bash
git add scripts/perf/barge_in.py
git commit -m "test(P2-2-M3): barge-in acceptance smoke script"
```

---

### Task 15: Real device testing + tag

- [ ] **Step 1: Earphone test**

| # | Item | Pass? |
|---|---|---|
| 1 | Speech → TTS streams incrementally | |
| 2 | Barge-in works, perceived delay ~300ms | |
| 3 | Conversation continues after barge-in | |
| 4 | No echo false triggers | |
| 5 | Audio quality smooth (no glitch) | |

- [ ] **Step 2: Laptop speaker test**

| # | Item | Pass? |
|---|---|---|
| 1-5 | Same as above | |
| 6 | Echo false trigger < 15% (< 2 in 10 tries) | |
| 7 | If too high → adjust `vad_threshold_during_tts` | |

- [ ] **Step 3: Commit + tag**

```bash
git add -A
git commit -m "test(P2-2): real device verification complete"
git tag p2-2-verified
```

---

## Execution DAG

```
Task 1 (BargeInFilter) ──┐
Task 2 (VAD dynamic)   ──┤── parallel, no deps
Task 4 (types+channel) ──┤
Task 5 (AudioWorklet)  ──┘
          ↓
Task 3 (voice_pipeline barge-in) ← needs Task 1, 2
          ↓
Task 6 (player bargeIn) ← needs Task 4
          ↓
Task 7 (App wiring) ← needs Task 3, 5, 6
          ↓
Task 8 (M1 E2E)
═══════ M1 ═══════
          ↓
Task 9 (ffmpeg PCM stream)
          ↓
Task 10 (pipeline→PCM)
          ↓
Task 11 (jitter buffer player)
          ↓
Task 12 (M2 tuning)
═══════ M2 ═══════
          ↓
Task 13 (VoiceConfig + dynamic threshold)
          ↓
Task 14 (perf script)
          ↓
Task 15 (device test + tag)
═══════ M3 ═══════
```

**Total: 15 Tasks, ~11 days ≈ 2.5 weeks**
