"""Sanity test — click the mic button, check for REC indicator."""
from __future__ import annotations

import time
from pathlib import Path

import ctypes
import pyautogui
import pygetwindow as gw


# Best-effort DPI-aware process flag so coordinates match visible pixels.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

SHOTS = Path(__file__).parent / "shots"
SHOTS.mkdir(exist_ok=True)


def find():
    for w in gw.getAllWindows():
        if "desktop pet" in (w.title or "").lower():
            return w
    raise SystemExit("not found")


def shot(w, name):
    pyautogui.screenshot(SHOTS / f"{name}.png", region=(w.left, w.top, w.width, w.height))


def main():
    w = find()
    print(f"window {w.width}x{w.height} at ({w.left},{w.top}) active={w.isActive}")
    try:
        w.activate()
        time.sleep(0.3)
    except Exception:
        pass

    shot(w, "sanity_00_before")

    # Mic button: bottom-left circle, 32x32 at (6,bottom-6-32). Center (22, height-22).
    mic_x = w.left + 22
    mic_y = w.top + w.height - 22
    print(f"click mic at ({mic_x},{mic_y})")
    pyautogui.click(mic_x, mic_y)
    time.sleep(1.5)
    shot(w, "sanity_01_after_mic_click")

    # Click again to stop (in case recording started)
    pyautogui.click(mic_x, mic_y)
    time.sleep(0.5)
    shot(w, "sanity_02_after_mic_stop")


if __name__ == "__main__":
    main()
