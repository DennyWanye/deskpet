"""Drive Esc interrupt: send a long prompt, then press Esc ~0.5s later.

Expected UI changes:
 - thinking indicator appears briefly (top bar shows 🤔 or similar)
 - reply bubble starts appearing
 - after Esc, stream stops, a status hint is shown
"""
from __future__ import annotations

import ctypes
import time

import pyautogui
import pygetwindow as gw
import pyperclip

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


def find():
    for w in gw.getAllWindows():
        if "desktop pet" in (w.title or "").lower():
            return w
    raise SystemExit("not found")


def main():
    w = find()
    print(f"window {w.width}x{w.height} at ({w.left},{w.top})")
    w.activate()
    time.sleep(0.4)

    # Click input (center ~302, 860 based on earlier measurement)
    pyautogui.click(w.left + 302, w.top + 860)
    time.sleep(0.3)

    prompt = "请用中文写一首100字的唐诗，再解释它的含义，字数不少于300字。"
    pyperclip.copy(prompt)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pyautogui.screenshot("scripts/e2e/shots/esc_01_typed.png",
                         region=(w.left, w.top, w.width, w.height))

    pyautogui.press("enter")
    print(f"[esc] sent: {prompt}")

    # Wait a moment for reply to start streaming
    time.sleep(2.0)
    pyautogui.screenshot("scripts/e2e/shots/esc_02_streaming.png",
                         region=(w.left, w.top, w.width, w.height))
    print("[esc] captured mid-stream; pressing Esc")

    # Press Esc to interrupt
    pyautogui.press("escape")
    time.sleep(1.5)
    pyautogui.screenshot("scripts/e2e/shots/esc_03_after_interrupt.png",
                         region=(w.left, w.top, w.width, w.height))
    print("[esc] captured after interrupt")

    # Wait a bit more to confirm no more tokens arrive
    time.sleep(3.0)
    pyautogui.screenshot("scripts/e2e/shots/esc_04_final.png",
                         region=(w.left, w.top, w.width, w.height))
    print("[esc] final capture — see scripts/e2e/shots/esc_*.png")


if __name__ == "__main__":
    main()
