# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""OS-level computer-use toolset (toolset=``computer_use``).

Lets a code-mode session agent (model ``gpt-5.5``, vision-capable, via
the the relay OpenAI-compatible base_url) do *simulated-human testing of
desktop apps* — the screenshot → reason → click/type loop that only
became viable once the driving model gained vision.

Primitives the agent composes
------------------------------

* ``screen_capture``  — grab the primary screen → PNG saved to the
  workspace + base64 (so the gpt-5.5 agent can literally see it).
* ``screen_click``    — left/right click, optional double, at (x, y).
* ``screen_type``     — type a literal string.
* ``screen_key``      — press a hotkey combo (e.g. ``ctrl+s``).
* ``screen_move``     — move the cursor without clicking.
* ``screen_scroll``   — vertical scroll by N "clicks".

Guardrail (footgun-prevention level — this is the user's *own* single
user machine, so no sandbox walls, but two cheap brakes):

  1. **Strangler-Fig flag.** Every tool refuses with
     ``{"ok": false, "error": "disabled", "hint": ...}`` unless
     ``[code_e2e] computer_use_enabled = true`` is set in config.toml.
     OFF by default ⇒ this whole module is zero-effect.
  2. **Coordinate clamping.** All x/y are clamped into the primary
     screen bounds before any pyautogui call, so a hallucinated
     ``click(999999, -50)`` can't fling the cursor / fail-safe-abort.
     Scroll magnitude is capped to prevent a "scroll storm".

``mss`` (fast, multi-monitor aware) does the capture; ``pyautogui``
does input. Both are imported lazily *inside* the handlers so module
import never fails on a headless CI box — discovery (``tools/__init__``)
keeps working and the disabled-flag tests need no display.

Error contract matches the registry: every handler returns a JSON
**string**. Success ⇒ ``{"ok": true, ...}``; failure ⇒
``{"ok": false, "error": ..., "hint": ...}``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import platformdirs
import tomli

from .registry import registry

logger = logging.getLogger(__name__)

_APP_NAME = "deskpet"

# Scroll magnitude cap (absolute "clicks"). pyautogui scroll units are
# platform-dependent but a value past this is almost certainly a model
# hallucination / runaway loop, not a real intent.
_MAX_SCROLL = 2000


# ---------------------------------------------------------------------
# JSON helpers — note the ``ok``-based contract for this toolset
# ---------------------------------------------------------------------
def _err(msg: str, hint: str = "", *, retriable: bool = False) -> str:
    return json.dumps(
        {"ok": False, "error": msg, "hint": hint, "retriable": retriable},
        ensure_ascii=False,
    )


def _ok(**fields: Any) -> str:
    return json.dumps({"ok": True, **fields}, ensure_ascii=False)


# ---------------------------------------------------------------------
# Config flag — [code_e2e] computer_use_enabled
# ---------------------------------------------------------------------
# Resolution order mirrors backend/config.py / tools/_config.py but only
# covers the two paths a tool-only run needs:
#   1. DESKPET_CONFIG env override (tests / CI)
#   2. <repo>/config.toml (dev mode) — three parents up from this file
#      (tools/ -> deskpet/ -> backend/ -> <repo>/).
def _config_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("DESKPET_CONFIG")
    if override:
        paths.append(Path(override))
    here = Path(__file__).resolve()
    paths.append(here.parents[3] / "config.toml")
    return paths


def _computer_use_enabled() -> bool:
    """True iff ``[code_e2e] computer_use_enabled`` is truthy in config.

    Never raises — a missing / malformed config means *disabled* (the
    safe default for an OS-automation footgun).
    """
    for p in _config_candidate_paths():
        try:
            if p.is_file():
                with p.open("rb") as f:
                    raw = tomli.load(f)
                section = raw.get("code_e2e") or {}
                return bool(section.get("computer_use_enabled", False))
        except (OSError, tomli.TOMLDecodeError):
            continue
    return False


