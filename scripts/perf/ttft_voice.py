"""Measure voice-pipeline TTFT (V5 §1.1 target: < 2.5s).

Definition of TTFT here: time from the last PCM chunk sent (i.e., the
moment the user would stop speaking) to the first TTS audio byte received
back. That's the latency the human perceives between "I finished talking"
and "the pet starts replying aloud."

Requires a running backend on 127.0.0.1:8100 with SHARED_SECRET passed
via --secret. Uses edge-tts to synthesize a test utterance and streams
it through /ws/audio, so this probe works without a local microphone but
does need outbound network for edge-tts the first time.

Usage:
    python scripts/perf/ttft_voice.py --secret $SECRET --runs 5
"""
from __future__ import annotations

import argparse
import asyncio
import io
import statistics
import sys
import time


FRAME_SAMPLES = 512  # silero-vad v5 hard requirement
FRAME_BYTES = FRAME_SAMPLES * 2  # int16 PCM


async def _generate_pcm(text: str) -> bytes:
    """edge-tts → mono 16kHz int16 PCM."""
    import edge_tts
    import numpy as np
    import soundfile as sf

    communicate = edge_tts.Communicate(text, "zh-CN-XiaoyiNeural")
    mp3 = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3.extend(chunk["data"])
    audio, sr = sf.read(io.BytesIO(bytes(mp3)), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        import torch
        import torchaudio.functional as F
        t = torch.from_numpy(audio).unsqueeze(0)
        t = F.resample(t, sr, 16000)
        audio = t.squeeze().numpy()
    pcm = (audio * 32767).astype(np.int16).tobytes()
    # Trailing silence so VAD fires speech_end.
    pcm += (np.zeros(16000, dtype=np.int16)).tobytes()
    return pcm


async def _measure_one(secret: str, pcm: bytes, session: str) -> float | None:
    """Return TTFT seconds, or None if the backend didn't send audio."""
    import websockets

    url = (
        f"ws://127.0.0.1:8100/ws/audio"
        f"?secret={secret}&session_id={session}"
    )
    last_frame_sent_at: float | None = None
    first_audio_at: float | None = None

    async with websockets.connect(url, max_size=None) as ws:
        async def reader():
            nonlocal first_audio_at
            async for msg in ws:
                if isinstance(msg, bytes):
                    first_audio_at = time.perf_counter()
                    return

        reader_task = asyncio.create_task(reader())

        for off in range(0, len(pcm), FRAME_BYTES):
            chunk = pcm[off : off + FRAME_BYTES]
            if len(chunk) < FRAME_BYTES:
                chunk = chunk + b"\x00" * (FRAME_BYTES - len(chunk))
            await ws.send(chunk)
            last_frame_sent_at = time.perf_counter()
            await asyncio.sleep(0.032)

        try:
            await asyncio.wait_for(reader_task, timeout=30.0)
        except asyncio.TimeoutError:
            reader_task.cancel()
            return None

    if last_frame_sent_at is None or first_audio_at is None:
        return None
    return first_audio_at - last_frame_sent_at


async def _main_async(args: argparse.Namespace) -> int:
    print(f"[ttft] generating test audio (edge-tts: {args.text!r})")
    pcm = await _generate_pcm(args.text)
    print(f"[ttft] pcm bytes: {len(pcm)}  duration: {len(pcm)/2/16000:.2f}s")

    results: list[float] = []
    for i in range(args.runs):
        sess = f"ttft_{i}"
        print(f"[ttft] run {i + 1}/{args.runs} (session={sess})")
        try:
            ttft = await _measure_one(args.secret, pcm, sess)
        except Exception as e:
            print(f"[ttft] run {i + 1} failed: {e}")
            continue
        if ttft is None:
            print(f"[ttft] run {i + 1}: no audio received")
            continue
        print(f"[ttft] run {i + 1}: {ttft * 1000:.0f} ms")
        results.append(ttft)
        await asyncio.sleep(1.0)  # cool-down

    if not results:
        print("[ttft] no successful runs — backend unreachable or silent.")
        return 2

    p50 = statistics.median(results) * 1000
    p95 = statistics.quantiles(results, n=20)[18] * 1000 if len(results) >= 2 else p50
    print("[ttft] summary:")
    print(f"  runs:    {len(results)}/{args.runs}")
    print(f"  p50:     {p50:.0f} ms")
    print(f"  p95:     {p95:.0f} ms")
    print(f"  min:     {min(results) * 1000:.0f} ms")
    print(f"  max:     {max(results) * 1000:.0f} ms")
    gate = 2500.0
    status = "PASS" if p95 < gate else "FAIL"
    print(f"  V5 gate (p95 < {gate:.0f} ms): {status}")
    return 0 if status == "PASS" else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--secret", required=True, help="SHARED_SECRET printed by main.py")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument(
        "--text",
        default="你好，请简短介绍一下你自己",
        help="utterance to speak via edge-tts",
    )
    args = p.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
