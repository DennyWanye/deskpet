"""P2-1-S1 application-level smoke test.

Connects to running backend at ws://127.0.0.1:8100/ws/control as a synthetic
session, sends a single `chat` message, waits for `chat_response`, and prints
the round-trip latency + response text.

End-to-end path exercised:
    ws/control handler (main.py:307)
      -> service_context.agent_engine (SimpleLLMAgent)
        -> OpenAICompatibleProvider.chat_stream
          -> POST http://localhost:11434/v1/chat/completions
            -> Ollama gemma4:e4b
"""

import asyncio
import json
import sys
import time

import websockets


async def main() -> int:
    url = "ws://127.0.0.1:8100/ws/control?secret=&session_id=smoke-test"
    prompt = "用一句话介绍一下你自己。"

    print(f"[smoke] connecting: {url}")
    async with websockets.connect(url) as ws:
        # Expect nothing on connect (server doesn't send hello).
        payload = {"type": "chat", "payload": {"text": prompt}}
        print(f"[smoke] sending chat: {prompt!r}")
        t0 = time.perf_counter()
        await ws.send(json.dumps(payload))

        # Wait for chat_response, ignore ping/pong etc.
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
            msg = json.loads(raw)
            if msg.get("type") == "chat_response":
                dt = time.perf_counter() - t0
                text = msg.get("payload", {}).get("text", "")
                print(f"[smoke] got chat_response in {dt:.2f}s")
                print(f"[smoke] text ({len(text)} chars): {text!r}")
                # Verdict heuristics
                if text.startswith("[echo] "):
                    print("[smoke] VERDICT: FAIL — fell back to echo (agent_engine missing or chat_stream raised)")
                    return 2
                if not text.strip():
                    print("[smoke] VERDICT: FAIL — empty response")
                    return 3
                print("[smoke] VERDICT: PASS — real LLM reply via agent->provider->Ollama")
                return 0
            else:
                print(f"[smoke] ignoring msg: {msg}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
