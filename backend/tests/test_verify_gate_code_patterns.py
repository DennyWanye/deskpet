# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""superpowers Layer ③ — code-scenario claim patterns + strict gate behavior.

Proves the new code patterns (zh_created_file / zh_modified_file /
zh_tests_passed / en_modified_file) (1) load, (2) extract claims, and
(3) under strict mode: a claim with a matching tool receipt PASSES, a
bare claim with no receipt is BLOCKED (the fake-completion we want to catch).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from deskpet.agent.verify_gate import (
    VerifyGate, RegexExtractor, load_claim_patterns,
)
from deskpet.tools.receipt import make_receipt

_YAML = Path(__file__).resolve().parents[1] / "verify" / "claim_patterns.yaml"


def _patterns():
    return load_claim_patterns(_YAML)


def _receipt(tool_name: str, ok: bool = True):
    t0 = datetime.now(timezone.utc)
    return make_receipt(tool_name=tool_name, args={}, started_at=t0,
                        ended_at=t0 + timedelta(milliseconds=5), ok=ok,
                        session_id="s1", iteration=1)


def test_code_patterns_load():
    pats = _patterns()
    ids = {p.id for p in pats}
    assert {"zh_created_file", "zh_modified_file", "zh_tests_passed",
            "en_modified_file"}.issubset(ids)


def test_created_file_claim_extracted():
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="strict")
    ext = gate.extractor
    claims = ext.extract("好了，我已创建 hello.py，内容已写好。", hints={})
    assert any(c.pattern_id == "zh_created_file" and c.path == "hello.py"
               for c in claims)


def test_strict_passes_when_receipt_present():
    """真调了 write_file → "已创建 X.py" claim 匹配 → strict 放行。"""
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="strict")
    out = gate.check(
        assistant_text="我已创建 hello.py 并写入内容。",
        ledger=[_receipt("write_file", ok=True)],
    )
    assert out.passed is True
    assert out.claims_extracted >= 1


def test_strict_blocks_fake_created_claim():
    """裸声明"已创建 X.py" 但 ledger 无 write_file → strict 拦截。"""
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="strict")
    out = gate.check(
        assistant_text="我已创建 hello.py 并写入内容。",
        ledger=[],  # no tool calls — fake completion
    )
    assert out.passed is False
    assert len(out.unmatched_claims) >= 1
    assert out.unmatched_claims[0].pattern_id == "zh_created_file"


def test_strict_blocks_fake_tests_passed():
    """裸声明"测试通过" 但 ledger 无 run_shell → strict 拦截。"""
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="strict")
    out = gate.check(assistant_text="所有测试已通过，可以交付。", ledger=[])
    assert out.passed is False
    assert any(c.pattern_id == "zh_tests_passed" for c in out.unmatched_claims)


def test_strict_passes_tests_with_run_shell_receipt():
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="strict")
    out = gate.check(
        assistant_text="测试通过。",
        ledger=[_receipt("run_shell", ok=True)],
    )
    assert out.passed is True


def test_modified_file_claim():
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="strict")
    # with edit_file receipt → pass
    ok = gate.check(assistant_text="我已修改 config.toml。",
                    ledger=[_receipt("edit_file", ok=True)])
    assert ok.passed is True
    # without → block
    bad = gate.check(assistant_text="我已修改 config.toml。", ledger=[])
    assert bad.passed is False


def test_shadow_never_blocks_even_fake():
    """shadow 模式：fake claim 也放行（只 warn）— 出厂默认安全。"""
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="shadow")
    out = gate.check(assistant_text="我已创建 hello.py。", ledger=[])
    assert out.passed is True  # shadow always passes


def test_no_false_positive_on_future_tense():
    """'我将创建 X.py' (未来时，非完成断言) 不应被 created 模式命中。"""
    gate = VerifyGate(extractor=RegexExtractor(_patterns()), mode="strict")
    out = gate.check(assistant_text="接下来我将创建 hello.py 并写入内容。",
                     ledger=[])
    # '将创建' 不匹配 '已创建/创建了' → no claim → pass
    assert out.passed is True
    assert out.claims_extracted == 0
