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

    # Record dialog-bar text before send; VN 底栏单条渲染，判断新回复 = 文本变了
    bar = page.locator('[data-testid="dialog-bar-assistant"]')
    before_text = bar.inner_text().strip() if bar.count() else ""
    page.locator('[data-testid="send-button"]').click()
    print(f"[chat] sent; waiting for dialog-bar to change (was: {before_text[:30]!r})")

    # Poll up to 60s for a changed dialog-bar text (non-empty and different)
    deadline = time.time() + 60
    got_text = None
    while time.time() < deadline:
        if bar.count():
            cur = bar.inner_text().strip()
            if cur and cur != before_text:
                got_text = cur
                break
        page.wait_for_timeout(500)

    shot(page, "chat_02_after_reply")
    if got_text:
        snippet = got_text[:80].replace("\n", " ")
        print(f"[chat] PASS assistant reply: {snippet}")
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


def step_dialog_bar(page: Page) -> bool:
    """验证 VN 底栏行为：
    1. 底栏只渲染最新 1 条助手消息（不是多条堆叠）
    2. 用户消息气泡在 2s 内淡出（opacity < 0.3）
    3. 展开历史按钮点击后，历史面板出现、关闭按钮生效
    """
    print("\n=== STEP: DIALOG BAR (VN 底栏) ===")
    ensure_mic_idle(page)

    # 确保至少有 1 条对话
    page.locator('[data-testid="chat-input"]').fill("说句你好")
    page.locator('[data-testid="send-button"]').click()
    # 等助手回复出现在底栏
    deadline = time.time() + 60
    bar_text = ""
    while time.time() < deadline:
        bar = page.locator('[data-testid="dialog-bar-assistant"]')
        if bar.count() and bar.inner_text().strip():
            bar_text = bar.inner_text().strip()
            break
        page.wait_for_timeout(500)
    if not bar_text:
        print("[dialog] FAIL 底栏未渲染助手回复")
        shot(page, "dialog_01_fail_no_reply")
        return False
    print(f"[dialog] 底栏最新助手文本: {bar_text[:50]}")
    shot(page, "dialog_01_first_reply")

    # 断言 1：底栏里 assistant 节点只有 1 个
    assistant_nodes = page.locator('[data-testid="dialog-bar-assistant"]').count()
    if assistant_nodes != 1:
        print(f"[dialog] FAIL 底栏助手节点数 {assistant_nodes} != 1")
        shot(page, "dialog_01_fail_multi_nodes")
        return False
    print("[dialog] PASS 底栏只渲染 1 条助手消息")

    # 断言 2：发第二条消息，底栏应替换为新内容
    page.locator('[data-testid="chat-input"]').fill("再说一句")
    page.locator('[data-testid="send-button"]').click()
    # 等内容变化
    deadline = time.time() + 60
    new_text = bar_text
    while time.time() < deadline:
        bar2 = page.locator('[data-testid="dialog-bar-assistant"]')
        if bar2.count():
            cur = bar2.inner_text().strip()
            if cur and cur != bar_text:
                new_text = cur
                break
        page.wait_for_timeout(500)
    if new_text == bar_text:
        print("[dialog] FAIL 底栏未被第二条回复替换")
        shot(page, "dialog_02_fail")
        return False
    print(f"[dialog] PASS 底栏被替换为新内容: {new_text[:50]}...")
    shot(page, "dialog_02_replaced")

    # 断言 3：用户消息气泡 2s 内淡出
    page.locator('[data-testid="chat-input"]').fill("测试气泡")
    page.locator('[data-testid="send-button"]').click()
    user_bubble = page.locator('[data-testid="user-bubble-fleeting"]')
    # 轮询等气泡出现（最多 2s）
    bubble_deadline = time.time() + 2.0
    appeared = False
    while time.time() < bubble_deadline:
        if user_bubble.count() > 0:
            appeared = True
            break
        page.wait_for_timeout(100)
    if not appeared:
        print("[dialog] FAIL 用户小气泡未出现")
        shot(page, "dialog_03_fail_no_bubble")
        return False
    # 等 2.5s，应已淡出
    page.wait_for_timeout(2500)
    count_after = user_bubble.count()
    if count_after == 0:
        # DOM 已移除也算淡出
        opacity_val = 0.0
    else:
        opacity_val = user_bubble.evaluate(
            "el => parseFloat(getComputedStyle(el).opacity)"
        )
    if opacity_val > 0.3:
        print(f"[dialog] FAIL 用户气泡 2.5s 后仍可见 opacity={opacity_val}")
        shot(page, "dialog_03_fail_visible")
        return False
    print(f"[dialog] PASS 用户气泡淡出 opacity={opacity_val}")
    shot(page, "dialog_03_user_faded")

    # 断言 4：展开历史按钮点击 → 历史面板出现
    page.locator('[data-testid="dialog-history-toggle"]').click()
    page.wait_for_timeout(500)
    panel = page.locator('[data-testid="chat-history-panel"]')
    if panel.count() == 0:
        print("[dialog] FAIL 点击按钮后历史面板未出现")
        shot(page, "dialog_04_fail_no_panel")
        return False
    print("[dialog] PASS 历史面板已打开")
    shot(page, "dialog_04_history_open")

    # 断言 5：历史面板关闭按钮生效
    page.locator('[data-testid="chat-history-close"]').click()
    page.wait_for_timeout(500)
    if page.locator('[data-testid="chat-history-panel"]').count() != 0:
        print("[dialog] FAIL 历史面板未关闭")
        shot(page, "dialog_05_fail_still_open")
        return False
    print("[dialog] PASS 历史面板开关正常")
    shot(page, "dialog_05_history_closed")

    return True


def step_esc_interrupt(page: Page) -> bool:
    print("\n=== STEP: ESC INTERRUPT ===")
    # Send a long-winded prompt
    page.locator('[data-testid="chat-input"]').fill(
        "请用中文写一篇800字的散文，主题是春天，细节尽可能丰富。"
    )
    # 记录当前底栏文本作为 before 基线（VN 架构下新回复表现为底栏文字变化）
    bar = page.locator('[data-testid="dialog-bar-assistant"]')
    before_text = bar.inner_text().strip() if bar.count() else ""
    page.locator('[data-testid="send-button"]').click()

    # 等底栏文本变化，意味着 streaming 已经开始
    deadline = time.time() + 10
    streaming_started = False
    while time.time() < deadline:
        if bar.count():
            cur = bar.inner_text().strip()
            if cur and cur != before_text:
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

    # 记录底栏文字长度在按 Esc 后 vs 3s 后——不应再增长
    def bar_text_len() -> int:
        return len(bar.inner_text()) if bar.count() else 0

    t1 = bar_text_len()
    page.wait_for_timeout(3000)
    t2 = bar_text_len()
    shot(page, "esc_03_stable")
    growth = t2 - t1
    print(f"[esc] dialog-bar len after-esc={t1} after-3s={t2} growth={growth}")
    ok = growth == 0
    print(f"[esc] {'PASS' if ok else 'FAIL'} stream halted by Esc")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[],
                    choices=["chat", "memory", "mic", "esc", "dialog"])
    args = ap.parse_args()
    steps = args.only or ["chat", "memory", "mic", "esc", "dialog"]

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
        if "dialog" in steps:
            results["dialog"] = step_dialog_bar(page)

        browser.close()

    print("\n=== SUMMARY ===")
    for k, v in results.items():
        print(f"  {k:8s}  {'PASS' if v else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
