# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""A-3 PPT bundled template resolve tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import ppt_tools
from deskpet.tools.ppt_tools import (
    _HAS_PPTX,
    _list_bundled_templates,
    _resolve_template_path,
    ppt_create,
)


pytestmark = pytest.mark.skipif(not _HAS_PPTX, reason="python-pptx not installed")


def _save_template(path: Path) -> Path:
    from pptx import Presentation

    Presentation().save(path)
    return path


def test_resolve_direct_path(tmp_path: Path) -> None:
    template = _save_template(tmp_path / "direct.pptx")

    resolved = _resolve_template_path(str(template))

    assert resolved == str(template.resolve())


def test_resolve_bundled_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundled = tmp_path / "templates"
    bundled.mkdir()
    template = _save_template(bundled / "foo.pptx")
    monkeypatch.setattr(ppt_tools, "_TEMPLATES_DIR", bundled)

    assert _resolve_template_path("foo") == str(template)
    assert _resolve_template_path("foo.pptx") == str(template)
    assert _list_bundled_templates() == ["foo"]


def test_resolve_unknown_returns_none() -> None:
    assert _resolve_template_path("不存在") is None


def test_ppt_create_uses_bundled_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundled = tmp_path / "templates"
    bundled.mkdir()
    _save_template(bundled / "foo.pptx")
    monkeypatch.setattr(ppt_tools, "_TEMPLATES_DIR", bundled)
    out = tmp_path / "out.pptx"
    outline = [
        {"layout": "title", "title": "Deck", "subtitle": "A-3"},
        {"layout": "bullet", "title": "Points", "bullets": ["A", "B"]},
    ]

    result = ppt_create(outline, output_path=str(out), template="foo")

    assert result["ok"] is True
    assert result["theme"] == "template"
    assert out.is_file()