# ---------------------------------------------------------------------
# Lazy backend imports (mss / pyautogui)
# ---------------------------------------------------------------------
# Imported inside handlers so:
#   * module import (tool auto-discovery) never crashes on headless CI;
#   * the disabled-flag path needs no display / X server / deps at all;
#   * tests can monkeypatch ``computer_use_tool.mss`` /
#     ``computer_use_tool.pyautogui`` to inject fakes.
mss = None  # type: ignore[assignment]
pyautogui = None  # type: ignore[assignment]
_import_lock = threading.Lock()


def _load_mss():  # type: ignore[no-untyped-def]
    global mss
    with _import_lock:
        if mss is None:
            import mss as _mss  # noqa: PLC0415 — deliberately lazy

            mss = _mss
    return mss


def _load_pyautogui():  # type: ignore[no-untyped-def]
    global pyautogui
    with _import_lock:
        if pyautogui is None:
            # FAILSAFE on: slamming the cursor to a corner aborts an
            # out-of-control automation run (extra brake on top of our
            # own coordinate clamp).
            import pyautogui as _pg  # noqa: PLC0415 — deliberately lazy

            _pg.FAILSAFE = True
            pyautogui = _pg
    return pyautogui


def _screen_size() -> tuple[int, int]:
    """Primary-screen (width, height) in pixels via mss monitor[1].

    ``monitors[0]`` is the virtual union of all displays; ``[1]`` is the
    primary physical monitor — the only one we clamp/click against to
    keep multi-monitor coordinate math unambiguous.
    """
    sct = _load_mss().mss()
    try:
        mons = sct.monitors
        primary = mons[1] if len(mons) > 1 else mons[0]
        return int(primary["width"]), int(primary["height"])
    finally:
        close = getattr(sct, "close", None)
        if callable(close):
            close()


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _workspace_root() -> Path:
    override = os.environ.get("DESKPET_WORKSPACE_DIR")
    if override:
        root = Path(override).resolve()
    else:
        base = Path(
            platformdirs.user_data_dir(_APP_NAME, appauthor=False, roaming=True)
        )
        root = (base / "workspace").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _guard() -> str | None:
    """Return an error-JSON string if the toolset is gated off, else None."""
    if not _computer_use_enabled():
        return _err(
            "disabled",
            "OS computer-use is OFF. Set [code_e2e] computer_use_enabled "
            "= true in config.toml to allow desktop automation.",
            retriable=False,
        )
    return None


# ---------------------------------------------------------------------
# screen_capture
# ---------------------------------------------------------------------
_SCHEMA_CAPTURE: dict[str, Any] = {
    "name": "screen_capture",
    "description": (
        "Take a screenshot of the primary screen for visual inspection "
        "of a desktop app under test. Saves a PNG into the DeskPet "
        "workspace and ALSO returns it base64-encoded so you (a "
        "vision-capable model) can look at it directly. Use this before "
        "every click to confirm UI state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Optional workspace filename (relative, .png). "
                    "Default: an auto timestamped name under screenshots/."
                ),
            }
        },
        "required": [],
    },
}


