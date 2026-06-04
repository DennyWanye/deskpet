# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""FEAT-C2 — superpowers 新代码错误/边角路径单测。

覆盖三块已落地的稳定符号（**不改产品代码**）:

* ``deskpet.agent.preference_memory.PreferenceMemory`` — embed 返回空/抛异常的
  安全降级、``max_entries`` 截断、``list_entries`` / ``clear`` 的 kind 过滤边角。
* ``deskpet.agent.verify_gate.VerifyGate`` — strict nudge 耗尽 →
  ``consult_ephemeral_subagent`` 三态（放行 / ephemeral=None 保守 fail /
  ephemeral 抛异常 fail）。
* ``agent.plan.maybe_extract_plan`` — not in_code_mode / 短消息 → None（且
  provider 不被调用，零副作用）。

并行约束（spec §3）：仅 import 已存在的稳定符号；**不** import A1 新抽的
``_build_intent_hint`` / ``_intent_label_from_turn`` 或 A4 新 sidecar schema。
"""
from __future__ import annotations

import math

import pytest

from deskpet.agent.preference_memory import PreferenceMemory
from deskpet.agent.verify_gate import (
    Claim,
    ClaimPattern,
    RegexExtractor,
    UnmatchedClaim,
    VerifyGate,
    VerifyOutcome,
)
from deskpet.tools.receipt import ToolReceipt
from agent.plan import maybe_extract_plan


def _unit(*xs: float) -> list[float]:
    n = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / n for x in xs]


# ──────────────────────────────────────────────────────────────────
# 1. PreferenceMemory 边角 — embed 失败 / 截断 / 过滤
# ──────────────────────────────────────────────────────────────────


class _EmptyEmbedder:
    """embed 永远返回空 list（模型未就绪 / relay 503）。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class _EmptyVecEmbedder:
    """返回了 list，但每条向量是空（[[]]）— 走 ``not out[0]`` 分支。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class _RaisingEmbedder:
    """embed 抛异常（BGE-M3 OOM / IO 错误）。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedder boom")


