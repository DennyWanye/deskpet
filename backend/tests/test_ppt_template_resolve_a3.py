# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT 模板解析测试(迁库后版本)。

模板源已从 git 跟踪的 bundled 目录迁到外部库 resources/PPT_Template。
``_resolve_template_path`` 现在只解析【直传 .pptx 路径】或【库内 stem】;
大类名走预览图视觉选(见 test_ppt_template_picker.py)。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools.ppt_tools import (
    _HAS_PPTX,
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


def test_resolve_library_stem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """库内按 stem 递归匹配(env DESKPET_PPT_TEMPLATE_ROOT 指向库根)。"""
    lib = tmp_path / "lib"
    cat = lib / "01 测试类"
    cat.mkdir(parents=True)
    template = _save_template(cat / "foo.pptx")
    monkeypatch.setenv("DESKPET_PPT_TEMPLATE_ROOT", str(lib))

    assert _resolve_template_path("foo") == str(template.resolve())
    assert _resolve_template_path("foo.pptx") == str(template.resolve())


def test_resolve_unknown_returns_none() -> None:
    assert _resolve_template_path("不存在的模板名XYZ") is None


def test_ppt_create_uses_direct_path(tmp_path: Path) -> None:
    """直传一个 .pptx 路径 → 走模板填充路径(空模板回落 layout 填充)。"""
    template = _save_template(tmp_path / "tpl.pptx")
    out = tmp_path / "out.pptx"
    outline = [
        {"layout": "title", "title": "Deck", "subtitle": "A-3"},
        {"layout": "bullet", "title": "Points", "bullets": ["A", "B"]},
    ]

    result = ppt_create(outline, output_path=str(out), template=str(template))

    assert result["ok"] is True
    assert result["theme"] in {"template", "template-design"}
    assert out.is_file()
