"""TG-3 T3-2 — ppt_create dry_run outline 预览（WI-T1.6）。

PRD §3 D9 + plans/2026-05-23-tool-last-mile-upgrade/01-TDD.md §B TG-3 T3-2。

dry_run=True 时不调 python-pptx、不写文件，仅返回 outline markdown 作为
text artifact（走 D1 显式 emit 路径）。
"""
from __future__ import annotations

from deskpet.tools.ppt_tools import ppt_create


OUTLINE = [
    {"layout": "title", "title": "Demo Deck", "subtitle": "WI-T1.6"},
    {"layout": "bullet", "title": "Section 1", "bullets": ["alpha", "beta", "gamma"]},
    {"layout": "section", "title": "End"},
]


def test_t3_2_dry_run_returns_text_artifact_no_file(tmp_path):
    result = ppt_create(OUTLINE, dry_run=True, title="Demo Deck")
    assert result["ok"] is True
    assert result.get("dry_run") is True
    assert result["slide_count"] == 3
    # **不**含 path 字段（没写文件）
    assert "path" not in result
    # 显式 emit artifacts[]，单条 kind=text
    arts = result.get("artifacts")
    assert isinstance(arts, list) and len(arts) == 1
    art = arts[0]
    assert art["kind"] == "text"
    assert "Demo Deck" in art["preview"]
    assert "alpha" in art["preview"]


def test_t3_2_normal_mode_still_writes_file_when_pptx_available(tmp_path):
    """非 dry_run + 有 python-pptx → 行为不变（BC）。"""
    from deskpet.tools.ppt_tools import _HAS_PPTX
    if not _HAS_PPTX:
        import pytest
        pytest.skip("python-pptx not installed")
    out = tmp_path / "demo.pptx"
    result = ppt_create(OUTLINE, output_path=str(out), title="Demo")
    assert result["ok"] is True
    assert result["path"] == str(out)
    assert out.exists() and out.stat().st_size > 0


def test_t3_2_dry_run_empty_outline_returns_error():
    """空 outline + dry_run=True → 走原 'outline parse failed' 错误路径
    （dry_run 不应让无效 outline 通过）。"""
    result = ppt_create([], dry_run=True)
    assert result["ok"] is False
    assert "outline parse" in result["error"]


def test_t3_2_dry_run_works_without_python_pptx(monkeypatch):
    """dry_run 不依赖 python-pptx — 即使 _HAS_PPTX=False 也能预览。"""
    import deskpet.tools.ppt_tools as ppt_mod
    monkeypatch.setattr(ppt_mod, "_HAS_PPTX", False)
    result = ppt_create(OUTLINE, dry_run=True, title="Preview")
    # 关键：_HAS_PPTX=False 时 dry_run 路径仍 ok=True（不走 missing_dependency）
    assert result["ok"] is True
    assert result.get("dry_run") is True
    arts = result.get("artifacts")
    assert arts and arts[0]["kind"] == "text"