def _handle_screen_capture(args: dict[str, Any], task_id: str) -> str:
    gated = _guard()
    if gated is not None:
        return gated

    rel = str(args.get("name") or "").strip()
    if rel:
        # Keep capture writes inside the workspace; reject escapes.
        cand = Path(rel)
        if cand.is_absolute() or str(cand).startswith(("\\\\", "//")):
            return _err("name must be workspace-relative", "drop drive/UNC prefix")
        target = (_workspace_root() / rel).resolve()
        try:
            target.relative_to(_workspace_root())
        except ValueError:
            return _err("name escapes workspace", "no .. traversal")
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        target = _workspace_root() / "screenshots" / f"screen-{ts}.png"

    try:
        sct = _load_mss().mss()
    except Exception as exc:  # noqa: BLE001 — headless / no display / missing dep
        return _err(
            f"screen capture backend unavailable: {exc}",
            "Likely headless (no display) or mss not installed.",
            retriable=False,
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mons = sct.monitors
        region = mons[1] if len(mons) > 1 else mons[0]
        shot = sct.grab(region)
        # mss ships its own PNG encoder (mss.tools.to_png) so we don't
        # drag in a hard Pillow dependency for capture.
        from mss.tools import to_png  # noqa: PLC0415

        to_png(shot.rgb, shot.size, output=str(target))
        raw = target.read_bytes()
    except Exception as exc:  # noqa: BLE001 — uniform JSON envelope
        return _err(f"capture failed: {exc}", "", retriable=True)
    finally:
        close = getattr(sct, "close", None)
        if callable(close):
            close()

    b64 = base64.b64encode(raw).decode("ascii")
    return _ok(
        path=str(target.relative_to(_workspace_root())).replace("\\", "/"),
        abs_path=str(target),
        width=int(getattr(shot, "size", (0, 0))[0]),
        height=int(getattr(shot, "size", (0, 0))[1]),
        bytes=len(raw),
        image_base64=b64,
        image_mime="image/png",
    )


# ---------------------------------------------------------------------
# screen_click
# ---------------------------------------------------------------------
_SCHEMA_CLICK: dict[str, Any] = {
    "name": "screen_click",
    "description": (
        "Click the mouse at primary-screen pixel (x, y). Coordinates are "
        "clamped into screen bounds (out-of-range is corrected, not "
        "rejected). Take a screen_capture first to know where to click."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X pixel (0 = left)."},
            "y": {"type": "integer", "description": "Y pixel (0 = top)."},
            "button": {
                "type": "string",
                "enum": ["left", "right"],
                "default": "left",
            },
            "double": {
                "type": "boolean",
                "description": "Double-click instead of single.",
                "default": False,
            },
        },
        "required": ["x", "y"],
    },
}


def _handle_screen_click(args: dict[str, Any], task_id: str) -> str:
    gated = _guard()
    if gated is not None:
        return gated

    try:
        x = int(args.get("x"))
        y = int(args.get("y"))
    except (TypeError, ValueError):
        return _err("x and y must be integers", "pass numeric pixel coords")

    button = str(args.get("button", "left") or "left")
    if button not in {"left", "right"}:
        return _err(f"invalid button: {button}", "use 'left' or 'right'")
    double = bool(args.get("double", False))

    try:
        sw, sh = _screen_size()
        pg = _load_pyautogui()
    except Exception as exc:  # noqa: BLE001 — headless / missing dep
        return _err(
            f"input backend unavailable: {exc}",
            "Likely headless or pyautogui/mss not installed.",
            retriable=False,
        )

    cx = _clamp(x, 0, max(0, sw - 1))
    cy = _clamp(y, 0, max(0, sh - 1))
    clamped = (cx, cy) != (x, y)

    try:
        pg.click(
            x=cx,
            y=cy,
            clicks=2 if double else 1,
            button=button,
        )
    except Exception as exc:  # noqa: BLE001 — uniform JSON envelope
        return _err(f"click failed: {exc}", "", retriable=True)

    return _ok(
        x=cx,
        y=cy,
        requested_x=x,
        requested_y=y,
        clamped=clamped,
        button=button,
        double=double,
        screen=[sw, sh],
    )


# ---------------------------------------------------------------------
# screen_move
# ---------------------------------------------------------------------
_SCHEMA_MOVE: dict[str, Any] = {
    "name": "screen_move",
    "description": (
        "Move the mouse cursor to primary-screen pixel (x, y) WITHOUT "
        "clicking. Coordinates are clamped into screen bounds. Useful to "
        "trigger hover states before a screen_capture."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X pixel."},
            "y": {"type": "integer", "description": "Y pixel."},
        },
        "required": ["x", "y"],
    },
}


