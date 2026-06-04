# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TDD T2 — doc_tools.py (doc_create / doc_read / doc_edit)."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import office_paths as op
from deskpet.tools import doc_tools as dt

pytestmark = pytest.mark.skipif(not dt._HAS_DOCX, reason="python-docx not installed")


@pytest.fixture(autouse=True)
def _auth(tmp_path: Path):
    op.clear_authorizations()
    op.authorize_path(tmp_path)
    yield
    op.clear_authorizations()


def _make_doc(tmp_path: Path, elements) -> str:
    out = tmp_path / "src.docx"
    r = dt.doc_create({"elements": elements}, output_path=str(out))
    assert r["ok"], r
    return r["path"]


def test_t2_1_create_basic(tmp_path: Path):
    path = _make_doc(tmp_path, [
        {"type": "heading", "text": "标题", "level": 1},
        {"type": "paragraph", "text": "正文一段"},
    ])
    from docx import Document

    doc = Document(path)
    assert any("正文一段" in p.text for p in doc.paragraphs)


def test_t2_2_create_with_table(tmp_path: Path):
    path = _make_doc(tmp_path, [
        {"type": "table", "rows": [["a", "b"], ["c", "d"]], "header": True},
    ])
    from docx import Document

    doc = Document(path)
    assert len(doc.tables) == 1
    assert len(doc.tables[0].rows) == 2


def test_t2_3_heading_levels(tmp_path: Path):
    path = _make_doc(tmp_path, [
        {"type": "heading", "text": "H1", "level": 1},
        {"type": "heading", "text": "H2", "level": 2},
    ])
    from docx import Document

    doc = Document(path)
    styles = [p.style.name for p in doc.paragraphs]
    assert any("Heading 1" in s for s in styles)
    assert any("Heading 2" in s for s in styles)


def test_t2_4_doc_read_outline(tmp_path: Path):
    path = _make_doc(tmp_path, [
        {"type": "heading", "text": "章节", "level": 1},
        {"type": "paragraph", "text": "段落"},
    ])
    r = dt.doc_read(path)
    assert r["ok"]
    assert len(r["paragraphs"]) >= 2
    assert all("index" in p and "text" in p and "style" in p for p in r["paragraphs"])


def test_t2_5_edit_replace(tmp_path: Path):
    path = _make_doc(tmp_path, [
        {"type": "paragraph", "text": "甲方：旧名"},
        {"type": "paragraph", "text": "乙方：保持不变"},
    ])
    r = dt.doc_edit(path, [{"op": "replace", "find": "旧名", "replace": "新名"}])
    assert r["ok"] and r["applied"] == 1
    from docx import Document

    doc = Document(path)
    texts = [p.text for p in doc.paragraphs]
    assert any("甲方：新名" in t for t in texts)
    assert any("乙方：保持不变" in t for t in texts)


def test_t2_6_edit_replace_scoped_to_index(tmp_path: Path):
    path = _make_doc(tmp_path, [
        {"type": "paragraph", "text": "重复词"},
        {"type": "paragraph", "text": "重复词"},
    ])
    # paragraph_index targets the 2nd paragraph only.
    from docx import Document

    doc = Document(path)
    idx = next(i for i, p in enumerate(doc.paragraphs) if p.text == "重复词")
    second = next(i for i, p in enumerate(doc.paragraphs) if p.text == "重复词" and i > idx)
    r = dt.doc_edit(path, [{"op": "replace", "find": "重复词", "replace": "改了", "paragraph_index": second}])
    assert r["ok"] and r["applied"] == 1
    doc2 = Document(path)
    texts = [p.text for p in doc2.paragraphs]
    assert texts.count("重复词") == 1
    assert "改了" in texts


def test_t2_7_insert_paragraph(tmp_path: Path):
    path = _make_doc(tmp_path, [{"type": "paragraph", "text": "原段"}])
    from docx import Document

    before = len(Document(path).paragraphs)
    r = dt.doc_edit(path, [{"op": "insert_paragraph", "text": "新段", "after_index": 0}])
    assert r["ok"]
    after = len(Document(path).paragraphs)
    assert after == before + 1


