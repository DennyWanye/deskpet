#!/usr/bin/env python3
"""P2-2-M3 barge-in acceptance smoke test.

Exercises three behaviors the pytest suite can't prove end-to-end:

  1. **Barge-in latency** (G1) — measure wall-clock ms from injecting a
     loud speech burst during TTS to receiving `tts_barge_in` JSON back.
     Target: p95 < 200ms post-VAD-confirm. The total perceived delay also
     includes min_speech_ms_during_tts (400ms default), so raw wall-clock
     here is allowed to be up to ~600ms; the acceptance bar is p95 post-VAD
     confirmation which we approximate.

  2. **Short-burst rejection** — a 100ms noise burst during TTS must NOT
     trigger `tts_barge_in` (min_speech_during_tts_ms=400 guards this).

  3. **Cooldown** — loud burst 200ms after `tts_end` must NOT trigger
     `tts_barge_in` (tts_cooldown_ms=300 guards this).

Prerequisites:
  - Backend running on 127.0.0.1:8100
  - DESKPET_DEV_MODE=1 set when backend was launched (skips auth)
  - Cloud LLM configured (or local Ollama) so the agent actually replies

Usage:
    python scripts/perf/barge_in.py
    python scripts/perf/barge_in.py --runs 5       # repeat latency test
    python scripts/perf/barge_in.py --skip-latency # only run rejection tests
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import struct
import sys
import time

import websockets

URL = "ws://127.0.0.1:8100/ws/audio?session_id=perf_bargein"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # silero-vad hard requirement
FRAME_MS = FRAME_SAMPLES / SAMPLE_RATE * 1000  # 32ms
SILENCE_FRAME = b"\x00" * FRAME_SAMPLES * 2
# ~10000 amplitude sine-ish frame — high enough that silero-vad confidently
# fires is_speech=True even with the 0.65 during-TTS threshold.
LOUD_FRAME = struct.pack("<" + "h" * FRAME_SAMPLES, *([10000] * FRAME_SAMPLES))


# --- helpers ---


async def _send_frames(ws, frame: bytes, count: int) -> None:
    """Send `count` frames of `frame` with 32ms pacing (realtime)."""
    for _ in range(count):
        await ws.send(frame)
        await asyncio.sleep(FRAME_MS / 1000)


async def _drain_until_tts_audio(ws, timeout_s: float = 15.0) -> bool:
    """Consume messages until a PCM frame arrives (type byte 0x01) or
    until tts_end. Returns True if TTS actually started.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            data = await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if isinstance(data, bytes) and len(data) > 1 and data[0] == 0x01:
            return True
    return False


async def _trigger_one_tts(ws) -> bool:
    """Speech (30 loud frames ≈960ms) + silence (20 ≈640ms) → VAD speech_end
    → ASR → agent → TTS. Wait for first PCM byte.
    """
    await _send_frames(ws, LOUD_FRAME, 30)
    await _send_frames(ws, SILENCE_FRAME, 20)
    return await _drain_until_tts_audio(ws, timeout_s=20.0)


# --- tests ---


async def test_latency() -> tuple[bool, float | None, str]:
    """Inject speech mid-TTS, measure time to tts_barge_in."""
    try:
        async with websockets.connect(URL, max_size=None) as ws:
            # Warmup: 1s of silence so VAD state settles.
            await _send_frames(ws, SILENCE_FRAME, 31)
            if not await _trigger_one_tts(ws):
                return False, None, "TTS never started — check backend + LLM"

            # TTS is playing. Inject loud speech; record send timestamp of
            # the FIRST loud frame since that's when VAD could in principle
            # detect it (first 512-sample window crosses threshold).
            t_send = time.time()
            await ws.send(LOUD_FRAME)

            # Keep pumping loud frames while waiting for tts_barge_in. The
            # filter needs min_speech_during_tts_ms=400ms of sustained
            # speech, so ~13 more frames at 32ms each.
            barge_task = asyncio.create_task(ws.recv())
            send_task = asyncio.create_task(_send_frames(ws, LOUD_FRAME, 25))

            deadline = time.time() + 5.0
            while time.time() < deadline:
                done, _ = await asyncio.wait(
                    {barge_task}, timeout=0.1, return_when=asyncio.FIRST_COMPLETED
                )
                if done:
                    msg = barge_task.result()
                    if isinstance(msg, str):
                        parsed = json.loads(msg)
                        if parsed.get("type") == "tts_barge_in":
                            latency_ms = (time.time() - t_send) * 1000
                            send_task.cancel()
                            return True, latency_ms, "ok"
                    # Not the message we want; keep waiting.
                    barge_task = asyncio.create_task(ws.recv())

            send_task.cancel()
            return False, None, "tts_barge_in never received within 5s"
    except Exception as e:
        return False, None, f"exception: {e}"