def _handle_screen_move(args: dict[str, Any], task_id: str) -> str:
    gated = _guard()
    if gated is not None:
        return gated
    try:
        x = int(args.get("x"))
        y = int(args.get("y"))
    except (TypeError, ValueError):
        return _err("x and y must be integers", "pass numeric pixel coords")

    try:
        sw, sh = _screen_size()
        pg = _load_pyautogui()
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"input backend unavailable: {exc}",
            "Likely headless or pyautogui/mss not installed.",
            retriable=False,
        )

    cx = _clamp(x, 0, max(0, sw - 1))
    cy = _clamp(y, 0, max(0, sh - 1))
    try:
        pg.moveTo(cx, cy)
    except Exception as exc:  # noqa: BLE001
        return _err(f"move failed: {exc}", "", retriable=True)
    return _ok(
        x=cx,
        y=cy,
        requested_x=x,
        requested_y=y,
        clamped=(cx, cy) != (x, y),
        screen=[sw, sh],
    )


# ---------------------------------------------------------------------
# screen_type
# ---------------------------------------------------------------------
_SCHEMA_TYPE: dict[str, Any] = {
    "name": "screen_type",
    "description": (
        "Type a literal UTF-8 string into the currently focused control "
        "(as if typed on the keyboard). Click the target field first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to type."},
            "interval_ms": {
                "type": "integer",
                "description": "Per-character delay in ms. Default 0.",
                "default": 0,
            },
        },
        "required": ["text"],
    },
}


def _handle_screen_type(args: dict[str, Any], task_id: str) -> str:
    gated = _guard()
    if gated is not None:
        return gated
    text = args.get("text")
    if not isinstance(text, str):
        return _err("text must be a string", "wrap value in quotes")
    interval = max(0, int(args.get("interval_ms", 0) or 0)) / 1000.0

    try:
        pg = _load_pyautogui()
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"input backend unavailable: {exc}",
            "Likely headless or pyautogui not installed.",
            retriable=False,
        )
    try:
        pg.typewrite(text, interval=interval)
    except Exception as exc:  # noqa: BLE001
        return _err(f"type failed: {exc}", "", retriable=True)
    return _ok(typed_chars=len(text))


# ---------------------------------------------------------------------
# screen_key
# ---------------------------------------------------------------------
_SCHEMA_KEY: dict[str, Any] = {
    "name": "screen_key",
    "description": (
        "Press a keyboard key or hotkey combo. Use '+' to chord "
        "(e.g. 'ctrl+s', 'alt+f4', 'enter', 'tab'). Single keys also "
        "work ('esc', 'f5')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keys": {
                "type": "string",
                "description": "Key or '+'-joined combo, e.g. 'ctrl+s'.",
            }
        },
        "required": ["keys"],
    },
}


def _handle_screen_key(args: dict[str, Any], task_id: str) -> str:
    gated = _guard()
    if gated is not None:
        return gated
    keys = str(args.get("keys", "") or "").strip()
    if not keys:
        return _err("keys is required", "e.g. 'ctrl+s' or 'enter'")
    parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
    if not parts:
        return _err("no valid keys parsed", "e.g. 'ctrl+s'")

    try:
        pg = _load_pyautogui()
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"input backend unavailable: {exc}",
            "Likely headless or pyautogui not installed.",
            retriable=False,
        )
    try:
        if len(parts) == 1:
            pg.press(parts[0])
        else:
            pg.hotkey(*parts)
    except Exception as exc:  # noqa: BLE001
        return _err(f"key press failed: {exc}", "", retriable=True)
    return _ok(keys=parts)


# ---------------------------------------------------------------------
# screen_scroll
# ---------------------------------------------------------------------
_SCHEMA_SCROLL: dict[str, Any] = {
    "name": "screen_scroll",
    "description": (
        "Vertical mouse-wheel scroll by N clicks (positive = up, "
        "negative = down). Magnitude is capped to prevent runaway "
        "scroll storms."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {
                "type": "integer",
                "description": "Wheel clicks. + = up, - = down.",
            }
        },
        "required": ["amount"],
    },
}


