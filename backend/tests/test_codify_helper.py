# SPDX-License-Identifier: BUSL-1.1
"""方案 B：_maybe_codify_skill helper（codify 抽出，FinalEvent + ErrorEvent 两处调）。

真机诊断：原 codify hook 只 inline 在 FinalEvent 分支 → turn 经 ErrorEvent /
迭代上限 / relay ReadError 中止结束时整段不跑 → 多工具 turn 跑了 ≥5 工具但
技能卡不弹。抽成 helper 后两处都调，turn 任意路径结束都能触发。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main import _maybe_codify_skill
from deskpet.agent.tool_path import ToolPathRecorder


def _cfg(enabled: bool):
    return SimpleNamespace(skills=SimpleNamespace(codify=SimpleNamespace(enabled=enabled)))


def _svc(overrides=None):
    """Minimal ServiceContext-like stub with get()."""
    store = {
        "tool_path_recorder": None,
        "skill_loader": None,
        "skill_candidate_store": None,
        "session_goal_store": None,
        "llm_registry": None,
    }
    store.update(overrides or {})
    return SimpleNamespace(get=lambda k: store.get(k))


@pytest.mark.asyncio
async def test_codify_helper_noop_when_flag_off():
    """flag off → 直接 return，不碰任何 service、不发 ws（字节级 BC）。"""
    ws = SimpleNamespace(send_json=AsyncMock())
    await _maybe_codify_skill(_svc(), _cfg(False), "sid", ws, None)
    ws.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_codify_helper_noop_when_recorder_none():
    """flag on 但 recorder 未注册（None）→ return，不崩、不发 ws。"""
    ws = SimpleNamespace(send_json=AsyncMock())
    await _maybe_codify_skill(_svc(), _cfg(True), "sid", ws, None)
    ws.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_codify_helper_noop_when_no_steps():
    """recorder 有但本 run 无录步（trivial turn）→ complete() steps=[] → 不提候选。"""
    rec = ToolPathRecorder()  # _active empty
    ws = SimpleNamespace(send_json=AsyncMock())
    svc = _svc({"tool_path_recorder": rec, "skill_candidate_store": MagicMock()})
    await _maybe_codify_skill(svc, _cfg(True), "sid", ws, None)
    ws.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_codify_helper_proposes_and_emits_when_steps_present():
    """≥1 录步 + 全 service 就位 + propose 返 cid → 发 skill_candidate_proposed。
    覆盖方案 B 核心：helper 能独立完成 complete→propose→emit（无论从哪个分支调）。"""
    rec = ToolPathRecorder()
    for n in ["todo_write", "list_dir", "read_file", "list_dir", "read_file"]:
        rec.record_tool("sid", name=n, ok=True)
    candidate_store = MagicMock()
    candidate_store.fetch_pending = AsyncMock(return_value={
        "name": "分析项目", "description": "5步分析", "steps": ["a", "b"],
    })
    loader = SimpleNamespace(_dirs=["builtin_dir", "/user/skills"])
    llm = MagicMock()
    llm.chat_with_fallback = AsyncMock()
    ws = SimpleNamespace(send_json=AsyncMock())
    svc = _svc({
        "tool_path_recorder": rec,
        "skill_candidate_store": candidate_store,
        "skill_loader": loader,
        "llm_registry": llm,
    })

    import main as _main
    # 桩掉 SkillCodifier.propose → 返回 cid，避免真 LLM 调用
    import deskpet.skills.skill_codifier as _codmod
    orig = _codmod.SkillCodifier.propose
    _codmod.SkillCodifier.propose = AsyncMock(return_value=7)
    try:
        await _maybe_codify_skill(svc, _cfg(True), "sid", ws, None)
    finally:
        _codmod.SkillCodifier.propose = orig

    # 发出了 skill_candidate_proposed 事件
    assert ws.send_json.await_count == 1
    evt = ws.send_json.await_args[0][0]
    assert evt["type"] == "skill_candidate_proposed"
    assert evt["payload"]["candidate_id"] == 7
    assert evt["payload"]["session_id"] == "sid"


@pytest.mark.asyncio
async def test_codify_helper_returns_promptly_confirm_in_background():
    """Bug#2 修复 (2026-06-11)：confirm 等待拆独立 task。

    原 300s Future-await 内联在 chat task → chat_v2_final 已发但 task 还
    挂着;下一条消息的同 sid 抢占 cancel 把候选 Future 连带杀死(卡点击
    无响应、candidate 永久 pending)。修复后:helper 在 propose+emit 后
    立即返回;Future 之后 resolve 时 confirm 仍在后台被调用。
    """
    import asyncio
    from deskpet.skills.skill_codifier import SkillCandidateWaiters

    rec = ToolPathRecorder()
    for n in ["todo_write", "list_dir", "read_file", "list_dir", "read_file"]:
        rec.record_tool("sid", name=n, ok=True)
    candidate_store = MagicMock()
    candidate_store.fetch_pending = AsyncMock(return_value={
        "name": "n", "description": "d", "steps": ["a"],
    })
    loader = SimpleNamespace(_dirs=["builtin_dir", "/user/skills"])
    llm = MagicMock()
    llm.chat_with_fallback = AsyncMock()
    ws = SimpleNamespace(send_json=AsyncMock())
    svc = _svc({
        "tool_path_recorder": rec,
        "skill_candidate_store": candidate_store,
        "skill_loader": loader,
        "llm_registry": llm,
    })
    waiters = SkillCandidateWaiters()

    import deskpet.skills.skill_codifier as _codmod
    orig_propose = _codmod.SkillCodifier.propose
    orig_confirm = _codmod.SkillCodifier.confirm
    confirm_mock = AsyncMock()
    _codmod.SkillCodifier.propose = AsyncMock(return_value=9)
    _codmod.SkillCodifier.confirm = confirm_mock
    try:
        # 关键断言1:helper 必须在 ~0s 内返回(不等 300s confirm)
        await asyncio.wait_for(
            _maybe_codify_skill(svc, _cfg(True), "sid", ws, waiters),
            timeout=2.0,
        )
        ws.send_json.assert_called_once()
        confirm_mock.assert_not_called()  # 还没人裁决

        # 关键断言2:之后 resolve(模拟用户点忽略) → 后台 task 调 confirm
        waiters.resolve(9, "reject")
        await asyncio.sleep(0.05)  # 让后台 task 跑完
        confirm_mock.assert_awaited_once()
        assert confirm_mock.await_args.kwargs.get("accept") is False
    finally:
        _codmod.SkillCodifier.propose = orig_propose
        _codmod.SkillCodifier.confirm = orig_confirm
