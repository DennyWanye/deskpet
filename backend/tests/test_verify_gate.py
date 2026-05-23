"""TG-9 — VerifyGate cascade extractor + claim matching + ephemeral 救援
（WI-T2.4 + T2.4b）。

PRD §3 D6 + 二轮 N1/N2/N3。覆盖 TDD T9-1 ~ T9-16 的可单测部分（不接真 LLM）。

**Stage 2 准入硬条件**：
  - T9-12b：默认 verify/claim_patterns.yaml 100% re2 编译通过
  - T9-14b：ephemeral subagent 输入 ledger 仅含 sig-valid receipts
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from deskpet.agent.verify_gate import (
    CascadeExtractor,
    Claim,
    ClaimPattern,
    RegexExtractor,
    SmallLLMExtractor,
    UnmatchedClaim,
    VerifyGate,
    VerifyOutcome,
    load_claim_patterns,
)
from deskpet.tools.receipt import make_receipt


# ─── T9-1/T9-2 shadow vs strict ─────────────────────────────

def test_t9_1_shadow_passes_with_matching_receipt():
    pat = ClaimPattern(id="zh_gen", regex=r"已生成 (?P<title>\S+)", artifact_kind="file")
    gate = VerifyGate(extractor=RegexExtractor([pat]), mode="shadow")
    r = make_receipt(tool_name="ppt_create", args={},
                     started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     ok=True)
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[r])
    assert o.passed is True
    assert o.claims_extracted == 1


def test_t9_2_shadow_warns_but_passes_when_unmatched(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    pat = ClaimPattern(id="zh_gen", regex=r"已生成 (?P<title>\S+)", artifact_kind="file")
    gate = VerifyGate(extractor=RegexExtractor([pat]), mode="shadow")
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[])
    assert o.passed is True  # shadow 总放行
    assert len(o.unmatched_claims) == 1
    assert any("unmatched" in r.message for r in caplog.records)


def test_t9_3_strict_blocks_when_unmatched():
    pat = ClaimPattern(id="zh_gen", regex=r"已生成 (?P<title>\S+)", artifact_kind="file")
    gate = VerifyGate(extractor=RegexExtractor([pat]), mode="strict")
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[])
    assert o.passed is False
    assert len(o.unmatched_claims) == 1


# ─── T9-12 / T9-12b yaml loading ────────────────────────────

def test_t9_12_safe_load_rejects_dangerous_yaml(tmp_path):
    """T9-15: yaml.safe_load 拒 !!python/object/apply 类危险节点。"""
    yml = tmp_path / "bad.yaml"
    yml.write_text(
        "version: 1\npatterns:\n  - !!python/object/apply:os.system\n",
        encoding="utf-8",
    )
    # safe_load 会抛 ConstructorError；load_claim_patterns 应捕获 → 返回 []
    patterns = load_claim_patterns(yml)
    assert patterns == []


def test_t9_12_invalid_yaml_returns_empty(tmp_path):
    yml = tmp_path / "broken.yaml"
    yml.write_text("not: yaml: [{", encoding="utf-8")
    patterns = load_claim_patterns(yml)
    assert patterns == []


def test_t9_12b_default_yaml_100pct_compiles():
    """**Stage 2 准入硬条件 N2**：仓库自带 verify/claim_patterns.yaml
    所有 pattern 必须成功 re 编译；任一 reject 即测试红。"""
    repo_root = Path(__file__).parent.parent.parent.resolve()
    yml = repo_root / "verify" / "claim_patterns.yaml"
    if not yml.exists():
        pytest.skip(f"default yaml not at {yml}")
    # 读 yaml 算 raw pattern 数
    with open(yml, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw_count = len(raw.get("patterns", []))
    loaded = load_claim_patterns(yml)
    assert len(loaded) == raw_count, (
        f"expected all {raw_count} patterns to compile, got {len(loaded)}"
    )


def test_t9_13_redos_pattern_rejected(tmp_path):
    """**T9-13** ReDoS-prone pattern（nested quantifier）应被拒。"""
    yml = tmp_path / "redos.yaml"
    yml.write_text(
        """
version: 1
patterns:
  - id: redos
    regex: '(a+)+$'
    artifact_kind: file
  - id: safe
    regex: 'hello'
    artifact_kind: file
