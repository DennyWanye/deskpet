"""Drive the running Tauri deskpet via WebView2 DevTools Protocol.

Requires:
 - deskpet.exe running with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS containing
   --remote-debugging-port=9222 (done in src-tauri/src/main.rs debug builds)
 - Vite dev server up (`npm run dev` under tauri-app/)
 - Backend running on :8100

No screen capture, no DPR math, no pixel sniffing — all selectors go through
the real DOM via data-testid attributes.

Run: backend/.venv/Scripts/python.exe scripts/e2e/drive_via_cdp.py
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# Force UTF-8 stdout on Windows so emoji in status messages don't crash print()
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


SHOTS = Path(__file__).parent / "shots_cdp"
SHOTS.mkdir(exist_ok=True)


def cdp_endpoint() -> str:
    """Playwright needs the browser-level websocket; /json/version has it."""
    raw = urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3.0).read()
    info = json.loads(raw)
    return info["webSocketDebuggerUrl"]


def attach_page(browser) -> Page:
    """Find the DeskPet page among the WebView2 contexts."""
    for ctx in browser.contexts:
        for page in ctx.pages:
            if "tauri.localhost" in (page.url or "") or "DeskPet" in (page.title() or ""):
                return page
    # Fall back to first page
    for ctx in browser.contexts:
        if ctx.pages:
            return ctx.pages[0]
    raise SystemExit("no page found in WebView2 CDP session")


def shot(page: Page, name: str) -> Path:
    p = SHOTS / f"{name}.png"
    page.screenshot(path=str(p))
    return p


def ensure_mic_idle(page: Page) -> None:
    """If the mic button is in recording state, toggle it off. Audio channel
    saturation can delay chat replies, so tests should start clean."""
    mic = page.locator('[data-testid="mic-button"]')
    if mic.count() and mic.get_attribute("title") == "Stop recording":
        print("[setup] mic was recording — toggling off")
        mic.evaluate("el => el.click()")
        page.wait_for_timeout(800)


def step_text_chat(page: Page) -> bool:
    print("\n=== STEP: TEXT CHAT ===")
    ensure_mic_idle(page)
    shot(page, "chat_00_initial")

    page.locator('[data-testid="chat-input"]').wait_for(state="visible", timeout=10_000)
    page.locator('[data-testid="chat-input"]').fill("你好！请用一句话介绍自己。")
    shot(page, "chat_01_typed")

    # Count bubbles before send
    before = page.locator('[data-testid="chat-bubble-assistant"]').count()
    page.locator('[data-testid="send-button"]').click()
    print(f"[chat] sent; waiting for assistant bubble (had {before})...")

    # Poll up to 60s for a NEW assistant bubble with non-empty text.
    # Large memory context (50+ turns) can slow the first token significantly.
    deadline = time.time() + 60
    got_text = None
    while time.time() < deadline:
        bubbles = page.locator('[data-testid="chat-bubble-assistant"]')
        count = bubbles.count()
        if count > before:
            got_text = bubbles.nth(count - 1).inner_text().strip()
            if got_text:
                break
        page.wait_for_timeout(500)

    shot(page, "chat_02_after_reply")
    if got_text:
        # Truncate for display
        snippet = got_text[:80].replace("\n", " ")
        print(f"[chat] PASS assistant reply: {snippet}...")
        return True
    print("[chat] FAIL no reply within 60s")
    return False


def step_memory_panel(page: Page) -> bool:
    print("\n=== STEP: MEMORY PANEL ===")
    page.locator('[data-testid="memory-toggle"]').click()
    page.wait_for_timeout(800)
    shot(page, "mem_01_opened")

    # Wait for refresh button to render (= panel mounted)
    page.locator('[data-testid="memory-refresh"]').wait_for(state="visible", timeout=5_000)
    # Click refresh to guarantee fresh data
    page.locator('[data-testid="memory-refresh"]').click()
    page.wait_for_timeout(800)

    turn_rows = page.locator('[data-testid^="memory-turn-"]')
    count = turn_rows.count()
    print(f"[mem] turn rows rendered: {count}")

    # Delete first turn (if any), assert row count drops
    deleted_ok = False
    if count > 0:
        first_id = turn_rows.first.get_attribute("data-testid").split("-")[-1]
        print(f"[mem] deleting turn id={first_id}")
        page.locator(f'[data-testid="memory-delete-{first_id}"]').click()
        page.wait_for_timeout(800)
        new_count = page.locator('[data-testid^="memory-turn-"]').count()
        if new_count == count - 1:
            print(f"[mem] PASS delete worked ({count} -> {new_count})")
            deleted_ok = True
        else:
            print(f"[mem] FAIL delete did not remove row (still {new_count})")
    else:
        deleted_ok = True  # nothing to delete is fine
        print("[mem] (no turns to delete — skipping delete assertion)")

    shot(page, "mem_02_after_delete")

    # Close panel
    page.locator('[data-testid="memory-close"]').click()
    page.wait_for_timeout(500)
    shot(page, "mem_03_closed")

    # Verify panel gone
    panel_gone = page.locator('[data-testid="memory-refresh"]').count() == 0
    if not panel_gone:
        print("[mem] FAIL panel did not close")
        return False
    print("[mem] PASS panel closed")
    return deleted_ok


def step_mic_toggle(page: Page) -> bool:
    print("\n=== STEP: MIC TOGGLE ===")
    mic = page.locator('[data-testid="mic-button"]')
    mic.wait_for(state="visible", timeout=5_000)

    # Idempotent to starting state: click twice, assert each click flips
    # the state and we end up where we started. JS click bypasses Playwright's
    # stability check (the button pulses while recording).
    t0 = mic.get_attribute("title")
    print(f"[mic] initial title={t0!r}")
    shot(page, "mic_00_t0")

    mic.evaluate("el => el.click()")
    page.wait_for_timeout(1500)
    t1 = mic.get_attribute("title")
    print(f"[mic] after-click-1 title={t1!r}")
    shot(page, "mic_01_t1")

    mic.evaluate("el => el.click()")
    page.wait_for_timeout(1000)
    t2 = mic.get_attribute("title")
    print(f"[mic] after-click-2 title={t2!r}")
    shot(page, "mic_02_t2")

    flipped1 = t1 != t0 and t1 in ("Start recording", "Stop recording")
    flipped2 = t2 != t1 and t2 == t0
    ok = flipped1 and flipped2
    print(f"[mic] {'PASS' if ok else 'FAIL'} flip1={flipped1} flip2={flipped2}")
    return ok


def step_esc_interrupt(page: Page) -> bool:
    print("\n=== STEP: ESC INTERRUPT ===")
    # Send a long-winded prompt
    page.locator('[data-testid="chat-input"]').fill(
        "请用中文写一篇800字的散文，主题是春天，细节尽可能丰富。"
    )
    page.locator('[data-testid="send-button"]').click()

    # Wait for first assistant token to appear, then press Esc fast
    before = page.locator('[data-testid="chat-bubble-assistant"]').count()
    deadline = time.time() + 10
    streaming_started = False
    while time.time() < deadline:
        if page.locator('[data-testid="chat-bubble-assistant"]').count() > before:
            streaming_started = True
            break
        page.wait_for_timeout(200)

    if not streaming_started:
        print("[esc] WARN stream did not start within 10s (cold model?)")
    else:
        print("[esc] stream started; pressing Esc")

    shot(page, "esc_01_before")
    # Press Esc at page level
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    shot(page, "esc_02_after")

    # Record len-of-last-assistant before vs after 3s — should not grow
    def last_assistant_len() -> int:
        b = page.locator('[data-testid="chat-bubble-assistant"]')
        n = b.count()
        return len(b.nth(n - 1).inner_text()) if n > 0 else 0

    t1 = last_assistant_len()
    page.wait_for_timeout(3000)
    t2 = last_assistant_len()
    shot(page, "esc_03_stable")
    growth = t2 - t1
    print(f"[esc] reply len after-esc={t1} after-3s={t2} growth={growth}")
    ok = growth == 0
    print(f"[esc] {'PASS' if ok else 'FAIL'} stream halted by Esc")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[],
                    choices=["chat", "memory", "mic", "esc"])
    args = ap.parse_args()
    steps = args.only or ["chat", "memory", "mic", "esc"]

    ws_url = cdp_endpoint()
    print(f"[cdp] connecting to {ws_url}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_url)
        page = attach_page(browser)
        print(f"[cdp] attached to page: {page.url}  title={page.title()!r}")

        results = {}
        if "chat" in steps:
            results["chat"] = step_text_chat(page)
        if "memory" in steps:
            results["memory"] = step_memory_panel(page)
        if "mic" in steps:
            results["mic"] = step_mic_toggle(page)
        if "esc" in steps:
            results["esc"] = step_esc_interrupt(page)

        browser.close()

    print("\n=== SUMMARY ===")
    for k, v in results.items():
        print(f"  {k:8s}  {'PASS' if v else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
