# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""B-2 PPT 整页生图布局测试。"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from deskpet.tools.ppt_tools import _HAS_PPTX, ppt_create


pytestmark = pytest.mark.skipif(not _HAS_PPTX, reason="python-pptx not installed")


PNG_1X1_TRANSPARENT = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
def tiny_png(tmp_path: Path) -> Path:
    path = tmp_path / "generated.png"
    path.write_bytes(base64.b64decode(PNG_1X1_TRANSPARENT))
    return path


def test_image_prompt_autofills_path(monkeypatch, tmp_path: Path, tiny_png: Path) -> None:
    from deskpet.tools import image_tools

    calls: list[list[str]] = []

    def fake_generate_images(prompts, **kwargs):
        calls.append(list(prompts))
        return [{"prompt": prompts[0], "path": str(tiny_png), "error": None}]

    monkeypatch.setattr(image_tools, "generate_images", fake_generate_images)
    out = tmp_path / "image-full.pptx"

    result = ppt_create(
        [{"layout": "image_full", "title": "封面", "image_prompt": "a teal abstract cover"}],
        output_path=str(out),
    )

    assert result["ok"] is True
    assert out.is_file()
    # prompt 现会追加版式负空间指令 + 禁字后缀,断言原 prompt 是前缀。
    assert len(calls) == 1 and len(calls[0]) == 1
    assert calls[0][0].startswith("a teal abstract cover")
    assert "no text" in calls[0][0].lower()


def test_no_image_prompt_no_network(monkeypatch, tmp_path: Path) -> None:
    from deskpet.tools import image_tools

    def fail_generate_images(prompts, **kwargs):
        raise AssertionError("generate_images should not be called")

    monkeypatch.setattr(image_tools, "generate_images", fail_generate_images)
    out = tmp_path / "plain.pptx"

    result = ppt_create(
        [{"layout": "bullet", "title": "普通页", "bullets": ["alpha"]}],
        output_path=str(out),
    )

    assert result["ok"] is True
    assert out.is_file()


def test_dry_run_skips_image_gen(monkeypatch) -> None:
    from deskpet.tools import image_tools

    def fail_generate_images(prompts, **kwargs):
        raise AssertionError("generate_images should not be called")

    monkeypatch.setattr(image_tools, "generate_images", fail_generate_images)

    result = ppt_create(
        [{"layout": "image_full", "title": "封面", "image_prompt": "a teal abstract cover"}],
        dry_run=True,
    )

    assert result["ok"] is True
    assert result.get("dry_run") is True


def test_image_gen_failure_degrades(monkeypatch, tmp_path: Path) -> None:
    from deskpet.tools import image_tools

    def fake_generate_images(prompts, **kwargs):
        return [{"prompt": prompts[0], "path": None, "error": "relay down"}]

    monkeypatch.setattr(image_tools, "generate_images", fake_generate_images)
    out = tmp_path / "fallback.pptx"

    result = ppt_create(
        [{"layout": "image_full", "title": "封面", "image_prompt": "a teal abstract cover"}],
        output_path=str(out),
    )

    assert result["ok"] is True
    assert out.is_file()
