# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unit tests for the OS-level computer-use toolset.

No real screenshots / clicks ever happen here:

* ``mss`` is replaced with a fake whose ``.grab()`` returns a tiny
  in-memory image and whose ``mss.tools.to_png`` writes a stub file.
* ``pyautogui`` is replaced with a recorder that just logs the calls.

Covered behaviours (the three the spec asks for + a few siblings):

* Flag OFF (default / explicit false)  → every tool returns the
  ``{"ok": false, "error": "disabled", ...}`` envelope and NEVER
  touches mss/pyautogui.
* ``screen_click`` clamps out-of-bounds coords into screen bounds.
* ``screen_capture`` returns ``{"ok": true, "path", "image_base64",
  ...}`` with the mocked grab.
* Bonus: scroll magnitude is capped; key combo is parsed.

The flag is driven through ``DESKPET_CONFIG`` pointing at a tmp
config.toml, exactly like the production resolution order.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from deskpet.tools import computer_use_tool as cu


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------
class _FakeShot:
    def __init__(self, w: int, h: int) -> None:
        self.size = (w, h)
        # 3 bytes/px RGB, content irrelevant — never decoded.
        self.rgb = b"\x00\x00\x00" * (w * h)


class _FakeSct:
    def __init__(self, w: int = 1920, h: int = 1080) -> None:
        # monitors[0] = virtual union, [1] = primary physical.
        self.monitors = [
            {"left": 0, "top": 0, "width": w, "height": h},
            {"left": 0, "top": 0, "width": w, "height": h},
        ]
        self.closed = False
        self._w, self._h = w, h

    def grab(self, region):  # noqa: ANN001
        return _FakeShot(int(region["width"]), int(region["height"]))

    def close(self) -> None:
        self.closed = True


def _make_fake_mss(w: int = 1920, h: int = 1080):
    """Build a module-like object standing in for the ``mss`` package."""
    mod = types.ModuleType("mss")

    def _mss_factory():  # mss.mss() -> context-ish object
        return _FakeSct(w, h)

    mod.mss = _mss_factory  # type: ignore[attr-defined]

    tools_mod = types.ModuleType("mss.tools")

    def _to_png(rgb, size, output=None):  # noqa: ANN001
        Path(output).write_bytes(b"\x89PNG\r\n\x1a\nFAKEPNGDATA")

    tools_mod.to_png = _to_png  # type: ignore[attr-defined]
    mod.tools = tools_mod  # type: ignore[attr-defined]
    return mod, tools_mod


class _FakePyAutoGUI:
    def __init__(self) -> None:
        self.FAILSAFE = True
        self.calls: list[tuple] = []

    def click(self, x=None, y=None, clicks=1, button="left"):  # noqa: ANN001
        self.calls.append(("click", x, y, clicks, button))

    def moveTo(self, x, y):  # noqa: ANN001, N802
        self.calls.append(("moveTo", x, y))

    def typewrite(self, text, interval=0.0):  # noqa: ANN001
        self.calls.append(("typewrite", text, interval))

    def press(self, key):  # noqa: ANN001
        self.calls.append(("press", key))

    def hotkey(self, *keys):  # noqa: ANN001
        self.calls.append(("hotkey", keys))

    def scroll(self, amount):  # noqa: ANN001
        self.calls.append(("scroll", amount))


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------
@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(ws))
    return ws


def _write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled):
    """Point DESKPET_CONFIG at a tmp config.toml; ``enabled`` may be
    True / False / None (None ⇒ section absent entirely)."""
    cfg = tmp_path / "config.toml"
    if enabled is None:
        cfg.write_text("[other]\nx = 1\n", encoding="utf-8")
    else:
        cfg.write_text(
            f"[code_e2e]\ncomputer_use_enabled = {str(bool(enabled)).lower()}\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("DESKPET_CONFIG", str(cfg))
    return cfg


@pytest.fixture
def fake_backends(monkeypatch: pytest.MonkeyPatch):
    """Install fake mss + pyautogui into the module + sys.modules."""
    fake_mss, fake_mss_tools = _make_fake_mss()
    fake_pg = _FakePyAutoGUI()

    # The module imports ``mss``/``mss.tools``/``pyautogui`` lazily inside
    # handlers via real ``import`` statements, so patch sys.modules too.
    monkeypatch.setitem(sys.modules, "mss", fake_mss)
    monkeypatch.setitem(sys.modules, "mss.tools", fake_mss_tools)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pg)

    # Reset the module-level lazy caches so each test re-imports the fake.
    monkeypatch.setattr(cu, "mss", None, raising=False)
    monkeypatch.setattr(cu, "pyautogui", None, raising=False)
    return fake_pg


# ---------------------------------------------------------------------
# Flag OFF — disabled envelope, no backend touched
# ---------------------------------------------------------------------
@pytest.mark.parametrize("flag", [False, None])
def test_all_tools_disabled_when_flag_off(
    flag, tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, flag)

    for name, args in [
        ("screen_capture", {}),
        ("screen_click", {"x": 10, "y": 10}),
        ("screen_move", {"x": 1, "y": 2}),
        ("screen_type", {"text": "hi"}),
        ("screen_key", {"keys": "ctrl+s"}),
        ("screen_scroll", {"amount": -3}),
    ]:
        out = json.loads(cu.registry.dispatch(name, args))
        assert out["ok"] is False, name
        assert out["error"] == "disabled", name
        assert "computer_use_enabled" in out["hint"], name

    # Backend must NOT have been invoked at all.
    assert fake_backends.calls == []


