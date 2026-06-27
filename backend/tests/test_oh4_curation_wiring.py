# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-OH-4 / WI-CC-5 — 记忆 self-curation nudge 接线层 wiring 测试.

**为什么存在本文件**（2026-06-23 真机抓出的生产死链）：
``test_memory_curation.py`` 的 12 个单测**直接构造 ``MemoryCurator(...)``** 验逻辑，
全绿；但生产环境 curator **永不构造** —— 出厂 ``config.toml`` 的 ``[memory.v2]`` 段
**漏配 ``curation_nudge`` flag** → dataclass 默认 False → lifespan(main.py:2648) 的
``if config.memory.v2.curation_nudge and ...`` 在 flag=False 处短路 → 永不 register
``memory_curator`` → build_agent 拿到 None → agent_loop 不 fire nudge。单测因绕过
config + lifespan + build_agent 这条**真实接线链**而测不到。

本文件守的是**接线链**，不是 curator 内部逻辑：
  ① 出厂 config.toml 真把 curation_nudge / auto_learnings 点亮（漏配回归守门）。
  ② load_config 真解析这两个 flag（parse 层）。
  ③ build_agent 真把 memory_curator 接到 _AgentLoop（接电证据，照 verify_gate
     P0-1 先例 test_build_agent_verify_wiring.py）。
  ④ curation_nudge_every_n_turns 真透传到 agent_loop 的 _curation_every。
  ⑤ curator=None（默认）→ agent_loop 不 fire（BC）。

不走 ``import main; reload``（monolithic main.py 99% 翻车，见 verify wiring 注释）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import load_config


# 仓库根 config.toml（出厂运行配置 = flag 真正点亮处）。
_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config.toml"


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


# ─── ① 出厂 config 点亮（真因回归守门）────────────────────────────────


def test_factory_config_lights_up_curation_nudge():
    """**核心回归守门**：出厂 config.toml 必须把 curation_nudge 点亮.

    这正是 2026-06-23 死链：flag 漏配 → lifespan if 短路 → curator 永不构造。
    若有人误删/改回 false，本测试立刻红。dataclass 默认 False（保字节级 BC），
    所以**必须靠 config.toml 点亮**，不能改 dataclass 默认。
    """
    assert _REPO_CONFIG.is_file(), f"repo config.toml missing: {_REPO_CONFIG}"
    cfg = load_config(str(_REPO_CONFIG))
    v2 = cfg.memory.v2
    assert v2.curation_nudge is True, (
        "WI-OH-4 出厂死链守门：config.toml [memory.v2] curation_nudge 必须 = true，"
        "否则 lifespan(main.py:2648) if 短路 → MemoryCurator 永不构造 → 生产死链。"
    )
    assert v2.auto_learnings is True, (
        "WI-CC-5 强依赖 OH-4：auto_learnings 也须出厂开，否则 learning category 不产。"
    )
    # every_n 必须是正整数（agent_loop 频率门控基础）。
    assert isinstance(v2.curation_nudge_every_n_turns, int)
    assert v2.curation_nudge_every_n_turns >= 1


# ─── ② parse 层 ─────────────────────────────────────────────────────


def test_load_config_parses_curation_flags(tmp_path):
    """显式 [memory.v2] curation_nudge/auto_learnings/every_n → 正确解析."""
    cfg = load_config(_write(
        tmp_path,
        "[memory]\n[memory.v2]\n"
        "curation_nudge = true\n"
        "curation_nudge_every_n_turns = 2\n"
        "auto_learnings = true\n",
    ))
    v2 = cfg.memory.v2
    assert v2.curation_nudge is True
    assert v2.curation_nudge_every_n_turns == 2
    assert v2.auto_learnings is True


def test_curation_flags_default_false_when_absent(tmp_path):
    """缺 [memory.v2] → 两 flag 默认 False（字节级 BC）。这正是死链的成因：

    出厂 config 漏配时运行时就是这个状态 → 必须靠 test ① 守住出厂值。
    """
    cfg = load_config(_write(tmp_path, "[memory]\nembedding_model = \"bge-m3\"\n"))
    assert cfg.memory.v2.curation_nudge is False
    assert cfg.memory.v2.auto_learnings is False


# ─── ③④⑤ build_agent → _AgentLoop 接电 ──────────────────────────────


@dataclass
class _VerifierStub:
    verify_gate_mode: str = "off"
    emit_receipts: bool = False
    claim_patterns_file: str = "verify/claim_patterns.yaml"
    max_verify_nudges: int = 2
    extractor_fallback_enabled: bool = True
    ephemeral_subagent_model: str = "haiku"
    run_build: bool = False
    run_tests: bool = False


@dataclass
class _LastMileStub:
    artifact_dir_retention_days: int = 30


@dataclass
class _ToolsStub:
    verifier: _VerifierStub = field(default_factory=_VerifierStub)
    last_mile: _LastMileStub = field(default_factory=_LastMileStub)


@dataclass
class _MemV2Stub:
    curation_nudge_every_n_turns: int = 8


@dataclass
class _MemStub:
    v2: _MemV2Stub = field(default_factory=_MemV2Stub)


@dataclass
class _CfgStub:
    tools: _ToolsStub = field(default_factory=_ToolsStub)
    memory: _MemStub = field(default_factory=_MemStub)


def _build(**kwargs):
    from main import build_agent

    base = dict(
        llm_registry=MagicMock(name="llm_registry"),
        tool_registry=MagicMock(name="tool_registry"),
        context_manager=MagicMock(name="ctx_mgr"),
        receipt_store_getter=lambda: None,
    )
    base.update(kwargs)
    return build_agent(_CfgStub(), **base)


def test_build_agent_wires_memory_curator():
    """**核心接电证据**：build_agent(memory_curator=X) → agent._memory_curator is X.

    lifespan register 的 curator 经 main.py:6924 service_context.get 取出、传给
    build_agent；本测试验工厂这一段真把它接到 _AgentLoop，否则即便 lifespan
    注册成功，agent_loop 仍拿不到 curator → nudge 死。
    """
    sentinel = MagicMock(name="MemoryCurator")
    agent = _build(memory_curator=sentinel)
    assert agent._memory_curator is sentinel, (
        "build_agent 必须把 memory_curator 透传给 _AgentLoop（否则 OH-4 生产 no-op）。"
    )


def test_build_agent_curator_none_is_bc():
    """默认 memory_curator=None → agent._memory_curator is None（BC，不 fire）。"""
    agent = _build()
    assert agent._memory_curator is None


def test_build_agent_passes_curation_every_n():
    """cfg.memory.v2.curation_nudge_every_n_turns 透传到 _curation_every（频率门控）。"""
    cfg = _CfgStub()
    cfg.memory.v2.curation_nudge_every_n_turns = 2
    from main import build_agent

    agent = build_agent(
        cfg,
        llm_registry=MagicMock(),
        tool_registry=MagicMock(),
        context_manager=MagicMock(),
        receipt_store_getter=lambda: None,
        memory_curator=MagicMock(name="curator"),
    )
    assert agent._curation_every == 2, (
        "every_n=2 → 每 2 回合 fire；若漏传则退回默认 8，真机'聊 2 轮'验收不触发。"
    )
