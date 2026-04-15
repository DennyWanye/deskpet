"""P2-1 finale Phase 2 smoke — verifies BillingLedger actually accrues spend
after a local chat call, and that budget_status reflects the debit.
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets

WS_URL = "ws://127.0.0.1:8100/ws/control?secret=dev&session_id=e2e2"


async def ws_roundtrip(request: dict, expect_type: str, timeout: float = 60.0) -> dict:
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps(request))
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no {expect_type} in {timeout}s")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("type") == expect_type:
                return msg


async def main() -> int:
    # 1. baseline
    b0 = (await ws_roundtrip({"type": "budget_status"}, "budget_status"))["payload"]
    spent0 = b0["spent_today_cny"]

    # 2. send a chat
    reply = await ws_roundtrip(
        {"type": "chat", "payload": {"text": "数到三"}}, "chat_response"
    )
    text = reply["payload"].get("text", "")
    budget_exceeded = reply["payload"].get("budget_exceeded", False)

    # 3. read budget_status again
    b1 = (await ws_roundtrip({"type": "budget_status"}, "budget_status"))["payload"]
    spent1 = b1["spent_today_cny"]
    delta = spent1 - spent0

    print("=== P2-1 E2E Phase 2 (BillingLedger accrual) ===")
    print(f"  chat reply (first 60 chars): {text[:60]!r}")
    print(f"  budget_exceeded flag        : {budget_exceeded}")
    print(f"  spent_today_cny before      : {spent0:.6f}")
    print(f"  spent_today_cny after       : {spent1:.6f}")
    print(f"  delta                       : {delta:.6f}")
    print(f"  percent_used after          : {b1['percent_used']:.4f}%")

    # Assertion: we charged *something* for the local call (because
    # gemma4:e4b isn't in [billing.pricing] and falls to the 20 CNY/1M
    # fallback — non-zero for any non-empty reply).
    passed = bool(text) and not budget_exceeded and delta > 0
    print(f"\n{'PASS' if passed else 'FAIL'}: local chat accrues spend in BillingLedger")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
