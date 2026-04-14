"""Short-horizon stability smoke — surrogate for the 8h V5 §1.1 gate.

Hammer the control WS with text-chat requests at a configurable rate for
N seconds/minutes, count errors, and report the error-rate distribution.
Pair with `vram_sampler.py` running in parallel to catch memory leaks.

For the full 8h run, just pass --duration 28800. For CI / quick smoke
use --duration 60.

Usage:
    python scripts/perf/stability_smoke.py --secret $SECRET --duration 60 --qps 2
    # With VRAM sampler in parallel:
    python scripts/perf/vram_sampler.py --duration 60 &
    python scripts/perf/stability_smoke.py --secret $SECRET --duration 60
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time


PROMPTS = [
    "你好",
    "今天天气怎么样？",
    "给我讲个笑话",
    "你叫什么名字",
    "1+1=?",
    "介绍一下上海",
    "写一首短诗",
    "say hi in english",
]


async def _one_turn(secret: str, prompt: str) -> tuple[bool, float]:
    """Return (success, elapsed_seconds)."""
    import websockets

    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}"
    start = time.perf_counter()
    try:
        async with websockets.connect(url) as ws:
            await ws.send(
                '{"type":"chat","payload":{"text":' + _json_str(prompt) + "}}"
            )
            # Wait up to 30s for a reply.
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
            except asyncio.TimeoutError:
                return (False, time.perf_counter() - start)
            import json

            data = json.loads(msg)
            ok = data.get("type") == "chat_response"
            return (ok, time.perf_counter() - start)
    except Exception:
        return (False, time.perf_counter() - start)


def _json_str(s: str) -> str:
    import json as _json

    return _json.dumps(s, ensure_ascii=False)


async def _main_async(args: argparse.Namespace) -> int:
    deadline = time.time() + args.duration
    interval = 1.0 / max(args.qps, 0.01)
    latencies: list[float] = []
    errors: int = 0
    total: int = 0
    i = 0

    print(
        f"[stability] running {args.duration}s at {args.qps} qps "
        f"→ ~{int(args.duration * args.qps)} turns"
    )

    while time.time() < deadline:
        prompt = PROMPTS[i % len(PROMPTS)]
        i += 1
        total += 1
        ok, elapsed = await _one_turn(args.secret, prompt)
        if ok:
            latencies.append(elapsed)
        else:
            errors += 1
            print(f"[stability] turn {i} FAILED ({elapsed * 1000:.0f}ms)")

        # Pace so we don't overload the backend.
        await asyncio.sleep(max(0.0, interval - elapsed))

    if total == 0:
        print("[stability] no turns executed")
        return 2

    err_rate = errors / total
    print("[stability] summary:")
    print(f"  total turns: {total}")
    print(f"  failures:    {errors}  ({err_rate * 100:.1f}%)")
    if latencies:
        p50 = statistics.median(latencies) * 1000
        p95 = (
            statistics.quantiles(latencies, n=20)[18] * 1000
            if len(latencies) >= 20
            else max(latencies) * 1000
        )
        print(f"  p50 latency: {p50:.0f} ms")
        print(f"  p95 latency: {p95:.0f} ms")
    gate = 0.01  # <1% error rate
    status = "PASS" if err_rate <= gate else "FAIL"
    print(f"  V5 gate (err <= {gate * 100:.0f}%): {status}")
    return 0 if status == "PASS" else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--secret", required=True)
    p.add_argument("--duration", type=int, default=60, help="total seconds")
    p.add_argument("--qps", type=float, default=2.0, help="turns/sec target")
    args = p.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
