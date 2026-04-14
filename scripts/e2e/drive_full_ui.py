"""Full UI driver — locate controls by pixel color, click them, verify.

Avoids hard-coded coords so it survives DPR/scaling. Colors are taken from
App.tsx inline styles:
  mic idle     = #6b7280
  send bg (connected) = #3b82f6
  memory btn bg = rgba(0,0,0,0.5) over transparent — use the 🗂 emoji glyph
  input bg     = rgba(255,255,255,0.95)

Each step screenshots before/after under scripts/e2e/shots/.
"""
from __future__ import annotations

import argparse
import ctypes
import time
from pathlib import Path

import pyautogui
import pygetwindow as gw
from PIL import Image


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


SHOTS = Path(__file__).parent / "shots"
SHOTS.mkdir(exist_ok=True)


def find_window():
    for w in gw.getAllWindows():
        if "desktop pet" in (w.title or "").lower():
            return w
    raise SystemExit("Desktop Pet window not found")


def shot(w, name: str) -> Image.Image:
    path = SHOTS / f"{name}.png"
    img = pyautogui.screenshot(path, region=(w.left, w.top, w.width, w.height))
    return img


def find_color_bbox(img: Image.Image, target, tol=6, region=None):
    """Return (x_center, y_center) of the dominant blob matching target RGB."""
    w, h = img.size
    x0, y0, x1, y1 = region or (0, 0, w, h)
    hits_x = []
    hits_y = []
    pixels = img.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            px = pixels[x, y]
            if (
                abs(px[0] - target[0]) <= tol
                and abs(px[1] - target[1]) <= tol
                and abs(px[2] - target[2]) <= tol
            ):
                hits_x.append(x)
                hits_y.append(y)
    if not hits_x:
        return None
    # Use median to ignore outliers
    hits_x.sort(); hits_y.sort()
    cx = hits_x[len(hits_x) // 2]
    cy = hits_y[len(hits_y) // 2]
    return (cx, cy, min(hits_x), max(hits_x), min(hits_y), max(hits_y))


def click_win(w, local_x, local_y):
    """Click at window-local pixel coords."""
    abs_x = w.left + local_x
    abs_y = w.top + local_y
    pyautogui.click(abs_x, abs_y)


def type_unicode(text: str):
    import pyperclip
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")


def step_text_chat(w):
    print("\n=== STEP: TEXT CHAT ===")
    img = shot(w, "chat_00_initial")

    # 1. Find input field — bottom row, mostly white (rgba(255,255,255,0.95))
    #    which is approximately RGB (242,242,242) after blending with dark bg
    res = find_color_bbox(img, (242, 242, 242), tol=8, region=(0, w.height - 80, w.width, w.height))
    if not res:
        # Try pure white
        res = find_color_bbox(img, (255, 255, 255), tol=5, region=(0, w.height - 80, w.width, w.height))
    if not res:
        raise RuntimeError("input field not found by color")
    in_cx, in_cy, in_x0, in_x1, in_y0, in_y1 = res
    print(f"[ui] input bbox x={in_x0}-{in_x1} y={in_y0}-{in_y1}  center=({in_cx},{in_cy})")

    # 2. Click center of input
    click_win(w, in_cx, in_cy)
    time.sleep(0.5)

    # 3. Type message
    prompt = "你好！请用一句话介绍自己。"
    print(f"[ui] typing: {prompt}")
    type_unicode(prompt)
    time.sleep(0.5)
    shot(w, "chat_01_typed")

    # 4. Press Enter
    pyautogui.press("enter")
    print("[ui] Enter pressed, waiting for reply...")

    # 5. Wait up to 20s for reply bubble. Reply bubbles are at bottom:55px.
    deadline = time.time() + 20.0
    last_shot = None
    while time.time() < deadline:
        time.sleep(1.0)
        last_shot = shot(w, f"chat_02_wait_{int(deadline - time.time())}s")
    print("[ui] (waited 20s)")


def step_memory_panel(w):
    print("\n=== STEP: MEMORY PANEL ===")
    img = shot(w, "mem_00_before")

    # Scan for horizontal runs of very-dark pixels (button bg rgba(0,0,0,0.5))
    # in the top 50px. First run after BG gap is the 🗂 memory button.
    def classify(p):
        r, g, b = p[:3]
        if r < 25 and g < 25 and b < 25:
            return "BTN"
        return "BG"

    # Sweep y=20..45 to find the row with most BTN pixels (button body center)
    best_y, best_btn = 32, 0
    for y in range(18, 48):
        cnt = sum(1 for x in range(w.width) if classify(img.getpixel((x, y))) == "BTN")
        if cnt > best_btn:
            best_y, best_btn = y, cnt
    print(f"[ui] top-bar best row y={best_y} btn-pixels={best_btn}")

    # Collect run spans at best_y
    runs = []
    last = None; start = 0
    for x in range(w.width):
        cls = classify(img.getpixel((x, best_y)))
        if cls != last:
            if last == "BTN":
                runs.append((start, x - 1))
            if cls == "BTN":
                start = x
            last = cls
    if last == "BTN":
        runs.append((start, w.width - 1))

    # Filter runs: we want the memory button (~14px wide) — the LEFTMOST isolated
    # BTN run that has at least 6px width and is in the left half of status cluster.
    if not runs:
        print("[ui] no button runs found; abort")
        return
    print(f"[ui] BTN runs (first 5): {runs[:5]}")
    # The 🗂 button is the leftmost BTN run
    mem_x0, mem_x1 = runs[0]
    target_x = (mem_x0 + mem_x1) // 2
    target_y = best_y
    print(f"[ui] clicking memory-toggle at local ({target_x},{target_y})")
    click_win(w, target_x, target_y)
    time.sleep(1.5)
    after = shot(w, "mem_01_after_click")

    # Verify: panel opened => large dark overlay in window center
    # Sample several pixels in the middle 50% area
    dark_count = 0
    total = 0
    for y in range(w.height // 3, w.height * 2 // 3, 10):
        for x in range(w.width // 4, w.width * 3 // 4, 10):
            p = after.getpixel((x, y))[:3]
            if p[0] < 30 and p[1] < 30 and p[2] < 30:
                dark_count += 1
            total += 1
    print(f"[ui] dark pixels in center region: {dark_count}/{total}")
    if dark_count / max(total, 1) > 0.5:
        print("[ui] memory panel OPENED successfully")
    else:
        print("[ui] panel did NOT open — sample a pixel:",
              after.getpixel((w.width // 2, w.height // 2)))


def step_mic_toggle(w):
    print("\n=== STEP: MIC TOGGLE ===")
    img = shot(w, "mic_00_before")
    # Mic idle: bg #6b7280 = (107, 114, 128)
    res = find_color_bbox(img, (107, 114, 128), tol=8, region=(0, w.height - 100, w.width // 3, w.height))
    if not res:
        raise RuntimeError("mic button not found by color")
    cx, cy, x0, x1, y0, y1 = res
    print(f"[ui] mic bbox x={x0}-{x1} y={y0}-{y1}  center=({cx},{cy})")
    click_win(w, cx, cy)
    time.sleep(1.5)
    shot(w, "mic_01_after_start")

    # Click again to stop
    click_win(w, cx, cy)
    time.sleep(0.5)
    shot(w, "mic_02_after_stop")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", nargs="*", default=[], choices=["chat", "memory", "mic"])
    args = ap.parse_args()

    w = find_window()
    print(f"window {w.width}x{w.height} at ({w.left},{w.top})")
    try:
        w.activate()
        time.sleep(0.5)
    except Exception as exc:
        print(f"activate failed: {exc}")

    if "chat" not in args.skip:
        step_text_chat(w)
    if "memory" not in args.skip:
        step_memory_panel(w)
    if "mic" not in args.skip:
        step_mic_toggle(w)


if __name__ == "__main__":
    main()
