# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FEAT-A2 — /prefs slash 命令后端单测（查看/清除偏好记忆）。

四态覆盖（spec FEAT-A2 完成定义 6a）：
  - list（无 args）→ prefs_list 契约
  - clear（全清）→ prefs_cleared(kind=None)
  - clear by kind（intent/plan）→ prefs_cleared(kind=<str>)
  - flag off（session_pref_memory is None）→ error

锁定返回契约（防跨层漂移）：
  list  → {"type":"prefs_list","entries":[{text,label,kind,ts}],"count":N}
  clear → {"type":"prefs_cleared","removed":N,"kind":<str|null>}
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.commands import dispatch_slash_command, _handle_prefs
from deskpet.agent.preference_memory import PreferenceMemory


def _fake_embed_factory():
    async def _embed(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = abs(hash(t))
            out.append([float(((h >> (i * 4)) & 0xF) + 1) for i in range(8)])
        return out
    return _embed


async def _make_pref(tmp_path: Path) -> PreferenceMemory:
    pref = PreferenceMemory(tmp_path / "pref.json", _fake_embed_factory())
    await pref.record("你用什么模型", "ask", "intent")
    await pref.record("帮我重构登录页", "task", "intent")
    await pref.record("生成 PPT", "approved", "plan")
    return pref


# ---- flag off ----------------------------------------------------------

@pytest.mark.asyncio
async def test_prefs_flag_off_none():
    res = await dispatch_slash_command(
        "prefs", "", "sid", session_pref_memory=None,
    )
    assert res["type"] == "error"
    assert "features.preference_memory" in res["message"]


def test_handle_prefs_none_direct():
    res = _handle_prefs("", None)
    assert res["type"] == "error"
    assert "preference_memory" in res["message"]


# ---- list --------------------------------------------------------------

@pytest.mark.asyncio
async def test_prefs_list(tmp_path: Path):
    pref = await _make_pref(tmp_path)
    res = await dispatch_slash_command(
        "prefs", "", "sid", session_pref_memory=pref,
    )
    assert res["type"] == "prefs_list"
    assert res["count"] == 3
    assert len(res["entries"]) == 3
    # 契约字段齐全
    for e in res["entries"]:
        assert set(e.keys()) == {"text", "label", "kind", "ts"}
    kinds = {e["kind"] for e in res["entries"]}
    assert kinds == {"intent", "plan"}


@pytest.mark.asyncio
async def test_prefs_list_empty(tmp_path: Path):
    pref = PreferenceMemory(tmp_path / "pref.json", _fake_embed_factory())
    res = await dispatch_slash_command(
        "prefs", "", "sid", session_pref_memory=pref,
    )
    assert res["type"] == "prefs_list"
    assert res["count"] == 0
    assert res["entries"] == []


# ---- clear (全清) -------------------------------------------------------

@pytest.mark.asyncio
async def test_prefs_clear_all(tmp_path: Path):
    pref = await _make_pref(tmp_path)
    res = await dispatch_slash_command(
        "prefs", "clear", "sid", session_pref_memory=pref,
    )
    assert res["type"] == "prefs_cleared"
    assert res["removed"] == 3
    assert res["kind"] is None
    # 真清干净
    assert pref.list_entries() == []


# ---- clear by kind ------------------------------------------------------

@pytest.mark.asyncio
async def test_prefs_clear_intent(tmp_path: Path):
    pref = await _make_pref(tmp_path)
    res = await dispatch_slash_command(
        "prefs", "clear intent", "sid", session_pref_memory=pref,
    )
    assert res["type"] == "prefs_cleared"
    assert res["removed"] == 2  # 两条 intent
    assert res["kind"] == "intent"
    # plan 还在
    remaining = pref.list_entries()
    assert len(remaining) == 1
    assert remaining[0]["kind"] == "plan"


@pytest.mark.asyncio
async def test_prefs_clear_plan(tmp_path: Path):
    pref = await _make_pref(tmp_path)
    res = await dispatch_slash_command(
        "prefs", "clear plan", "sid", session_pref_memory=pref,
    )
    assert res["type"] == "prefs_cleared"
    assert res["removed"] == 1
    assert res["kind"] == "plan"


@pytest.mark.asyncio
async def test_prefs_clear_unknown_kind(tmp_path: Path):
    pref = await _make_pref(tmp_path)
    res = await dispatch_slash_command(
        "prefs", "clear bogus", "sid", session_pref_memory=pref,
    )
    assert res["type"] == "error"
    assert "bogus" in res["message"]


@pytest.mark.asyncio
async def test_prefs_unknown_subcommand(tmp_path: Path):
    pref = await _make_pref(tmp_path)
    res = await dispatch_slash_command(
        "prefs", "wat", "sid", session_pref_memory=pref,
    )
    assert res["type"] == "error"


# ---- /help 含 prefs -----------------------------------------------------

@pytest.mark.asyncio
async def test_help_lists_prefs():
    res = await dispatch_slash_command("help", "", "sid", skill_loader=None)
    assert res["type"] == "help"
    names = {b["name"] for b in res["builtins"]}
    assert any(n.startswith("prefs") for n in names)
    assert any(n == "prefs" for n in names)