def _handle_screen_scroll(args: dict[str, Any], task_id: str) -> str:
    gated = _guard()
    if gated is not None:
        return gated
    try:
        amount = int(args.get("amount"))
    except (TypeError, ValueError):
        return _err("amount must be an integer", "e.g. -3 to scroll down")

    capped = _clamp(amount, -_MAX_SCROLL, _MAX_SCROLL)
    try:
        pg = _load_pyautogui()
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"input backend unavailable: {exc}",
            "Likely headless or pyautogui not installed.",
            retriable=False,
        )
    try:
        pg.scroll(capped)
    except Exception as exc:  # noqa: BLE001
        return _err(f"scroll failed: {exc}", "", retriable=True)
    return _ok(amount=capped, requested=amount, capped=capped != amount)


# ---------------------------------------------------------------------
# Registration — toolset gated by ContextAssembler's enabled_toolsets,
# AND at runtime by the [code_e2e] computer_use_enabled flag.
# permission_category "shell"（高危系统控制类）—— 注意："shell_exec" 不在合法
# PermissionCategory 集（read_file/.../shell/...），gate.check 会 raise ValueError
# 致工具被吞不执行（同 agent_parallel/skill_invoke 的 P0 bug），故用合法的 "shell"。
# ---------------------------------------------------------------------
_TOOLSET = "computer_use"

# 2026-06-14: 误路由根治 —— computer_use 工具(screen_click 等)此前【无条件
# 注册且永远暴露在 LLM 的工具 schema 里】,只在 handler 运行时检查 flag 返回
# "disabled"。后果:污染的会话上下文一诱导,LLM 就去调 screen_click 撞 disabled
# 墙(真机实测:一个电池调研 query 被带偏成"按坐标点击")。修法:flag OFF(默认)
# 时给这些工具挂一个【永不设置的 sentinel env】→ schemas() 的 requires_env 过滤
# 把它们从 LLM 视野里隐藏(LLM 看不到就不可能误调),而 dispatch() 不查 env →
# 万一被强行调用仍走 handler 返回 disabled(保留 disabled 测试 + 纵深防御)。
# flag ON(code_e2e 真测)时 requires_env=[] → 正常可见可用。
_CU_HIDE_ENV: list[str] = [] if _computer_use_enabled() else ["__DESKPET_COMPUTER_USE_DISABLED__"]

registry.register(
    "screen_capture",
    _TOOLSET,
    _SCHEMA_CAPTURE,
    _handle_screen_capture,
    permission_category="read_file",
    requires_env=_CU_HIDE_ENV,
)
registry.register(
    "screen_click",
    _TOOLSET,
    _SCHEMA_CLICK,
    _handle_screen_click,
    permission_category="shell",
    dangerous=True,
    requires_env=_CU_HIDE_ENV,
)
registry.register(
    "screen_move",
    _TOOLSET,
    _SCHEMA_MOVE,
    _handle_screen_move,
    permission_category="shell",
    requires_env=_CU_HIDE_ENV,
)
registry.register(
    "screen_type",
    _TOOLSET,
    _SCHEMA_TYPE,
    _handle_screen_type,
    permission_category="shell",
    dangerous=True,
    requires_env=_CU_HIDE_ENV,
)
registry.register(
    "screen_key",
    _TOOLSET,
    _SCHEMA_KEY,
    _handle_screen_key,
    permission_category="shell",
    dangerous=True,
    requires_env=_CU_HIDE_ENV,
)
registry.register(
    "screen_scroll",
    _TOOLSET,
    _SCHEMA_SCROLL,
    _handle_screen_scroll,
    permission_category="shell",
    requires_env=_CU_HIDE_ENV,
)
