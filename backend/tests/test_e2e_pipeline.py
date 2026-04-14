"""End-to-end test: simulate a user speaking by streaming real audio to /ws/audio."""
from __future__ import annotations

import sys
# Windows default stdout is GBK, chokes on emoji in LLM replies
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

import asyncio
import io
import json
import wave
from pathlib import Path

import edge_tts
import numpy as np
import websockets


BACKEND_URL = "ws://127.0.0.1:8100/ws/audio?secret=&session_id=e2e_test"
FRAME_SAMPLES = 512  # silero-vad v5 requires exactly 512 samples @ 16kHz
FRAME_BYTES = FRAME_SAMPLES * 2  # int16


async def generate_test_audio() -> bytes:
    """Generate Chinese TTS via edge-tts, return 16kHz int16 PCM bytes."""
    text = "你好，请介绍一下你自己"
    print(f"[gen] synthesizing: {text!r}")

    communicate = edge_tts.Communicate(text, "zh-CN-XiaoyiNeural")
    mp3_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_data.extend(chunk["data"])
    print(f"[gen] got {len(mp3_data)} bytes MP3")

    # Decode MP3 → PCM via soundfile (libsndfile)
    import soundfile as sf
    audio_np, sr = sf.read(io.BytesIO(bytes(mp3_data)), dtype="float32")
    if audio_np.ndim > 1:
        audio_np = audio_np.mean(axis=1)  # mix to mono
    print(f"[gen] decoded: samples={len(audio_np)}, sr={sr}")

    # Resample to 16kHz using torchaudio
    if sr != 16000:
        import torch
        import torchaudio.functional as F
        t = torch.from_numpy(audio_np).unsqueeze(0)
        t = F.resample(t, sr, 16000)
        audio_np = t.squeeze().numpy()

    audio_int16 = (audio_np * 32767).astype(np.int16)

    # Add 1 second of silence at the end so VAD fires speech_end
    silence = np.zeros(16000, dtype=np.int16)
    audio_int16 = np.concatenate([audio_int16, silence])

    pcm_bytes = audio_int16.tobytes()
    print(f"[gen] final PCM: {len(pcm_bytes)} bytes ({len(pcm_bytes) / 2 / 16000:.2f}s)")
    return pcm_bytes


async def stream_to_backend(pcm_bytes: bytes) -> None:
    """Connect to backend, stream PCM as 512-sample chunks, collect responses."""
    print(f"[ws] connecting to {BACKEND_URL}")
    async with websockets.connect(BACKEND_URL, max_size=None) as ws:
        print("[ws] connected")

        # Start a task to read incoming messages
        responses: list = []
        audio_out = bytearray()
        done = asyncio.Event()

        async def reader():
            try:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        audio_out.extend(msg)
                        print(f"[<-] binary {len(msg)} bytes (total TTS: {len(audio_out)})")
                    else:
                        data = json.loads(msg)
                        responses.append(data)
                        mtype = data.get("type")
                        payload = data.get("payload", {})
                        try:
                            print(f"[<-] {mtype}: {payload}")
                        except UnicodeEncodeError:
                            print(f"[<-] {mtype}: <payload contains non-printable chars, len={len(str(payload))}>")
                        if mtype == "tts_end":
                            done.set()
            except Exception as e:
                print(f"[<-] reader ended: {e}")
                done.set()

        reader_task = asyncio.create_task(reader())

        # Stream PCM in 32ms chunks, real-time pacing
        print(f"[->] streaming {len(pcm_bytes)} bytes in {FRAME_BYTES}-byte chunks...")
        for offset in range(0, len(pcm_bytes), FRAME_BYTES):
            chunk = pcm_bytes[offset : offset + FRAME_BYTES]
            if len(chunk) < FRAME_BYTES:
                chunk = chunk + b"\x00" * (FRAME_BYTES - len(chunk))
            await ws.send(chunk)
            await asyncio.sleep(0.032)  # 32ms real-time pacing

        print("[->] streaming complete, waiting for TTS end (max 30s)...")
        try:
            await asyncio.wait_for(done.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            print("[!] timeout waiting for tts_end")

        reader_task.cancel()

        # Summary
        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        vad_events = [r for r in responses if r.get("type") == "vad_event"]
        transcripts = [r for r in responses if r.get("type") == "transcript"]
        errors = [r for r in responses if r.get("type") == "error"]
        print(f"VAD events:    {len(vad_events)}")
        for r in vad_events:
            print(f"  - {r['payload']}")
        print(f"Transcripts:   {len(transcripts)}")
        for r in transcripts:
            p = r["payload"]
            try:
                print(f"  - [{p['role']}] {p['text']!r}")
            except UnicodeEncodeError:
                print(f"  - [{p['role']}] <{len(p['text'])} chars, contains emoji>")
        print(f"TTS audio out: {len(audio_out)} bytes")
        print(f"Errors:        {len(errors)}")
        for r in errors:
            print(f"  - {r['payload']}")

        # Save TTS output for manual inspection
        if audio_out:
            out_path = Path("./temp/e2e_tts_output.mp3")
            out_path.parent.mkdir(exist_ok=True)
            out_path.write_bytes(bytes(audio_out))
            print(f"TTS saved:     {out_path}")


async def main():
    pcm = await generate_test_audio()
    await stream_to_backend(pcm)


if __name__ == "__main__":
    asyncio.run(main())
