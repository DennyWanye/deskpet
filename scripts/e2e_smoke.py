"""P2-1 finale manual E2E smoke — exercises S3/S6/S7/S8 end-to-end against
a running backend (assumes DESKPET_DEV_MODE=1 so we don't need the secret).

Checks:
  1. /health
  2. /metrics (DEV_MODE bypass + ensure llm_ttft_seconds metric family exists)
  3. WS /ws/control budget_status → DailyBudgetStatus payload
  4. WS /ws/control provider_test_connection with garbage key → ok:false
  5. WS /ws/control chat → chat_response via local Ollama
  6. /metrics after chat → llm_ttft_seconds bucket count > 0
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

import websockets

BASE_URL = "http://127.0.0.1:8100"
WS_URL = "ws://127.0.0.1:8100/ws/control?secret=dev&session_id=e2e"
METRICS_URL = f"{BASE_URL}/metrics"


def _http_get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


async def ws_roundtrip(request: dict, expect_type: str, timeout: float = 20.0) -> dict:
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
    results: list[tuple[str, bool, str]] = []

    # --- 1. /health
    status, body = _http_get(f"{BASE_URL}/health")
    ok = status == 200 and '"status":"ok"' in body
    results.append(("1. /health", ok, body[:120]))

    # --- 2. /metrics (DEV_MODE bypass)
    status, body = _http_get(METRICS_URL)
    ok = status == 200 and "llm_ttft_seconds" in body
    metric_lines_before = sum(
        1 for ln in body.splitlines()
        if ln.startswith("llm_ttft_seconds_count")
    )
    results.append((
        "2. /metrics (DEV bypass + histogram registered)", ok,
        f"{metric_lines_before} llm_ttft_seconds_count lines",
    ))

    # --- 3. budget_status
    try:
        reply = await ws_roundtrip({"type": "budget_status"}, "budget_status", 5.0)
        p = reply["payload"]
        ok = all(k in p for k in ("spent_today_cny", "daily_budget_cny",
                                  "remaining_cny", "percent_used"))
        results.append(("3. WS budget_status", ok, json.dumps(p, ensure_ascii=False)))
    except Exception as e:
        results.append(("3. WS budget_status", False, f"EXC: {e}"))

    # --- 4. provider_test_connection (garbage key → ok:false)
    try:
        reply = await ws_roundtrip(
            {
                "type": "provider_test_connection",
                "payload": {
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "sk-invalid-e2e",
                    "model": "qwen3.6-plus",
                },
            },
            "provider_test_connection_result",
            20.0,
        )
        p = reply["payload"]
        ok = p.get("ok") is False and "error" in p
        results.append(("4. WS provider_test_connection (bad key → ok:false)", ok,
                        json.dumps(p, ensure_ascii=False)[:160]))
    except Exception as e:
        results.append(("4. WS provider_test_connection", False, f"EXC: {e}"))

    # --- 5. chat → chat_response (uses local Ollama)
    try:
        reply = await ws_roundtrip(
            {"type": "chat", "payload": {"text": "说一句话"}},
            "chat_response",
            60.0,
        )
        text = reply["payload"].get("text", "")
        ok = bool(text) and not reply["payload"].get("budget_exceeded")
        results.append(("5. WS chat → chat_response (local Ollama)", ok,
                        f"text[:40]={text[:40]!r}"))
    except Exception as e:
        results.append(("5. WS chat", False, f"EXC: {e}"))

    # --- 6. /metrics after chat → ttft observed
    status, body = _http_get(METRICS_URL)
    after_count = 0
    for ln in body.splitlines():
        if ln.startswith('llm_ttft_seconds_count{') and 'provider="local"' in ln:
            # parse trailing number
            try:
                after_count = int(float(ln.rsplit(" ", 1)[1]))
            except ValueError:
                pass
            break
    ok = after_count > 0
    results.append(("6. TTFT observed (llm_ttft_seconds_count provider=local)", ok,
                    f"count={after_count}"))

    # --- summary
    width = max(len(name) for name, _, _ in results)
    print("\n=== P2-1 E2E Smoke Results ===")
    for name, passed, detail in results:
        mark = "OK  " if passed else "FAIL"
        print(f"  [{mark}] {name.ljust(width)}  | {detail}")

    failed = [n for n, p, _ in results if not p]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
