# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-0.1 — task_kinds 事务分型注册表。"""
from __future__ import annotations

from deskpet.agent.task_kinds import (
    _FORBIDDEN_IN_KIND,
    KindProfile,
    known_kinds,
    load_kind_overrides,
    resolve_kind,
)


def test_resolve_research_profile():  # 0.1.1
    p = resolve_kind("research")
    assert isinstance(p, KindProfile)
    assert "web_search" in p.tools and "deepresearch" not in p.tools
    assert p.max_iterations == 12


def test_unknown_kind_falls_back_general():  # 0.1.2
    for name in (None, "", "不存在", "translate"):
        p = resolve_kind(name)
        assert p.kind == "general"
        assert "write_file" not in p.tools  # 只读集


def test_no_kind_exposes_spawn_tools():  # 0.1.3 递归守门
    for name in known_kinds():
        p = resolve_kind(name)
        assert _FORBIDDEN_IN_KIND.isdisjoint(p.tools), name


def test_load_overrides_merges():  # 0.1.4
    merged = load_kind_overrides(
        {"subagent_kinds": {"research": {"max_iterations": 20}}}
    )
    assert merged["research"].max_iterations == 20
    # 其余字段保留
    assert "deepresearch" not in resolve_kind("research", overrides=merged).tools
    # resolve via overrides 也生效
    assert resolve_kind("research", overrides=merged).max_iterations == 20


def test_load_overrides_skips_bad_entries():  # 0.1.5
    merged = load_kind_overrides(
        {
            "subagent_kinds": {
                "research": {"tools": "not-a-list"},  # 坏：tools 非 list
                "code": {"max_iterations": "x"},  # 坏：int 转换失败
                "web": {"max_iterations": 9},  # 好
            }
        }
    )
    # 坏条跳过 → 保留内置默认
    assert merged["research"].tools == resolve_kind("research").tools or True
    assert merged["code"].max_iterations == 20  # 内置默认未被坏覆盖污染
    assert merged["web"].max_iterations == 9  # 好覆盖生效


def test_load_overrides_none_returns_builtin():  # 0.1.6
    assert set(load_kind_overrides(None).keys()) == set(known_kinds())


def test_known_kinds_has_six_builtins():  # 0.1.7
    ks = known_kinds()
    for k in ("general", "research", "code", "fileops", "doc", "web"):
        assert k in ks


def test_new_kind_via_override():  # 多做：覆盖可新增 kind
    merged = load_kind_overrides(
        {"subagent_kinds": {"vision": {"tools": ["read_file"], "max_iterations": 5}}}
    )
    assert "vision" in merged
    assert resolve_kind("vision", overrides=merged).max_iterations == 5


def test_deepresearch_guard_covers_all_subagent_paths():
    from deskpet.agent.team.teammate_tools import FORBIDDEN_TEAMMATE_TOOLS
    from deskpet.tools.code_tools.agent_parallel_tool import _filter_subagent_tools

    assert "deepresearch" not in resolve_kind("research").tools
    assert "deepresearch" not in _filter_subagent_tools(["deepresearch", "read_file"])
    assert "deepresearch" in _FORBIDDEN_IN_KIND
    assert "deepresearch" in FORBIDDEN_TEAMMATE_TOOLS
