# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""B-2 异步图文 PPT 路径测试: 带 image_prompt 的 deck 走后台,秒回不阻塞。"""
from __future__ import annotations

import json

import pytest

from deskpet.tools import ppt_tools
from deskpet.tools.ppt_tools import _HAS_PPTX, _handle_ppt_create

pytestmark = pytest.mark.skipif(not _HAS_PPTX, reason="python-pptx not installed")


class _FakeWorker:
    def __init__(self, alive=True):
        self._alive = alive
        self.scheduled = []

    def alive(self):
        return self._alive

    def submit_background(self, coro_factory):
        # 不真跑协程(避免真生图);只记录被调度,模拟「已提交」
        self.scheduled.append(coro_factory)
        return self._alive


def test_async_path_returns_generating_immediately(monkeypatch):
    """有 image_prompt + worker alive → 秒回 generating,不调 ppt_create(同步)。"""
    called = {"sync": False}
    monkeypatch.setattr(
        ppt_tools, "ppt_create",
        lambda *a, **k: called.__setitem__("sync", True) or {"ok": True},
    )
    worker = _FakeWorker(alive=True)
    out = _handle_ppt_create({
        "outline": [{"layout": "image_full", "title": "封面", "image_prompt": "a city"}],
        "_image_worker": worker,
        "_session_id": "s1",
    }, "")
    data = json.loads(out)
    assert data["status"] == "generating"
    assert len(worker.scheduled) == 1
    # 同步 ppt_create 不应在主调用里被触发(真生成在后台协程内)
    assert called["sync"] is False


def test_no_image_prompt_runs_sync(monkeypatch):
    """没有 image_prompt → 走同步快路径(模板/文本 deck 本就快)。"""
    monkeypatch.setattr(
        ppt_tools, "ppt_create",
        lambda *a, **k: {"ok": True, "path": "x.pptx", "slide_count": 1},
    )
    worker = _FakeWorker(alive=True)
    out = _handle_ppt_create({
        "outline": [{"layout": "bullet", "title": "要点", "bullets": ["A"]}],
        "_image_worker": worker,
        "_session_id": "s1",
    }, "")
    data = json.loads(out)
    assert data.get("ok") is True
    assert data.get("status") != "generating"
    assert worker.scheduled == []


def test_worker_dead_falls_back_to_sync(monkeypatch):
    """worker 不在 → 带 image_prompt 也回退同步(慢但出图,不静默失败)。"""
    monkeypatch.setattr(
        ppt_tools, "ppt_create",
        lambda *a, **k: {"ok": True, "path": "x.pptx", "slide_count": 1},
    )
    worker = _FakeWorker(alive=False)
    out = _handle_ppt_create({
        "outline": [{"layout": "image_full", "title": "封面", "image_prompt": "a city"}],
        "_image_worker": worker,
        "_session_id": "s1",
    }, "")
    data = json.loads(out)
    assert data.get("status") != "generating"
    assert data.get("ok") is True


def test_async_disabled_runs_sync(monkeypatch):
    """config [ppt].async_enabled=false → 同步。"""
    monkeypatch.setattr(ppt_tools, "_ppt_async_enabled", lambda: False)
    monkeypatch.setattr(
        ppt_tools, "ppt_create",
        lambda *a, **k: {"ok": True, "path": "x.pptx", "slide_count": 1},
    )
    worker = _FakeWorker(alive=True)
    out = _handle_ppt_create({
        "outline": [{"layout": "image_full", "title": "封面", "image_prompt": "a city"}],
        "_image_worker": worker,
        "_session_id": "s1",
    }, "")
    data = json.loads(out)
    assert data.get("status") != "generating"
    assert worker.scheduled == []
