# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ppt_template_picker 测试 — 库结构原语 + 预览图视觉选模板(降级路径)。

用 tmp_path 造小型假库(env DESKPET_PPT_TEMPLATE_ROOT 指向它),不依赖真
2.8GB 库;vision 用 monkeypatch 强制不可用 → 走随机回退,断言从不抛异常。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import ppt_template_picker as picker


def _has_pptx() -> bool:
    try:
        import pptx  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _has_pptx(), reason="python-pptx not installed")


def _save_pptx(path: Path) -> Path:
    from pptx import Presentation

    Presentation().save(path)
    return path


def _save_img(path: Path, *, fmt: str) -> Path:
    from PIL import Image

    Image.new("RGB", (320, 200), (180, 120, 60)).save(path, format=fmt)
    return path


@pytest.fixture()
def fake_lib(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """造假库: 测试彩(a.pptx+a.jpg, b.pptx+b.png), 测试简(c.pptx 无预览图)。"""
    lib = tmp_path / "PPT_Template"
    cat1 = lib / "01 测试彩"
    cat2 = lib / "02 测试简"
    (cat1 / "预览图").mkdir(parents=True)
    (cat2 / "预览图").mkdir(parents=True)
    _save_pptx(cat1 / "a.pptx")
    _save_pptx(cat1 / "b.pptx")
    _save_img(cat1 / "预览图" / "a.jpg", fmt="JPEG")
    _save_img(cat1 / "预览图" / "b.png", fmt="PNG")
    _save_pptx(cat2 / "c.pptx")  # 故意不给预览图
    monkeypatch.setenv("DESKPET_PPT_TEMPLATE_ROOT", str(lib))
    return lib


def test_list_categories(fake_lib: Path) -> None:
    cats = {c["key"]: c["count"] for c in picker.list_categories()}
    assert cats == {"测试彩": 2, "测试简": 1}


def test_category_pptx_and_normalize(fake_lib: Path) -> None:
    names = sorted(p.name for p in picker.category_pptx("测试彩"))
    assert names == ["a.pptx", "b.pptx"]
    # 传原始带编号目录名也认
    assert len(picker.category_pptx("01 测试彩")) == 2
    assert picker.category_pptx("不存在") == []


def test_preview_for_stem_mapping(fake_lib: Path) -> None:
    a = next(p for p in picker.category_pptx("测试彩") if p.stem == "a")
    b = next(p for p in picker.category_pptx("测试彩") if p.stem == "b")
    c = picker.category_pptx("测试简")[0]
    assert picker.preview_for(a).suffix == ".jpg"
    assert picker.preview_for(b).suffix == ".png"
    assert picker.preview_for(c) is None  # 缺预览图


def test_is_category(fake_lib: Path) -> None:
    assert picker.is_category("测试彩") is True
    assert picker.is_category("01 测试彩") is True
    assert picker.is_category("不存在") is False
    assert picker.is_category(None) is False


def test_build_contact_sheets_produces_valid_image(fake_lib: Path) -> None:
    from PIL import Image

    pairs = [(1, picker.preview_for(p)) for p in picker.category_pptx("测试彩")]
    sheets = picker._build_contact_sheets(pairs, cols=2, rows=1)
    assert sheets, "应至少拼出一张 sheet"
    with Image.open(sheets[0]) as im:
        assert im.width > 0 and im.height > 0


def test_pick_falls_back_to_random_when_no_vision(
    fake_lib: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vision 不可用 → 随机回退该类一套带预览图的模板,从不抛异常。"""
    import deskpet.tools.ppt_visual_review as vr

    monkeypatch.setattr(vr, "vision_chat", lambda *a, **k: "")
    picked = picker.pick_template_by_preview("测试彩", "新能源主题", seed=0)
    assert picked is not None
    assert picked.suffix == ".pptx"
    assert picked.stem in {"a", "b"}


def test_pick_uses_vision_choice(
    fake_lib: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vision 返回 {"id": N} → 映射回对应 .pptx。"""
    import deskpet.tools.ppt_visual_review as vr

    # 强制选 id=2(按枚举顺序对应 category_pptx 的第 2 套)
    monkeypatch.setattr(vr, "vision_chat", lambda *a, **k: '{"id": 2}')
    pairs = picker.category_pptx("测试彩")
    expected = pairs[1]  # id 是 1-based,id=2 → 第二套
    picked = picker.pick_template_by_preview("测试彩", "主题", seed=0)
    assert picked == expected


def test_pick_empty_category_returns_none(fake_lib: Path) -> None:
    assert picker.pick_template_by_preview("不存在的类", "主题") is None


def test_pick_category_without_previews_still_returns_pptx(
    fake_lib: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """该类有 pptx 但都没预览图 → 仍回退返回一套 pptx(不为 None)。"""
    import deskpet.tools.ppt_visual_review as vr

    monkeypatch.setattr(vr, "vision_chat", lambda *a, **k: "")
    picked = picker.pick_template_by_preview("测试简", "主题", seed=0)
    assert picked is not None and picked.stem == "c"


def test_external_absent_falls_back_to_bundled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """外部大库不可用(env 指向不存在)→ 回退到随仓库提交的 bundled 兜底库。"""
    monkeypatch.setenv("DESKPET_PPT_TEMPLATE_ROOT", str(tmp_path / "does-not-exist"))
    root = picker.template_library_root()
    assert root is not None and root.name == "ppt_templates"
    keys = {c["key"] for c in picker.list_categories()}
    assert "通用商务" in keys, f"兜底类缺失: {keys}"


def test_truly_empty_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """外部库不可用 + bundled 也被屏蔽 → 真正为空。"""
    monkeypatch.setenv("DESKPET_PPT_TEMPLATE_ROOT", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(picker, "_bundled_fallback_root", lambda: None)
    assert picker.list_categories() == []
    assert picker.pick_template_by_preview("任何", "主题") is None


def test_bundled_fallback_has_business_templates() -> None:
    """随仓库提交的兜底库应有「通用商务」类且 ≥3 套带预览图。"""
    root = picker._bundled_fallback_root()
    assert root is not None, "bundled 兜底库缺失"
    # 此测试不设 env;有外部大库时 template_library_root 返回外部库,故直接查 bundled root。
    cats = [d for d in root.iterdir() if d.is_dir() and any(d.glob("*.pptx"))]
    assert cats, "兜底库无任何含 pptx 的大类"
    biz = next((d for d in cats if picker._normalize_category(d.name) == "通用商务"), None)
    assert biz is not None
    n_pptx = len(list(biz.glob("*.pptx")))
    n_prev = len(list((biz / "预览图").glob("*"))) if (biz / "预览图").is_dir() else 0
    assert n_pptx >= 3, f"兜底模板数={n_pptx}"
    assert n_prev >= 3, f"兜底预览图数={n_prev}"
