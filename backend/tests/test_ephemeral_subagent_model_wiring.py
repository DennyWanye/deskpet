# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""shipped-bug fix — [tools.verifier].ephemeral_subagent_model 真正生效.

Bug（2026-06-22 R2 对抗审查发现）：自我纠错闭环在 verify-gate 失败/停滞时
升级到 ephemeral 子代理重新校验，但 main.build_agent 注入 ephemeral verifier
时直接用 ``local_llm or cloud_llm``，**从未读 config.ephemeral_subagent_model**。
结果：用户/默认配的 ephemeral 专用模型完全不生效，永远复用主 LLM。

修复：新增 ``main._resolve_ephemeral_provider(base, model_name)`` —— 按配置
克隆出专用 model 的 provider（中转站按 model id 路由）；缺省/解析失败回退主 LLM。

两层证据：
  1. 直接单测解析器 ``_resolve_ephemeral_provider`` 的 4 个分支（核心修复逻辑）。
  2. 集成测 build_agent 真把配的模型接到 ephemeral verifier 的 LLM 调用上。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from providers.openai_compatible import OpenAICompatibleProvider


def _base_provider(model: str = "gpt-5.5") -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://relay.example.com/v1",
        api_key="tsk_test_key",
        model=model,
        temperature=0.2,
    )


# ─── Layer 1: 解析器单测 ──────────────────────────────────────────────


def test_resolve_overrides_model_reusing_connection():
    """配了不同模型 → 克隆出该模型的 provider，复用 base 的连接参数."""
    from main import _resolve_ephemeral_provider

    base = _base_provider("gpt-5.5")
    out = _resolve_ephemeral_provider(base, "sonnet")

    assert out is not base
    assert out.model == "sonnet"               # 真用配的模型
    assert out.base_url == base.base_url        # 复用同一中转站
    assert out.api_key == base.api_key          # 复用同一 key
    assert out.temperature == base.temperature


def test_resolve_empty_model_falls_back_to_base():
    """缺省（空串）→ 回退主 LLM（保持旧行为 = BC）."""
    from main import _resolve_ephemeral_provider

    base = _base_provider("gpt-5.5")
    assert _resolve_ephemeral_provider(base, "") is base
    assert _resolve_ephemeral_provider(base, "   ") is base


def test_resolve_same_model_returns_base():
    """配的模型 == 主 LLM 模型 → 免重复构造，直接返回 base."""
    from main import _resolve_ephemeral_provider

    base = _base_provider("haiku")
    assert _resolve_ephemeral_provider(base, "haiku") is base


def test_resolve_none_base_returns_none():
    """base=None（用户离线/未配 LLM）→ None，调用方整体跳过 ephemeral."""
    from main import _resolve_ephemeral_provider

    assert _resolve_ephemeral_provider(None, "sonnet") is None


def test_resolve_clone_failure_falls_back_to_base():
    """克隆抛异常 → 兜底回退 base（绝不让 verify-gate 接电崩）."""
    from main import _resolve_ephemeral_provider

    broken = MagicMock(name="broken_provider")
    broken.model = "gpt-5.5"
    # base_url 取属性时抛 → 触发 except 分支
    type(broken).base_url = property(
        lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = _resolve_ephemeral_provider(broken, "sonnet")
    assert out is broken  # 回退


# ─── Layer 2: build_agent 集成接电 ────────────────────────────────────


@dataclass
class _VerifierStub:
    verify_gate_mode: str = "shadow"
    emit_receipts: bool = True
    claim_patterns_file: str = "verify/claim_patterns.yaml"
    max_verify_nudges: int = 2
    extractor_fallback_enabled: bool = True
    ephemeral_subagent_model: str = "haiku"
    run_build: bool = False
    run_tests: bool = False
    structured_reflection: bool = False
    external_evaluator: bool = False
    evaluator_provider: str = "default"


@dataclass
class _LastMileStub:
    artifact_dir_retention_days: int = 30


@dataclass
class _ToolsStub:
    verifier: _VerifierStub = field(default_factory=_VerifierStub)
    last_mile: _LastMileStub = field(default_factory=_LastMileStub)


@dataclass
class _CfgStub:
    tools: _ToolsStub = field(default_factory=_ToolsStub)
    raw: dict = field(default_factory=dict)


@pytest.fixture
def capture_ephemeral_provider(monkeypatch):
    """Patch main.local_llm to a known base + record providers passed to
    _make_str_llm_call with max_tokens==256 (the ephemeral verifier call)."""
    import main

    base = _base_provider("BASE-MODEL-gpt-5.5")
    monkeypatch.setattr(main, "local_llm", base, raising=False)
    monkeypatch.setattr(main, "cloud_llm", None, raising=False)

    recorded: list = []
    orig = main._make_str_llm_call

    def _spy(provider, *, max_tokens: int = 512):
        if max_tokens == 256:
            recorded.append(provider)
        return orig(provider, max_tokens=max_tokens)

    monkeypatch.setattr(main, "_make_str_llm_call", _spy)
    return base, recorded


def _build(cfg):
    from main import build_agent

    rs = MagicMock(name="ReceiptStore")
    rs.load_session.return_value = []
    return build_agent(
        cfg,
        llm_registry=MagicMock(),
        tool_registry=MagicMock(),
        context_manager=MagicMock(),
        receipt_store_getter=lambda: rs,
    )


def test_build_agent_ephemeral_uses_configured_model(capture_ephemeral_provider):
    """配 ephemeral_subagent_model="sonnet" → ephemeral verifier 真用 sonnet."""
    base, recorded = capture_ephemeral_provider
    cfg = _CfgStub()
    cfg.tools.verifier.ephemeral_subagent_model = "sonnet"

    agent = _build(cfg)

    assert agent.verify_gate is not None
    assert recorded, "ephemeral verifier (max_tokens=256) 未被构造 — 接电断了"
    eph = recorded[-1]
    assert eph.model == "sonnet"              # 用配的模型，不是主 LLM
    assert eph is not base
    assert eph.base_url == base.base_url       # 仍走同一中转站


def test_build_agent_ephemeral_falls_back_when_unset(capture_ephemeral_provider):
    """ephemeral_subagent_model 空 → 回退主 LLM（旧行为）."""
    base, recorded = capture_ephemeral_provider
    cfg = _CfgStub()
    cfg.tools.verifier.ephemeral_subagent_model = ""

    agent = _build(cfg)

    assert agent.verify_gate is not None
    assert recorded, "ephemeral verifier 未被构造"
    assert recorded[-1] is base               # 回退主 LLM verbatim