class _CountingUnitEmbedder:
    """每条文本映射到一个确定的单位向量，便于截断/计数测试。"""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for t in texts:
            # 用文本 hash 派生 3 维向量，保证不同文本不同向量
            h = abs(hash(t.strip()))
            out.append(_unit((h % 7) + 1.0, ((h // 7) % 5) + 1.0, ((h // 35) % 3) + 1.0))
        return out


@pytest.mark.asyncio
async def test_record_returns_false_when_embed_empty(tmp_path):
    """embed 返回空 list → record 安全返 False，不写入、不崩。"""
    pm = PreferenceMemory(tmp_path / "p.json", _EmptyEmbedder().embed)
    assert await pm.record("创建文件 a", "approved", "plan") is False
    assert pm.list_entries() == []
    # 文件不应被创建（无写入）
    assert not (tmp_path / "p.json").exists()


@pytest.mark.asyncio
async def test_record_returns_false_when_vec_empty(tmp_path):
    """embed 返回 [[]]（向量为空）→ record 返 False。"""
    pm = PreferenceMemory(tmp_path / "p.json", _EmptyVecEmbedder().embed)
    assert await pm.record("创建文件 a", "approved", "plan") is False
    assert pm.list_entries() == []


@pytest.mark.asyncio
async def test_record_returns_false_when_embed_raises(tmp_path):
    """embed 抛异常 → record 吞掉异常返 False（不冒泡崩主流程）。"""
    pm = PreferenceMemory(tmp_path / "p.json", _RaisingEmbedder().embed)
    assert await pm.record("创建文件 a", "approved", "plan") is False
    assert pm.list_entries() == []


@pytest.mark.asyncio
async def test_match_returns_none_when_embed_empty(tmp_path):
    """embed 返回空 → match 安全返 None。"""
    pm = PreferenceMemory(tmp_path / "p.json", _EmptyEmbedder().embed)
    assert await pm.match("创建文件 a", "plan") is None


@pytest.mark.asyncio
async def test_match_returns_none_when_embed_raises(tmp_path):
    """embed 抛异常 → match 返 None（不崩）。"""
    pm = PreferenceMemory(tmp_path / "p.json", _RaisingEmbedder().embed)
    assert await pm.match("创建文件 a", "plan") is None


@pytest.mark.asyncio
async def test_record_blank_text_returns_false(tmp_path):
    """空白/纯空格文本 → _vec 早返 None → record False（且不调 embed）。"""
    emb = _CountingUnitEmbedder()
    pm = PreferenceMemory(tmp_path / "p.json", emb.embed)
    assert await pm.record("   ", "ask", "intent") is False
    assert await pm.record("", "ask", "intent") is False
    assert emb.calls == 0  # 空文本短路，不触达 embedder
    assert pm.list_entries() == []


@pytest.mark.asyncio
async def test_match_blank_text_returns_none(tmp_path):
    pm = PreferenceMemory(tmp_path / "p.json", _CountingUnitEmbedder().embed)
    assert await pm.match("   ", "plan") is None


@pytest.mark.asyncio
async def test_max_entries_truncates_to_latest_n(tmp_path):
    """超过 max_entries → 只保留最新 N 条（_entries[-max:]）。"""
    pm = PreferenceMemory(
        tmp_path / "p.json", _CountingUnitEmbedder().embed, max_entries=3
    )
    for i in range(6):
        assert await pm.record(f"任务 {i}", "task", "intent") is True
    entries = pm.list_entries("intent")
    assert len(entries) == 3, f"expected 3 after truncation, got {len(entries)}"
    # 保留的是最新 3 条 (3,4,5)
    texts = [e["text"] for e in entries]
    assert texts == ["任务 3", "任务 4", "任务 5"]


@pytest.mark.asyncio
async def test_max_entries_reload_persists_truncation(tmp_path):
    """截断后落盘 → 重载实例只看到截断后的 N 条。"""
    path = tmp_path / "p.json"
    pm = PreferenceMemory(path, _CountingUnitEmbedder().embed, max_entries=2)
    for i in range(5):
        await pm.record(f"任务 {i}", "task", "intent")
    pm2 = PreferenceMemory(path, _CountingUnitEmbedder().embed, max_entries=2)
    assert [e["text"] for e in pm2.list_entries()] == ["任务 3", "任务 4"]


@pytest.mark.asyncio
async def test_list_entries_kind_filter_and_shape(tmp_path):
    """list_entries 按 kind 过滤；返回 dict 不含 embedding（仅 4 个键）。"""
    pm = PreferenceMemory(tmp_path / "p.json", _CountingUnitEmbedder().embed)
    await pm.record("派个活", "task", "intent")
    await pm.record("创建文件", "approved", "plan")
    intents = pm.list_entries("intent")
    plans = pm.list_entries("plan")
    assert len(intents) == 1 and len(plans) == 1
    assert intents[0]["label"] == "task"
    assert plans[0]["label"] == "approved"
    # 不泄露 embedding；只暴露 text/label/kind/ts
    assert set(intents[0].keys()) == {"text", "label", "kind", "ts"}
    # 全量
    assert len(pm.list_entries()) == 2
    # 不存在的 kind → 空
    assert pm.list_entries("nope") == []


@pytest.mark.asyncio
async def test_clear_by_nonexistent_kind_returns_zero(tmp_path):
    """clear 一个不存在的 kind → removed=0，其它 kind 不动，不触发落盘改写。"""
    pm = PreferenceMemory(tmp_path / "p.json", _CountingUnitEmbedder().embed)
    await pm.record("派个活", "task", "intent")
    assert pm.clear("plan") == 0  # 没有 plan 条目
    assert len(pm.list_entries("intent")) == 1


@pytest.mark.asyncio
async def test_clear_on_empty_returns_zero(tmp_path):
    """空记忆上 clear → 0，不崩。"""
    pm = PreferenceMemory(tmp_path / "p.json", _CountingUnitEmbedder().embed)
    assert pm.clear() == 0
    assert pm.clear("intent") == 0


# ──────────────────────────────────────────────────────────────────
# 2. VerifyGate strict 耗尽路径 — consult_ephemeral_subagent 三态
# ──────────────────────────────────────────────────────────────────


def _gate(mode="strict", ephemeral=None):
    # 用一个带 tool_hint 的 pattern，让 strict 能真正判 unmatched
    pat = ClaimPattern(
        id="ppt_done",
        regex=r"已生成.*PPT",
        artifact_kind="pptx",
        tool_hint=["ppt_create"],
    )
    return VerifyGate(
        extractor=RegexExtractor([pat]),
        mode=mode,
        ephemeral_subagent=ephemeral,
    )


def _failed_claim() -> UnmatchedClaim:
    return UnmatchedClaim(
        pattern_id="ppt_done",
        raw_text="已生成 PPT",
        expected_kind="pptx",
        expected_path_or_title=None,
        reason="no_receipt",
    )


def test_strict_check_flags_unmatched_when_no_receipt():
    """strict 模式：声称已生成 PPT 但 ledger 空 → 不放行（passed=False）。

    这是 nudge 触发前提：check 失败才会进入 nudge/ephemeral 升级链路。
    """
    gate = _gate()
    outcome = gate.check(assistant_text="我已生成 PPT 文件", ledger=[])
    assert isinstance(outcome, VerifyOutcome)
    assert outcome.passed is False
    assert outcome.claims_extracted == 1
    assert len(outcome.unmatched_claims) == 1
    assert outcome.unmatched_claims[0].reason == "no_receipt"


def test_ephemeral_consult_passes_when_subagent_returns_true():
    """nudge 耗尽 → consult_ephemeral_subagent；ephemeral 判 True → 放行。"""
    called = {}

    def ephemeral(payload):
        called["payload"] = payload
        return True

    gate = _gate(ephemeral=ephemeral)
    verdict = gate.consult_ephemeral_subagent(
        ledger=[],
        failed_claims=[_failed_claim()],
        assistant_text="我已生成 PPT 文件",
    )
    assert verdict is True
    # 救援收到了结构化 payload
    assert called["payload"]["ledger_size"] == 0
    assert len(called["payload"]["failed_claims"]) == 1


def test_ephemeral_consult_fails_when_subagent_none():
    """ephemeral_subagent=None（未接入）→ 保守 fail(False)。"""
    gate = _gate(ephemeral=None)
    verdict = gate.consult_ephemeral_subagent(
        ledger=[],
        failed_claims=[_failed_claim()],
        assistant_text="我已生成 PPT 文件",
    )
    assert verdict is False


def test_ephemeral_consult_fails_when_subagent_raises():
    """ephemeral 抛异常 → 吞掉返 False（保守，不让异常冒泡崩 verify 链）。"""

    def boom(payload):
        raise RuntimeError("ephemeral subagent crashed")

    gate = _gate(ephemeral=boom)
    verdict = gate.consult_ephemeral_subagent(
        ledger=[],
        failed_claims=[_failed_claim()],
        assistant_text="x",
    )
    assert verdict is False


def test_ephemeral_consult_fails_when_ledger_none():
    """N1 防护：ledger=None（caller bug）→ False，不崩。"""

    gate = _gate(ephemeral=lambda p: True)
    verdict = gate.consult_ephemeral_subagent(
        ledger=None,  # type: ignore[arg-type]
        failed_claims=[_failed_claim()],
        assistant_text="x",
    )
    assert verdict is False


def test_ephemeral_consult_passes_when_subagent_returns_truthy_receipt():
    """ephemeral 在 ledger 有匹配 receipt 时确认通过（真值即放行）。"""
    receipt = ToolReceipt(
        receipt_id="r1",
        tool_name="ppt_create",
        args_hash="deadbeef",
        started_at="2026-06-02T00:00:00Z",
        ended_at="2026-06-02T00:00:01Z",
        duration_ms=1000,
        ok=True,
    )

    def ephemeral(payload):
        # 救援逻辑：ledger 非空就放行
        return payload["ledger_size"] > 0

    gate = _gate(ephemeral=ephemeral)
    verdict = gate.consult_ephemeral_subagent(
        ledger=[receipt],
        failed_claims=[_failed_claim()],
        assistant_text="我已生成 PPT 文件",
    )
    assert verdict is True


def test_off_mode_always_passes_even_with_bogus_claim():
    """off 模式：永远 passed=True（出厂默认，BC 路径不受 claim 影响）。"""
    gate = _gate(mode="off")
    outcome = gate.check(assistant_text="我已生成 PPT 但其实没有", ledger=[])
    assert outcome.passed is True
    # off 短路，不提取 claim
    assert outcome.claims_extracted == 0


def test_shadow_mode_never_blocks_but_records_unmatched():
    """shadow 模式：检测到 unmatched 但 passed 仍 True（永不拦）。"""
    gate = _gate(mode="shadow")
    outcome = gate.check(assistant_text="我已生成 PPT 文件", ledger=[])
    assert outcome.passed is True  # shadow 永放行
    assert len(outcome.unmatched_claims) == 1  # 但仍记录了未匹配 claim


def test_invalid_mode_raises():
    """非法 mode → 构造即 ValueError（VG-INVARIANT-0）。"""
    with pytest.raises(ValueError):
        VerifyGate(extractor=RegexExtractor([]), mode="bogus")


# ──────────────────────────────────────────────────────────────────
# 3. plan 门 — maybe_extract_plan 早返边角（无副作用）
# ──────────────────────────────────────────────────────────────────


class _ExplodingProvider:
    """若 maybe_extract_plan 在该走早返时还调了 provider，立刻炸出来。"""

    async def chat_with_tools(self, *args, **kwargs):
        raise AssertionError(
            "provider.chat_with_tools must NOT be called on early-return path"
        )


@pytest.mark.asyncio
async def test_maybe_extract_plan_none_when_not_code_mode():
    """not in_code_mode → 直接 None，且不触达 provider（零副作用）。"""
    plan = await maybe_extract_plan(
        _ExplodingProvider(),
        user_message="这是一条足够长的、超过四十个字符阈值的真实代码任务请求文本用于测试",
        project_root="/tmp/proj",
        in_code_mode=False,
    )
    assert plan is None


@pytest.mark.asyncio
async def test_maybe_extract_plan_none_when_message_too_short():
    """in_code_mode 但消息 <40 字符 → None，不触达 provider。"""
    plan = await maybe_extract_plan(
        _ExplodingProvider(),
        user_message="列个目录",  # 短
        project_root="/tmp/proj",
        in_code_mode=True,
    )
    assert plan is None


@pytest.mark.asyncio
async def test_maybe_extract_plan_short_after_strip():
    """前后全空格、strip 后 <40 → 仍按短消息处理返 None。"""
    plan = await maybe_extract_plan(
        _ExplodingProvider(),
        user_message="   ls   " + " " * 60,
        project_root=None,
        in_code_mode=True,
    )
    assert plan is None
