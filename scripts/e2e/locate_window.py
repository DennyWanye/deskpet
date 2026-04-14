"""Find the running deskpet.exe window, report geometry + screenshot."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pygetwindow as gw
import pyautogui


def find_deskpet():
    candidates = []
    for w in gw.getAllWindows():
        title = (w.title or "").strip()
        if not title:
            continue
        if any(k in title.lower() for k in ("deskpet", "desktop pet", "tauri")):
            candidates.append(w)
    return candidates


def main():
    wins = find_deskpet()
    if not wins:
        # Fallback: list everything so we can eyeball what title to use.
        print("no match; all visible windows:")
        for w in gw.getAllWindows():
            if w.title and w.visible:
                print(f"  [{w.title}]  {w.width}x{w.height} @ ({w.left},{w.top})")
        sys.exit(1)

    w = wins[0]
    print(f"found: [{w.title}]  {w.width}x{w.height} @ ({w.left},{w.top})")
    try:
        w.activate()
        time.sleep(0.5)
    except Exception as e:
        print(f"  (activate raised {e}, may already be focused)")

    out = Path(__file__).parent / "deskpet_window.png"
    shot = pyautogui.screenshot(region=(w.left, w.top, w.width, w.height))
    shot.save(out)
    print(f"screenshot saved: {out}")


if __name__ == "__main__":
    main()