""",
        encoding="utf-8",
    )
    patterns = load_claim_patterns(yml)
    # 1 个 safe pattern 编译成功；redos 被拒
    assert len(patterns) == 1
    assert patterns[0].id == "safe"


def test_t9_13_redos_alternation_rejected(tmp_path):
    """(a|b|c)+ 类等价分支也算 ReDoS-prone。"""
    yml = tmp_path / "redos2.yaml"
    yml.write_text(
        """
version: 1
patterns:
  - id: alt_redos
    regex: '(a|b|c)+'
    artifact_kind: file
""",
        encoding="utf-8",
    )
    patterns = load_claim_patterns(yml)
    assert patterns == []


# ─── T9-14b ephemeral 信任面 ───────────────────────────────

def test_t9_14b_ephemeral_called_only_when_set():
    """ephemeral_subagent=None 时 consult 返回 False（无救援，直接 fail）。"""
    gate = VerifyGate(extractor=RegexExtractor([]), mode="strict",
                      ephemeral_subagent=None)
    verdict = gate.consult_ephemeral_subagent(
        ledger=[], failed_claims=[], assistant_text="x",
    )
    assert verdict is False


def test_t9_14b_ephemeral_pass_path():
    called_with: list[dict] = []

    def _mock_eph(ctx: dict) -> bool:
        called_with.append(ctx)
        return True

    gate = VerifyGate(extractor=RegexExtractor([]), mode="strict",
                      ephemeral_subagent=_mock_eph)
    r = make_receipt(tool_name="t", args={},
                     started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     ok=True)
    verdict = gate.consult_ephemeral_subagent(
        ledger=[r],
        failed_claims=[UnmatchedClaim(
            pattern_id="x", raw_text="已生成",
            expected_kind="file", expected_path_or_title=None,
            reason="no_receipt",
        )],
        assistant_text="已生成 x.pptx",
    )
    assert verdict is True
    assert len(called_with) == 1
    assert called_with[0]["ledger_size"] == 1


def test_t9_14b_ephemeral_exception_falls_back_to_fail(caplog):
    import logging
    caplog.set_level(logging.WARNING)

    def _bad_eph(ctx: dict) -> bool:
        raise RuntimeError("ephemeral subagent crashed")

    gate = VerifyGate(extractor=RegexExtractor([]), mode="strict",
                      ephemeral_subagent=_bad_eph)
    verdict = gate.consult_ephemeral_subagent(
        ledger=[], failed_claims=[], assistant_text="x",
    )
    assert verdict is False
    assert any("ephemeral_subagent raised" in r.message for r in caplog.records)


# ─── CascadeExtractor (T9-11 LLM fallback hook) ─────────────

def test_cascade_skips_fallback_when_regex_hits():
    pat = ClaimPattern(id="zh", regex=r"已生成", artifact_kind="file")
    primary = RegexExtractor([pat])
    called = []

    class _SpyFallback:
        def extract(self, t, h):
            called.append(t)
            return [Claim(pattern_id="llm", raw_text="x")]

    cas = CascadeExtractor(primary, _SpyFallback())
    out = cas.extract("已生成 x", hints={"ledger_size": 1})
    assert len(out) == 1
    assert out[0].pattern_id == "zh"
    assert called == []  # fallback 未触发


def test_cascade_triggers_fallback_when_suspicious():
    """长 text + 0 regex 命中 + ledger 非空 → 触发 fallback。"""
    primary = RegexExtractor([])  # 0 patterns
    called = []

    class _SpyFallback:
        def extract(self, t, h):
            called.append(t)
            return [Claim(pattern_id="llm", raw_text=t[:20])]

    cas = CascadeExtractor(primary, _SpyFallback(), fallback_threshold_chars=20)
    long_text = "PPT 已就绪 " * 20  # 长 + 同义改写
    out = cas.extract(long_text, hints={"ledger_size": 1})
    assert len(called) == 1
    assert len(out) == 1
    assert out[0].pattern_id == "llm"


def test_cascade_no_fallback_when_ledger_empty():
    """ledger=0 时即使 0 regex 命中也不调 fallback（防误判）。"""
    primary = RegexExtractor([])
    fallback_called = []

    class _SpyFallback:
        def extract(self, t, h):
            fallback_called.append(t)
            return []

    cas = CascadeExtractor(primary, _SpyFallback())
    cas.extract("PPT 已就绪 " * 20, hints={"ledger_size": 0})
    assert fallback_called == []


# ─── invalid mode rejection (sanity) ────────────────────────

def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="invalid mode"):
        VerifyGate(extractor=RegexExtractor([]), mode="bananas")