async def test_short_burst_rejection() -> tuple[bool, str]:
    """A 100ms noise burst during TTS must NOT trigger tts_barge_in."""
    try:
        async with websockets.connect(URL, max_size=None) as ws:
            await _send_frames(ws, SILENCE_FRAME, 31)
            if not await _trigger_one_tts(ws):
                return False, "TTS never started"

            # ~100ms of loud frames (3 frames × 32ms = 96ms) — shorter than
            # min_speech_during_tts_ms (400ms).
            await _send_frames(ws, LOUD_FRAME, 3)
            # Followed by silence so we don't accidentally sustain speech.
            await _send_frames(ws, SILENCE_FRAME, 30)

            # Drain for 2s; if tts_barge_in shows up, test fails.
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                if isinstance(data, str):
                    msg = json.loads(data)
                    if msg.get("type") == "tts_barge_in":
                        return False, "short burst falsely triggered barge-in"
            return True, "ok (no false trigger)"
    except Exception as e:
        return False, f"exception: {e}"


async def test_cooldown_rejection() -> tuple[bool, str]:
    """Loud burst right after tts_end must NOT trigger tts_barge_in (300ms cooldown)."""
    try:
        async with websockets.connect(URL, max_size=None) as ws:
            await _send_frames(ws, SILENCE_FRAME, 31)
            if not await _trigger_one_tts(ws):
                return False, "TTS never started"

            # Drain binary frames + wait for tts_end JSON.
            deadline = time.time() + 30.0
            tts_ended = False
            while time.time() < deadline:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if isinstance(data, str):
                    msg = json.loads(data)
                    if msg.get("type") == "tts_end":
                        tts_ended = True
                        break
            if not tts_ended:
                return False, "tts_end never received"

            # 200ms after tts_end — well inside the 300ms cooldown.
            await asyncio.sleep(0.2)
            # Fire sustained speech (would normally barge-in).
            await _send_frames(ws, LOUD_FRAME, 25)

            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                if isinstance(data, str):
                    msg = json.loads(data)
                    if msg.get("type") == "tts_barge_in":
                        return False, "cooldown window did not block barge-in"
            return True, "ok (cooldown held)"
    except Exception as e:
        return False, f"exception: {e}"


# --- driver ---


def _fmt_row(name: str, ok: bool, detail: str) -> str:
    mark = "PASS" if ok else "FAIL"
    return f"  [{mark}] {name:<28} {detail}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="latency test repetitions")
    ap.add_argument("--skip-latency", action="store_true")
    args = ap.parse_args()

    print("=" * 64)
    print("P2-2-M3 Barge-In Acceptance")
    print(f"Backend: {URL}")
    print("=" * 64)

    all_ok = True

    # Test 1: latency
    if not args.skip_latency:
        latencies: list[float] = []
        for i in range(args.runs):
            print(f"\n[latency run {i+1}/{args.runs}] triggering TTS + injecting speech...")
            ok, lat_ms, detail = await test_latency()
            if ok and lat_ms is not None:
                print(_fmt_row("barge_in_latency", True, f"{lat_ms:.0f}ms"))
                latencies.append(lat_ms)
            else:
                print(_fmt_row("barge_in_latency", False, detail))
                all_ok = False

        if latencies:
            p50 = statistics.median(latencies)
            p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
            print(
                f"\n  latency summary: n={len(latencies)}  "
                f"p50={p50:.0f}ms  p95={p95:.0f}ms  "
                f"target p95 < 600ms (raw wall-clock incl. min_speech_ms_during_tts)"
            )
            if p95 > 600:
                all_ok = False

    # Test 2: short burst rejection
    print("\n[short-burst rejection] injecting 100ms noise mid-TTS...")
    ok, detail = await test_short_burst_rejection()
    print(_fmt_row("short_burst_rejection", ok, detail))
    if not ok:
        all_ok = False

    # Test 3: cooldown rejection
    print("\n[cooldown rejection] loud burst 200ms after tts_end...")
    ok, detail = await test_cooldown_rejection()
    print(_fmt_row("cooldown_rejection", ok, detail))
    if not ok:
        all_ok = False

    print("\n" + "=" * 64)
    print("RESULT:", "PASS" if all_ok else "FAIL")
    print("=" * 64)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
