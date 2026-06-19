# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""A-5 PPT visual preview render loop tests."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.tools import ppt_tools
from deskpet.tools.ppt_tools import _HAS_PPTX, ppt_create


pytestmark = pytest.mark.skipif(not _HAS_PPTX, reason="python-pptx not installed")


OUTLINE = [
    {"layout": "title", "title": "Preview Deck", "subtitle": "A5"},
    {"layout": "bullet", "title": "要点", "bullets": ["自动渲染", "聊天内预览"]},
]


def _enable_preview_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ppt_tools, "_in_pytest", lambda: False, raising=False)


def test_preview_artifacts_appended(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _enable_preview_in_tests(monkeypatch)

    def fake_render_pptx_to_pngs(pptx_path: str, out_dir: str, **kwargs):
        assert Path(pptx_path).is_file()
        base = Path(out_dir)
        base.mkdir(parents=True, exist_ok=True)
        pngs = []
        for idx in range(1, 3):
            png = base / f"slide{idx}.png"
            png.write_bytes(b"\x89PNG\r\n" + (b"x" * 2048))
            pngs.append(str(png))
        return pngs

    monkeypatch.setattr(
        ppt_tools,
        "ppt_render",
        SimpleNamespace(
            com_render_available=lambda: True,
            render_pptx_to_pngs=fake_render_pptx_to_pngs,
        ),
        raising=False,
    )

    result = ppt_create(OUTLINE, output_path=str(tmp_path / "preview.pptx"))

    assert result["ok"] is True
    images = [a for a in result["artifacts"] if a.get("kind") == "image"]
    assert len(images) == 2
    assert images[0]["mime"] == "image/png"
    assert images[0]["title"] == "预览 第1页"
    assert images[1]["title"] == "预览 第2页"


def test_preview_disabled_by_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _enable_preview_in_tests(monkeypatch)

    # config 读取层: 生产路径读全局 config.config.raw
    import config as config_module

    monkeypatch.setattr(
        config_module,
        "config",
        SimpleNamespace(raw={"ppt": {"preview_render": False}}),
        raising=False,
    )
    assert ppt_tools._ppt_preview_render_enabled() is False

    # 接线层: enabled=False 时渲染器绝不被调
    monkeypatch.setattr(
        ppt_tools,
        "ppt_render",
        SimpleNamespace(
            com_render_available=lambda: True,
            render_pptx_to_pngs=lambda *a, **k: (_ for _ in ()).throw(AssertionError("render should not run")),
        ),
        raising=False,
    )

    result = ppt_create(OUTLINE, output_path=str(tmp_path / "disabled.pptx"))

    assert result["ok"] is True
    assert not [a for a in result["artifacts"] if a.get("kind") == "image"]


def test_preview_render_unavailable_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _enable_preview_in_tests(monkeypatch)
    monkeypatch.setattr(
        ppt_tools,
        "ppt_render",
        SimpleNamespace(
            com_render_available=lambda: False,
            render_pptx_to_pngs=lambda *a, **k: (_ for _ in ()).throw(AssertionError("render should not run")),
        ),
        raising=False,
    )

    result = ppt_create(OUTLINE, output_path=str(tmp_path / "unavailable.pptx"))

    assert result["ok"] is True
    assert result["path"].endswith("unavailable.pptx")
    assert not [a for a in result["artifacts"] if a.get("kind") == "image"]


def test_render_module_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DESKPET_PPT_PREVIEW_RENDER", "0")

    from deskpet.tools import ppt_render

    assert ppt_render.com_render_available() is False
    assert ppt_render.render_pptx_to_pngs(str(tmp_path / "missing.pptx"), str(tmp_path / "out")) == []
