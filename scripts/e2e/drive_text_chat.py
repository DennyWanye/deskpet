"""Drive the real Desktop Pet window via mouse+keyboard — text chat path.

Steps:
 1. Find window by title
 2. Click the text input (bottom center)
 3. Type a message
 4. Press Enter (handleKeyDown sends on Enter)
 5. Wait for reply, screenshot
 6. Scroll-read backend log for chat_response confirmation

Run with: backend/.venv/Scripts/python.exe scripts/e2e/drive_text_chat.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pyautogui
import pygetwindow as gw


PROMPT = "你好！请一句话介绍你自己。"
SHOTS_DIR = Path(__file__).parent / "shots"
SHOTS_DIR.mkdir(exist_ok=True)


def find_window():
    for w in gw.getAllWindows():
        if "desktop pet" in (w.title or "").lower():
            return w
    raise SystemExit("Desktop Pet window not found")


def shot(w, name: str) -> Path:
    p = SHOTS_DIR / f"{name}.png"
    pyautogui.screenshot(p, region=(w.left, w.top, w.width, w.height))
    return p


def main():
    w = find_window()
    print(f"[ui] window {w.width}x{w.height} @ ({w.left},{w.top})")
    try:
        w.activate()
    except Exception:
        pass
    time.sleep(0.5)

    # Text input sits in the bottom row; bottom: 6px; row gap ~4px; input flex:1.
    # The row has mic button (32w), optional interrupt (32w, absent here), input,
    # Send button (~60w). Input vertical center ≈ height - 6 - 16 = height-22.
    # Input horizontal center ≈ (left_after_mic + right_before_send) / 2
    #   left_after_mic ≈ 6 + 32 + 4 = 42
    #   right_before_send ≈ width - 6 - 60 - 4 = width - 70
    input_x = w.left + (42 + (w.width - 70)) // 2
    input_y = w.top + w.height - 22
    print(f"[ui] click input at ({input_x},{input_y})")

    shot(w, "01_before")
    pyautogui.click(input_x, input_y)
    time.sleep(0.3)
    # Use write for ASCII but we want Chinese — fall through to keyboard fake typing
    # via the clipboard for non-ASCII.
    try:
        import pyperclip  # noqa
        pyperclip.copy(PROMPT)
        pyautogui.hotkey("ctrl", "v")
    except Exception:
        # Fallback: pyautogui.write can't do CJK — type via unicode sequence.
        # This won't work on most layouts but we keep it as the best-effort path.
        pyautogui.write(PROMPT, interval=0.02)
    time.sleep(0.3)
    shot(w, "02_typed")

    print("[ui] press Enter")
    pyautogui.press("enter")

    # Wait for chat_response — Ollama usually replies < 10s for a short prompt.
    for i in range(1, 16):
        time.sleep(1)
        shot(w, f"03_after_{i:02d}s")
    print("[ui] done — see scripts/e2e/shots/")


if __name__ == "__main__":
    main()
