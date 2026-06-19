# SPDX-License-Identifier: BUSL-1.1
"""WI-1.6 — ToolPath 录制 + get_completed_path（只录不消费）。"""
from __future__ import annotations

from deskpet.agent.tool_path import ToolPath, ToolPathRecorder


def test_record_and_complete_path():
    rec = ToolPathRecorder()
    rec.record_tool("s1", name="file_read", ok=True)
    rec.record_tool("s1", name="ppt_create", ok=False, recovered=True)
    rec.record_tool("s1", name="ppt_create", ok=True)
    path = rec.complete("s1", goal_id="g1", goal_text="生成 PPT")
    assert isinstance(path, ToolPath)
    assert [s.name for s in path.steps] == ["file_read", "ppt_create", "ppt_create"]
    assert path.steps[1].recovered is True
    assert path.goal_text == "生成 PPT"


def test_get_completed_path_returns_recorded():
    rec = ToolPathRecorder()
    rec.record_tool("s1", name="x", ok=True)
    rec.complete("s1", goal_id="g1", goal_text="t")
    got = rec.get_completed_path("s1", "g1")
    assert got is not None
    assert got.goal_id == "g1"


def test_get_completed_path_missing_returns_none():
    rec = ToolPathRecorder()
    assert rec.get_completed_path("s1", "nope") is None


def test_complete_clears_active_buffer():
    rec = ToolPathRecorder()
    rec.record_tool("s1", name="x", ok=True)
    rec.complete("s1", goal_id="g1", goal_text="t")
    # 完成后 active buffer 清空，新目标从头录
    rec.record_tool("s1", name="y", ok=True)
    path2 = rec.complete("s1", goal_id="g2", goal_text="t2")
    assert [s.name for s in path2.steps] == ["y"]
