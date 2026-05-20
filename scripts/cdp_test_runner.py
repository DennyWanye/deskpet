"""CDP-based testcase runner — drives deskpet WebView2 via Chrome DevTools
Protocol on localhost:9222. Substitute for the disconnected computer-use MCP.

Usage:
  python cdp_test_runner.py <TC-id>
  python cdp_test_runner.py list

Run via backend .venv Python so the `websockets` dep is available.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import urllib.request
from pathlib import Path

# Force UTF-8 stdout — Windows GBK default chokes on ▶ / ◀ / 思考中 etc.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import websockets

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "evidence"
EVIDENCE.mkdir(exist_ok=True)


def list_targets() -> list[dict]:
    with urllib.request.urlopen("http://localhost:9222/json/list", timeout=2) as r:
        return json.loads(r.read())


def find_targets() -> dict:
    t = list_targets()
    return {
        "pet": next((x for x in t if x["url"].rstrip("/").endswith(":5173") or x["url"].endswith(":5173/")), None),
        "msg": next((x for x in t if "#/message-panel" in x["url"]), None),
        "code": next((x for x in t if "#/code-panel" in x["url"]), None),
        "all": t,
    }


class Cdp:
    """One CDP session per WebView target."""

    def __init__(self, ws_url: str, label: str):
        self.ws_url = ws_url
        self.label = label
        self._id = 0
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def __aenter__(self) -> "Cdp":
        self._ws = await websockets.connect(self.ws_url, max_size=20 * 1024 * 1024)
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        return self

    async def __aexit__(self, *_):
        if self._ws:
            await self._ws.close()

    async def send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        my_id = self._id
        payload = {"id": my_id, "method": method, "params": params or {}}
        await self._ws.send(json.dumps(payload))
        # Wait for the matching response, ignoring event frames in the meantime
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise RuntimeError(
                        f"[{self.label}] {method} -> {msg['error']}"
                    )
                return msg.get("result", {})

    async def evaluate(self, expression: str, *, await_promise: bool = False):
        r = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        )
        if r.get("exceptionDetails"):
            ex = r["exceptionDetails"]
            raise RuntimeError(
                f"[{self.label}] eval failed: {ex.get('text')} :: "
                f"{ex.get('exception', {}).get('description', '')}"
            )
        return r.get("result", {}).get("value")

    async def screenshot(self, name: str) -> Path:
        r = await self.send("Page.captureScreenshot", {"format": "png"})
        path = EVIDENCE / name
        path.write_bytes(base64.b64decode(r["data"]))
        return path


# ───────────────────────── Test cases ─────────────────────────


async def tc_01_1():
    """TC-01.1 — click ▶ 消息 → leftPanelOpen flips, bottom input bar hides, tab label flips.

    Primary signal: the bottom input bar is gated by `{!leftPanelOpen && ...}`, so its
    presence/absence is the authoritative state. Tab label flip is secondary because
    it's driven by the async `message-panel-visibility` event which may race.
    """
    t = find_targets()
    assert t["pet"], "pet target not found"
    async with Cdp(t["pet"]["webSocketDebuggerUrl"], "pet") as pet:
        before = await pet.evaluate(r"""
          (() => {
            const btn = Array.from(document.querySelectorAll('button'))
              .find(b => /[▶◀]\s*消息/.test(b.textContent));
            return {
              btnFound: !!btn,
              tabLabel: btn?.textContent,
              chatInputExists: !!document.querySelector('[data-testid="chat-input"]'),
              micExists: !!document.querySelector('[data-testid="mic-button"]'),
            };
          })()
        """)
        print(f"[TC-01.1] before:", before)
        assert before["btnFound"], "'消息' button not found"
        await pet.screenshot("TC-01.1-cdp-before.png")

        await pet.evaluate(r"""
          (() => {
            const btn = Array.from(document.querySelectorAll('button'))
              .find(b => /[▶◀]\s*消息/.test(b.textContent));
            btn.click();
          })()
        """)
        # poll for state change up to 5s (in case the visibility event is slow)
        after = None
        for _ in range(20):
            await asyncio.sleep(0.25)
            after = await pet.evaluate(r"""
              (() => {
                const btn = Array.from(document.querySelectorAll('button'))
                  .find(b => /[▶◀]\s*消息/.test(b.textContent));
                return {
                  tabLabel: btn?.textContent,
                  chatInputExists: !!document.querySelector('[data-testid="chat-input"]'),
                  micExists: !!document.querySelector('[data-testid="mic-button"]'),
                };
              })()
            """)
            # leftPanelOpen took effect when chat-input disappears
            if not after["chatInputExists"]:
                break
        print(f"[TC-01.1] after :", after)
        await pet.screenshot("TC-01.1-cdp-after.png")

    # Authoritative pass condition: chat-input AND mic-button disappeared
    bottom_bar_hidden = (not after["chatInputExists"]) and (not after["micExists"])
    tab_flipped = "◀" in (after.get("tabLabel") or "")
    return {
        "passed": bottom_bar_hidden,
        "before": before,
        "after": after,
        "bottom_bar_hidden": bottom_bar_hidden,
        "tab_label_flipped": tab_flipped,
    }


async def tc_10_2():
    """TC-10.2 — pet bottom input bar HIDES when panel is open."""
    t = find_targets()
    assert t["pet"], "pet target not found"
    assert t["msg"], "panel must be open — run TC-01.1 first"
    async with Cdp(t["pet"]["webSocketDebuggerUrl"], "pet") as pet:
        state = await pet.evaluate(r"""
          (() => ({
            inputExists: !!document.querySelector('[data-testid="chat-input"]'),
            micExists  : !!document.querySelector('[data-testid="mic-button"]'),
            sendExists : !!document.querySelector('[data-testid="send-button"]'),
          }))()
        """)
        print(f"[TC-10.2] pet bottom-bar elements when panel open:", state)
        await pet.screenshot("TC-10.2-cdp-panel-open.png")
    return {
        "passed": not state["inputExists"] and not state["micExists"] and not state["sendExists"],
        **state,
    }


async def tc_04_1():
    """TC-04.1 — type into panel input + click 发送 + verify state transitions."""
    t = find_targets()
    assert t["msg"], "panel must be open — run TC-01.1 first"
    async with Cdp(t["msg"]["webSocketDebuggerUrl"], "msg") as panel:
        typed = await panel.evaluate(r"""
          (() => {
            const ta = document.querySelector('textarea');
            if (!ta) return { ok: false, reason: 'no textarea' };
            ta.focus();
            const setter = Object.getOwnPropertyDescriptor(
              window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(ta, 'CDP端到端测试_只回复两个字OK');
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            return { ok: true, valueAfter: ta.value };
          })()
        """)
        print(f"[TC-04.1] typed:", typed)
        await panel.screenshot("TC-04.1-cdp-typed.png")
        await asyncio.sleep(0.2)

        clicked = await panel.evaluate(r"""
          (() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const send = btns.find(b => b.textContent.trim() === '发送');
            if (!send) return { ok: false };
            send.click();
            return { ok: true };
          })()
        """)
        print(f"[TC-04.1] send click:", clicked)
        await asyncio.sleep(0.6)

        inflight = await panel.evaluate(r"""
          (() => ({
            hasThinking: document.body.textContent.includes('思考中'),
            hasStop    : document.body.textContent.includes('停止'),
          }))()
        """)
        print(f"[TC-04.1] inflight indicator:", inflight)
        await panel.screenshot("TC-04.1-cdp-inflight.png")

        # Wait for the LLM reply to land in the panel's default stream
        replied = False
        for i in range(30):
            await asyncio.sleep(1)
            done = await panel.evaluate(r"""
              (() => {
                const txt = document.body.textContent;
                return txt.includes('CDP端到端测试') &&
                       !txt.includes('思考中') &&
                       !txt.includes('停止');
              })()
            """)
            if done:
                replied = True
                print(f"[TC-04.1] reply landed after {i + 1}s")
                break
        await panel.screenshot("TC-04.1-cdp-final.png")

    return {
        "passed": typed["ok"] and clicked["ok"]
                  and (inflight["hasThinking"] or inflight["hasStop"])
                  and replied,
        "typed": typed,
        "send_clicked": clicked["ok"],
        "inflight_seen": inflight,
        "reply_seen": replied,
    }


async def tc_01_2():
    """TC-01.2 — click panel's ◀ to close + verify pet bottom bar returns."""
    t = find_targets()
    assert t["msg"], "panel must be open — run TC-01.1 first"
    async with Cdp(t["msg"]["webSocketDebuggerUrl"], "msg") as panel:
        await panel.evaluate(r"""
          (() => {
            const btn = Array.from(document.querySelectorAll('button'))
              .find(b => b.getAttribute('aria-label') === '收起消息面板'
                      || b.textContent.trim() === '◀');
            if (!btn) throw new Error('close button not found');
            btn.click();
          })()
        """)
        await asyncio.sleep(1.0)
    t2 = find_targets()
    async with Cdp(t2["pet"]["webSocketDebuggerUrl"], "pet") as pet:
        state = await pet.evaluate(r"""
          (() => {
            const btn = Array.from(document.querySelectorAll('button'))
              .find(b => /[▶◀]\s*消息/.test(b.textContent));
            return {
              inputExists: !!document.querySelector('[data-testid="chat-input"]'),
              micExists  : !!document.querySelector('[data-testid="mic-button"]'),
              tabLabel   : btn?.textContent,
            };
          })()
        """)
        print(f"[TC-01.2] after close:", state)
        await pet.screenshot("TC-01.2-cdp-panel-closed.png")
    return {
        "passed": state["inputExists"]
                  and state["micExists"]
                  and "▶" in (state["tabLabel"] or ""),
        **state,
    }


