# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for superpowers Layer 1B PreferenceMemory."""
from __future__ import annotations

import math

import pytest

from deskpet.agent.preference_memory import PreferenceMemory, _cosine


def _unit(*xs: float) -> list[float]:
    n = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / n for x in xs]


class _FakeEmbedder:
    """Maps known phrases to fixed unit vectors; unknown → orthogonal-ish."""

    def __init__(self) -> None:
        self._table = {
            "创建文件 a": _unit(1.0, 0.0, 0.0),
            "新建一个文件 b": _unit(0.97, 0.24, 0.0),   # ~ similar to "创建文件"
            "你用什么模型": _unit(0.0, 1.0, 0.0),
            "今天天气如何": _unit(0.0, 0.0, 1.0),
        }

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            out.append(self._table.get(t.strip(), _unit(0.1, 0.1, 0.99)))
        return out


def test_cosine_basic():
    assert _cosine([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert _cosine([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert _cosine([], [1]) == -1.0


@pytest.mark.asyncio
async def test_record_and_match_similar(tmp_path):
    pm = PreferenceMemory(tmp_path / "pref.json", _FakeEmbedder().embed, threshold=0.86)
    assert await pm.record("创建文件 a", "approved", "plan") is True
    # similar phrase (cosine ~0.97) → hit
    hit = await pm.match("新建一个文件 b", "plan")
    assert hit is not None and hit["label"] == "approved"
    assert hit["score"] >= 0.86


@pytest.mark.asyncio
async def test_no_match_dissimilar(tmp_path):
    pm = PreferenceMemory(tmp_path / "pref.json", _FakeEmbedder().embed, threshold=0.86)
    await pm.record("创建文件 a", "approved", "plan")
    # orthogonal phrase → no hit
    assert await pm.match("今天天气如何", "plan") is None


@pytest.mark.asyncio
async def test_kind_isolation(tmp_path):
    pm = PreferenceMemory(tmp_path / "pref.json", _FakeEmbedder().embed, threshold=0.86)
    await pm.record("你用什么模型", "ask", "intent")
    # same text but querying the "plan" kind → no cross-kind match
    assert await pm.match("你用什么模型", "plan") is None
    hit = await pm.match("你用什么模型", "intent")
    assert hit is not None and hit["label"] == "ask"


@pytest.mark.asyncio
async def test_dedup_same_text(tmp_path):
    pm = PreferenceMemory(tmp_path / "pref.json", _FakeEmbedder().embed, threshold=0.86)
    await pm.record("创建文件 a", "approved", "plan")
    await pm.record("创建文件 a", "rejected", "plan")  # overwrite
    entries = pm.list_entries("plan")
    assert len(entries) == 1
    assert entries[0]["label"] == "rejected"


@pytest.mark.asyncio
async def test_persist_reload(tmp_path):
    path = tmp_path / "pref.json"
    pm = PreferenceMemory(path, _FakeEmbedder().embed, threshold=0.86)
    await pm.record("创建文件 a", "approved", "plan")
    # new instance reads from disk
    pm2 = PreferenceMemory(path, _FakeEmbedder().embed, threshold=0.86)
    hit = await pm2.match("新建一个文件 b", "plan")
    assert hit is not None and hit["label"] == "approved"


@pytest.mark.asyncio
async def test_clear(tmp_path):
    pm = PreferenceMemory(tmp_path / "pref.json", _FakeEmbedder().embed, threshold=0.86)
    await pm.record("创建文件 a", "approved", "plan")
    await pm.record("你用什么模型", "ask", "intent")
    assert pm.clear("plan") == 1
    assert pm.list_entries("plan") == []
    assert len(pm.list_entries("intent")) == 1
    assert pm.clear() == 1
    assert pm.list_entries() == []
