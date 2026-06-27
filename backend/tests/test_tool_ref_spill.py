# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""2026-06-13 上下文外置: ref store 磁盘 spill —— LRU 淘汰/重启后
ref 仍可取回(压缩摘要里的 ref_id 不失效)。"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent.tool_result_truncator as trunc
from agent.tool_result_truncator import ToolResultRefStore


@pytest.fixture()
def spill_dir(monkeypatch, tmp_path):
    """把 _spill_dir 指到 tmp(绕过 pytest 内默认禁用)。"""
    d = tmp_path / "tool_refs"
    d.mkdir()
    monkeypatch.setattr(trunc, "_spill_dir", lambda: d)
    return d


def test_put_spills_to_disk(spill_dir):
    store = ToolResultRefStore(max_entries=4)
    ref = store.put("hello full body " * 100)
    assert (spill_dir / f"{ref}.txt").is_file()
    assert (spill_dir / f"{ref}.txt").read_text(encoding="utf-8").startswith("hello full body")


def test_lru_evicted_ref_recovered_from_disk(spill_dir):
    """内存 LRU 淘汰后 → get() 读盘回填,取回成功(旧版返回 None)。"""
    store = ToolResultRefStore(max_entries=2)
    r1 = store.put("first-body")
    store.put("second")
    store.put("third")  # r1 被内存淘汰
    assert r1 not in store._store
    assert store.get(r1) == "first-body"  # 从盘回填
    assert r1 in store._store  # 回填进内存


def test_restart_simulation_new_store_reads_old_refs(spill_dir):
    """模拟重启: 新 store 实例读旧 ref(盘上仍在) → 取回 + slice。"""
    ref = ToolResultRefStore().put("persisted across restart")
    fresh = ToolResultRefStore()
    assert fresh.get(ref) == "persisted across restart"
    assert fresh.get(ref, start=0, end=9) == "persisted"


def test_unknown_ref_still_none(spill_dir):
    assert ToolResultRefStore().get("nonexist") is None


def test_spill_capacity_prunes_oldest(spill_dir, monkeypatch):
    monkeypatch.setattr(trunc, "_SPILL_MAX_FILES", 5)
    store = ToolResultRefStore(max_entries=100)
    refs = [store.put(f"body-{i}") for i in range(9)]
    files = list(spill_dir.glob("*.txt"))
    assert len(files) <= 5
    # 最新的还在
    assert (spill_dir / f"{refs[-1]}.txt").is_file()


def test_pytest_guard_disables_spill_by_default(tmp_path):
    """不 monkeypatch 时(pytest 进程内) _spill_dir 返回 None → 纯内存。"""
    assert trunc._spill_dir() is None


# ── Risk-1 收窄: spill / 内存上限可配 ─────────────────────────────


def test_spill_max_files_param_overrides_default(spill_dir):
    """显式 spill_max_files 参数生效: 按它的值剪,而非默认 400。"""
    store = ToolResultRefStore(max_entries=100, spill_max_files=3)
    assert store._spill_max == 3
    for i in range(8):
        store.put(f"body-{i}")
    assert len(list(spill_dir.glob("*.txt"))) <= 3


def test_env_overrides_spill_and_memory_caps(spill_dir, monkeypatch):
    """env 可调大/调小两个上限(磁盘窗口是真正的丢失边界,现在可配)。"""
    monkeypatch.setenv("DESKPET_TOOL_REF_SPILL_MAX_FILES", "2")
    monkeypatch.setenv("DESKPET_TOOL_REF_MAX_ENTRIES", "7")
    store = ToolResultRefStore()
    assert store._spill_max == 2
    assert store._cap == 7
    for i in range(6):
        store.put(f"b-{i}")
    assert len(list(spill_dir.glob("*.txt"))) <= 2


def test_env_invalid_or_missing_falls_back_to_default(monkeypatch):
    """非法/缺失 env → 回落默认(256 / 400),不崩。"""
    monkeypatch.setenv("DESKPET_TOOL_REF_SPILL_MAX_FILES", "not-a-number")
    monkeypatch.setenv("DESKPET_TOOL_REF_MAX_ENTRIES", "-5")
    store = ToolResultRefStore()
    assert store._spill_max == trunc._SPILL_MAX_FILES  # 400
    assert store._cap == 256


def test_explicit_param_beats_env(spill_dir, monkeypatch):
    """显式参数优先级高于 env。"""
    monkeypatch.setenv("DESKPET_TOOL_REF_SPILL_MAX_FILES", "999")
    store = ToolResultRefStore(spill_max_files=4)
    assert store._spill_max == 4
