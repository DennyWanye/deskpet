"""P2-1-S6 cloud TTFT smoke — run N rounds of force_cloud chat, print p50/p95.

Usage:
    DESKPET_DEV_MODE=1 uv run python main.py &
    uv run python scripts/perf/ttft_cloud.py --rounds 10

Requires cloud provider configured (``config.toml [llm.cloud]`` uncommented +
``DESKPET_CLOUD_API_KEY`` env). Exits with code 1 if no sample succeeds
(cloud unreachable or misconfigured) — the caller can treat that as
"skip" rather than "fail" in CI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import websockets


async def one_round(url: str, prompt: str) -> float:
    """Return elapsed seconds from WS send -> first ``chat_response`` frame.

    NOTE: This is a TTFT *proxy* — the control WS currently buffers the
    full LLM reply before emitting ``chat_response``, so this measures
    full-reply latency. True per-token TTFT requires instrumenting the
    audio channel; that's deferred to a later slice. The /metrics
    Histogram exposed by the backend *is* true TTFT (first token),
    collected server-side — use this script to dot-check that the
    histogram has non-empty cloud samples.
    """
    async with websockets.connect(url) as ws:
        t0 = time.perf_counter()
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"text": prompt, "force_cloud": True},
        }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
            msg = json.loads(raw)
            if msg.get("type") == "chat_response":
                return time.perf_counter() - t0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--secret", default="",
                        help="shared secret; empty if backend in DEV_MODE")
    args = parser.parse_args()

    qs = f"?secret={args.secret}&session_id=ttft-smoke"
    url = f"ws://127.0.0.1:{args.port}/ws/control{qs}"
    samples: list[float] = []
    for i in range(args.rounds):
        try:
            dt = await one_round(url, f"用一句话介绍中国第{i + 1}大城市")
            samples.append(dt)
            print(f"  round {i + 1}: {dt * 1000:.0f} ms")
        except Exception as e:
            print(f"  round {i + 1}: FAILED {type(e).__name__} {e}")

    if not samples:
        print("[ttft_cloud] no successful samples; cloud probably unconfigured")
        return 1

    n = len(samples)
    p50 = statistics.median(samples)
    # quantiles(n=20) gives every 5th percentile; index 18 is p95.
    if n >= 2:
        qs_ = statistics.quantiles(samples, n=20)
        p95 = qs_[18]
    else:
        p95 = samples[0]
    print(
        f"\n[ttft_cloud] n={n} "
        f"p50={p50 * 1000:.0f}ms "
        f"p95={p95 * 1000:.0f}ms "
        f"max={max(samples) * 1000:.0f}ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