async def tc_11_4():
    """TC-11.4 — runtime import of auth scaffold confirms zero-regression."""
    t = find_targets()
    async with Cdp(t["pet"]["webSocketDebuggerUrl"], "pet") as pet:
        result = await pet.evaluate(
            r"""import('/src/auth/index.ts').then(m => ({
              hasGetAuthAdapter: typeof m.getAuthAdapter === 'function',
              hasManual: typeof m.ManualAuthAdapter === 'function',
              hasNull: typeof m.NullAuthAdapter === 'function',
              adapterId: m.getAuthAdapter().id,
              isAuth: m.getAuthAdapter().isAuthenticated(),
              user: m.getAuthAdapter().currentUser(),
            }))""",
            await_promise=True,
        )
        print(f"[TC-11.4] auth scaffold:", result)
    return {
        "passed": result["hasGetAuthAdapter"]
                  and result["hasManual"]
                  and result["hasNull"]
                  and result["adapterId"] == "manual"
                  and result["isAuth"] is True,
        **result,
    }


async def tc_07_2():
    """TC-07.2 — open ChangeModelModal + verify it appears."""
    t = find_targets()
    assert t["msg"], "panel must be open"
    async with Cdp(t["msg"]["webSocketDebuggerUrl"], "msg") as panel:
        before = await panel.evaluate(r"""
          (() => {
            const chip = Array.from(document.querySelectorAll('button'))
              .find(b => /模型|✎/.test(b.textContent));
            return { chipFound: !!chip, label: chip?.textContent?.trim() };
          })()
        """)
        print(f"[TC-07.2] model chip:", before)
        await panel.evaluate(r"""
          (() => {
            const chip = Array.from(document.querySelectorAll('button'))
              .find(b => /模型|✎/.test(b.textContent));
            if (chip) chip.click();
          })()
        """)
        await asyncio.sleep(0.8)
        modal = await panel.evaluate(r"""
          (() => {
            const t = document.body.textContent;
            return {
              hasModal: t.includes('选择模型') || t.includes('Change Model') || t.includes('模型与参数'),
              snippet: t.slice(0, 200),
            };
          })()
        """)
        print(f"[TC-07.2] modal opened:", modal["hasModal"])
        await panel.screenshot("TC-07.2-cdp-model-modal.png")
        # Close it
        await panel.evaluate(r"""
          (() => {
            const closeBtn = Array.from(document.querySelectorAll('button'))
              .find(b => b.textContent.trim() === '取消' || b.textContent.trim() === '×');
            if (closeBtn) closeBtn.click();
          })()
        """)
        await asyncio.sleep(0.3)
    return {"passed": before["chipFound"] and modal["hasModal"], "chip": before, "modal": modal["hasModal"]}


REGISTRY = {
    "TC-01.1": tc_01_1,
    "TC-10.2": tc_10_2,
    "TC-04.1": tc_04_1,
    "TC-07.2": tc_07_2,
    "TC-01.2": tc_01_2,
    "TC-11.4": tc_11_4,
}


async def main():
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        print("Available TCs:")
        for k in REGISTRY:
            print(f"  {k}")
        return 0
    tc = sys.argv[1]
    if tc not in REGISTRY:
        print(f"Unknown TC: {tc}", file=sys.stderr)
        return 2
    try:
        r = await REGISTRY[tc]()
        print(json.dumps({"tc": tc, **r}, ensure_ascii=False, indent=2))
        return 0 if r.get("passed") else 1
    except Exception as e:
        print(f"[{tc}] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
