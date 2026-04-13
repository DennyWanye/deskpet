"""
End-to-end test: verifies the full chat flow works.
Requires: backend running on port 8100.
Run: python tests/e2e/test_chat_flow.py
"""
import asyncio
import json
import sys
import httpx


async def test_health():
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://127.0.0.1:8100/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        print("[PASS] Health check")


async def test_chat_flow(secret: str):
    import websockets
    uri = f"ws://127.0.0.1:8100/ws/control?secret={secret}"
    async with websockets.connect(uri) as ws:
        # Test ping
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await ws.recv())
        assert pong["type"] == "pong"
        print("[PASS] Ping/pong")

        # Test chat
        await ws.send(json.dumps({"type": "chat", "payload": {"text": "Hello!"}}))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "chat_response"
        assert "text" in resp["payload"]
        print(f"[PASS] Chat response: {resp['payload']['text'][:50]}...")


async def main():
    print("=== Desktop Pet E2E Test ===")
    print("Requires: backend running on port 8100\n")

    try:
        await test_health()
    except Exception as e:
        print(f"[FAIL] Health check: {e}")
        print("Is the backend running? Start with: cd backend && .venv/Scripts/python main.py")
        sys.exit(1)

    secret = input("Enter SHARED_SECRET from backend stdout: ").strip()
    await test_chat_flow(secret)
    print("\n=== All E2E tests passed! ===")


if __name__ == "__main__":
    asyncio.run(main())
