"""TG-6 — artifact 默认保存路径 + title_slug 规则（WI-T1.5）。

PRD §3 D4 v2.1：
  <artifact_dir>/<YYYY-MM-DD>/<tool_name>/<title_slug>-<8hex>.<ext>

title_slug 关键点：保留中文 + 常用 emoji；非法 FS 字符替换为 '-'；
NFC 归一化；截至 60 grapheme；空 → 'untitled'。
"""
from __future__ import annotations

import asyncio
import time
import unicodedata
from pathlib import Path

import pytest

from deskpet.tools.office_paths import (
    artifact_default_path,
    auto_temp_path,
    title_slug,
)


# ─── T6-1 默认路径模式 ─────────────────────────────────────

def test_t6_1_default_pattern_under_artifact_dir(tmp_path):
    """artifact_dir 非空 → <dir>/<YYYY-MM-DD>/<tool>/<slug>-<8hex>.<ext>。"""
    p = artifact_default_path(
        tool_name="ppt_create",
        title="demo deck",
        ext="pptx",
        artifact_dir=str(tmp_path),
    )
    # 日期目录
    date = time.strftime("%Y-%m-%d")
    assert p.parent.name == "ppt_create"
    assert p.parent.parent.name == date
    assert p.parent.parent.parent == tmp_path.resolve()
    # 文件名结构
    assert p.suffix == ".pptx"
    assert p.stem.startswith("demo-deck-")
    # 8-hex 后缀
    hex_suffix = p.stem.split("-")[-1]
    assert len(hex_suffix) == 8
    assert all(c in "0123456789abcdef" for c in hex_suffix)


def test_t6_1b_empty_artifact_dir_falls_back_to_tempdir():
    """artifact_dir="" → 走旧 auto_temp_path 保持 BC。"""
    p = artifact_default_path(
        tool_name="ppt_create",
        title="demo",
        ext="pptx",
        artifact_dir="",
    )
    # 应在系统 temp 目录下
    import tempfile
    assert str(p).startswith(tempfile.gettempdir())


# ─── T6-2 用户自定义 + ~ 展开 ───────────────────────────────

def test_t6_2_tilde_expanded(tmp_path, monkeypatch):
    """artifact_dir 含 ~ → 展开到 home。"""
    # 用 monkeypatch 让 HOME 指向 tmp_path 避免污染真实 home
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    p = artifact_default_path(
        tool_name="excel_create",
        title="x",
        ext="xlsx",
        artifact_dir="~/MyDeskPet",
    )
    # 期望解析到 <tmp_path>/MyDeskPet/<date>/excel_create/x-<hex>.xlsx
    assert "MyDeskPet" in str(p)
    assert str(p).startswith(str(tmp_path.resolve()))


# ─── T6-3 同名 collision → 不同 hash ────────────────────────

def test_t6_3_collision_distinct_hashes(tmp_path):
    """相同 title 两次调用 → seed 不同 → hash 不同 → 文件名不冲突。"""
    p1 = artifact_default_path(
        tool_name="ppt_create", title="weekly", ext="pptx",
        artifact_dir=str(tmp_path),
    )
    p2 = artifact_default_path(
        tool_name="ppt_create", title="weekly", ext="pptx",
        artifact_dir=str(tmp_path),
    )
    assert p1 != p2  # hash 不同


# ─── T6-4 并发不冲撞 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_t6_4_concurrent_distinct(tmp_path):
    """10 并发生成同 title 路径，全部 unique。"""
    loop = asyncio.get_running_loop()
    paths = await asyncio.gather(*[
        loop.run_in_executor(
            None,
            lambda: artifact_default_path(
                tool_name="ppt_create",
                title="parallel",
                ext="pptx",
                artifact_dir=str(tmp_path),
            ),
        )
        for _ in range(10)
    ])
    assert len(set(str(p) for p in paths)) == 10


# ─── T6-5 title_slug 全测 ───────────────────────────────────

def test_t6_5_slug_zh_emoji_preserved():
    """中文 + emoji 保留，空白 → '-'。"""
    assert title_slug("营销周报 📊") == "营销周报-📊"


def test_t6_5_slug_illegal_fs_chars_replaced():
    """<>:"/\\|?* → '-'；连续 '-' 折叠。"""
    assert title_slug("Q2 / 2026!") == "Q2-2026!"


def test_t6_5_slug_all_illegal_falls_back():
    """全非法字符 → 'untitled'。"""
    assert title_slug("<<<") == "untitled"
    assert title_slug("///") == "untitled"
    assert title_slug("") == "untitled"


def test_t6_5_slug_truncated_to_60():
    """超长 → 截到 60 字符（grapheme 简化版）。"""
    long_title = "a" * 200
    result = title_slug(long_title)
    assert len(result) <= 60


def test_t6_5_slug_nfc_normalized():
    """NFC 标准化：é(U+00E9) vs e+combining acute(U+0065 U+0301) → 同结果。"""
    composed = "café"            # é
    decomposed = "café"    # e + combining acute
    assert unicodedata.normalize("NFC", decomposed) == composed
    assert title_slug(composed) == title_slug(decomposed)


def test_t6_5_slug_collapses_multiple_dashes():
    """连续 '-' / 空白折叠为单个 '-'，去头尾。"""
    assert title_slug("  a  --  b  ") == "a-b"


def test_t6_5_slug_strips_control_chars():
    """\\x00..\\x1f 控制字符也算非法。"""
    assert title_slug("a\x00b\x07c") == "a-b-c"