def test_t2_8_set_table_cell(tmp_path: Path):
    path = _make_doc(tmp_path, [{"type": "table", "rows": [["a", "b"], ["c", "d"]]}])
    r = dt.doc_edit(path, [{"op": "set_table_cell", "table_index": 0, "row": 1, "col": 1, "text": "改了"}])
    assert r["ok"] and r["applied"] == 1
    from docx import Document

    assert Document(path).tables[0].cell(1, 1).text == "改了"


def test_t2_9_edit_preserves_style(tmp_path: Path):
    path = _make_doc(tmp_path, [
        {"type": "heading", "text": "标题甲", "level": 1},
    ])
    dt.doc_edit(path, [{"op": "replace", "find": "甲", "replace": "乙"}])
    from docx import Document

    doc = Document(path)
    heading = next(p for p in doc.paragraphs if "标题" in p.text)
    assert "Heading 1" in heading.style.name


def test_t2_10_edit_unauthorized_path_rejected(tmp_path: Path):
    path = _make_doc(tmp_path, [{"type": "paragraph", "text": "x"}])
    op.clear_authorizations()  # revoke
    r = dt.doc_edit(path, [{"op": "replace", "find": "x", "replace": "y"}])
    assert not r["ok"] and r["retriable"] is False
    assert "office_pick_file" in r["error"]


def test_t2_11_read_missing_file():
    r = dt.doc_read("Z:\\nope\\missing.docx")
    assert not r["ok"]


def test_t2_12_edit_empty_ops_noop(tmp_path: Path):
    path = _make_doc(tmp_path, [{"type": "paragraph", "text": "x"}])
    r = dt.doc_edit(path, [])
    assert r["ok"] and r["applied"] == 0


def test_t2_13_failing_op_skipped_rest_apply(tmp_path: Path):
    path = _make_doc(tmp_path, [{"type": "paragraph", "text": "命中目标"}])
    r = dt.doc_edit(path, [
        {"op": "replace", "find": "不存在", "replace": "x"},
        {"op": "replace", "find": "命中", "replace": "已改"},
    ])
    assert r["ok"] and r["applied"] == 1
    assert r["ops"][0]["status"] == "skipped"
    assert r["ops"][1]["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────
# 回归 (2026-06-04 工具层严测 TC-11)：LLM 实发**嵌套** element 格式
#   {"heading":{"text":..,"level":..}} / {"paragraph":{"text":..}}
# 旧 _add_element 归一化只认简写字符串({"heading":"文字"})，把整个内层
# dict 当 text → 正文渲染成字面量 "{'text': '标题', 'level': 1}"。
# 修复后内层 dict 应平铺其字段 → 正确提取 text/level。
# ─────────────────────────────────────────────────────────────────────
def test_t2_regression_nested_element_dict_format(tmp_path: Path):
    """嵌套 dict 格式 {heading:{text,level}} 必须提取 text，不能 str(dict)。"""
    path = _make_doc(tmp_path, [
        {"heading": {"text": "团队周报", "level": 1}},
        {"paragraph": {"text": "本周完成了核心开发。"}},
    ])
    from docx import Document

    doc = Document(path)
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    # 必须是干净文本，绝不能出现字面 dict（旧 bug 现象）
    assert "团队周报" in texts, texts
    assert any("本周完成了核心开发" in t for t in texts), texts
    assert not any("{'text'" in t or '{"text"' in t for t in texts), \
        f"BUG 回归：dict 被 str() 进正文 → {texts}"
    # heading 仍应用 Heading 样式、level 生效
    h = next(p for p in doc.paragraphs if p.text.strip() == "团队周报")
    assert "Heading" in h.style.name, h.style.name


def test_t2_regression_shorthand_string_still_works(tmp_path: Path):
    """旧简写格式 {heading:"文字"} / {paragraph:"文字"} 向后兼容不破。"""
    path = _make_doc(tmp_path, [
        {"heading": "简写标题"},
        {"paragraph": "简写正文"},
    ])
    from docx import Document

    doc = Document(path)
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert "简写标题" in texts and "简写正文" in texts, texts
    assert not any("{" in t for t in texts), texts