def test_disabled_when_no_config_file(monkeypatch, workspace, fake_backends):
    # DESKPET_CONFIG points nowhere; <repo>/config.toml in the worktree
    # has computer_use_enabled absent ⇒ disabled. Use a bogus path to be
    # deterministic regardless of the worktree's real config.toml.
    monkeypatch.setenv("DESKPET_CONFIG", str(workspace / "nope.toml"))
    out = json.loads(cu.registry.dispatch("screen_capture", {}))
    assert out["ok"] is False
    assert out["error"] == "disabled"


# ---------------------------------------------------------------------
# Flag ON — screen_capture happy path
# ---------------------------------------------------------------------
def test_screen_capture_returns_ok_shape(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(cu.registry.dispatch("screen_capture", {}))

    assert out["ok"] is True
    assert out["path"].startswith("screenshots/")
    assert out["path"].endswith(".png")
    assert out["image_mime"] == "image/png"
    assert out["bytes"] > 0
    assert isinstance(out["image_base64"], str) and out["image_base64"]
    assert out["width"] == 1920 and out["height"] == 1080
    # File actually written into the sandboxed workspace.
    assert (workspace / out["path"]).is_file()


def test_screen_capture_named_target_forced_png_and_sandboxed(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch("screen_capture", {"name": "shots/login"})
    )
    assert out["ok"] is True
    assert out["path"] == "shots/login.png"
    assert (workspace / "shots" / "login.png").is_file()


def test_screen_capture_rejects_escape(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch("screen_capture", {"name": "../escape.png"})
    )
    assert out["ok"] is False
    assert "escape" in out["error"]


# ---------------------------------------------------------------------
# Flag ON — screen_click coordinate clamping
# ---------------------------------------------------------------------
def test_screen_click_clamps_out_of_bounds(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch(
            "screen_click", {"x": 999999, "y": -50, "button": "left"}
        )
    )
    assert out["ok"] is True
    assert out["clamped"] is True
    assert out["requested_x"] == 999999 and out["requested_y"] == -50
    assert out["x"] == 1919  # screen width 1920 -> max index 1919
    assert out["y"] == 0  # negative clamped up to 0
    # pyautogui got the CLAMPED coords, never the wild ones.
    assert ("click", 1919, 0, 1, "left") in fake_backends.calls


def test_screen_click_in_bounds_not_clamped(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch(
            "screen_click", {"x": 400, "y": 300, "double": True}
        )
    )
    assert out["ok"] is True
    assert out["clamped"] is False
    assert out["double"] is True
    assert ("click", 400, 300, 2, "left") in fake_backends.calls


def test_screen_click_bad_coords(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch("screen_click", {"x": "abc", "y": 1})
    )
    assert out["ok"] is False
    assert "integers" in out["error"]


# ---------------------------------------------------------------------
# Flag ON — type / key / scroll
# ---------------------------------------------------------------------
def test_screen_type(tmp_path, monkeypatch, workspace, fake_backends):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch("screen_type", {"text": "hello"})
    )
    assert out["ok"] is True
    assert out["typed_chars"] == 5
    assert ("typewrite", "hello", 0.0) in fake_backends.calls


def test_screen_key_combo_parsed(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch("screen_key", {"keys": "Ctrl+S"})
    )
    assert out["ok"] is True
    assert out["keys"] == ["ctrl", "s"]
    assert ("hotkey", ("ctrl", "s")) in fake_backends.calls


def test_screen_key_single(tmp_path, monkeypatch, workspace, fake_backends):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(cu.registry.dispatch("screen_key", {"keys": "enter"}))
    assert out["ok"] is True
    assert ("press", "enter") in fake_backends.calls


def test_screen_scroll_magnitude_capped(
    tmp_path, monkeypatch, workspace, fake_backends
):
    _write_config(tmp_path, monkeypatch, True)
    out = json.loads(
        cu.registry.dispatch("screen_scroll", {"amount": 10_000_000})
    )
    assert out["ok"] is True
    assert out["capped"] is True
    assert out["amount"] == cu._MAX_SCROLL
    assert ("scroll", cu._MAX_SCROLL) in fake_backends.calls


# --- 误路由根治回归 (2026-06-14): disabled 时从 LLM schema 隐藏 ---

def test_computer_use_tools_hidden_from_schema_when_disabled():
    """flag OFF(默认)→ 6 个 screen_* 工具挂 sentinel requires_env →
    registry.schemas() 不返回它们 → LLM 看不到 → 不可能误路由调用。
    (注册仍在、dispatch 仍返回 disabled —— 见上面 disabled 测试。)"""
    sc = [s for s in cu.registry.all_specs() if s.name.startswith("screen_")]
    assert len(sc) == 6, [s.name for s in sc]
    # 都挂了永不满足的 sentinel env
    for s in sc:
        assert s.requires_env == ["__DESKPET_COMPUTER_USE_DISABLED__"], s.name
    # schemas() 全部过滤掉
    schema_names = {f["function"]["name"] for f in cu.registry.schemas()}
    for s in sc:
        assert s.name not in schema_names, f"{s.name} 不该出现在 LLM schema"
