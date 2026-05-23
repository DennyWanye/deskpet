"""TG-0 — tool last-mile 模块 smoke 测试（WI-T0 / 接口腐烂守护）。

PRD/TDD 提到的全部新模块必须可被 import 且接口签名稳定。这是 strangler-fig
接入前的 baseline 健康检查 —— 任何新建模块若 import 失败或接口腐烂，本
测试组立刻红，避免后续 WI 在错误地基上推进。

测试组对照 plans/2026-05-23-tool-last-mile-upgrade/01-TDD.md §B TG-0。
"""
from __future__ import annotations

import inspect


def test_t0_1_artifact_module_imports():
    from deskpet.tools.artifact import (  # noqa: F401
        ToolArtifact,
        ArtifactAction,
        sha256_file_async,
        extract_artifacts_from_result,
        maybe_add_artifacts,
    )


def test_t0_2_receipt_module_imports():
    from deskpet.tools.receipt import (  # noqa: F401
        ToolReceipt,
        hmac_sign,
        hmac_verify,
        args_hash,
        canonical_json,
        make_receipt,
    )


def test_t0_3_verify_gate_module_imports():
    from deskpet.agent.verify_gate import (  # noqa: F401
        VerifyGate,
        ClaimPattern,
        Claim,
        RegexExtractor,
        SmallLLMExtractor,
        CascadeExtractor,
        VerifyOutcome,
        UnmatchedClaim,
        VerifierFailure,
    )


def test_t0_4_registry_execute_tool_signature_compatible():
    """execute_tool 签名不破现有 caller（4 必需位置参数 + 默认值）。"""
    from deskpet.tools.registry import ToolRegistry
    sig = inspect.signature(ToolRegistry.execute_tool)
    params = list(sig.parameters.values())
    # self + name + params + session_id + task_id
    assert len(params) == 5
    assert params[1].name == "name"
    assert params[2].name == "params"
    assert params[3].name == "session_id"
    assert params[4].name == "task_id"


def test_t0_5_config_tools_dataclass_chain():
    """AppConfig → ToolsConfig → ToolsLastMileConfig / ToolsVerifierConfig 链。"""
    from config import AppConfig, ToolsConfig, ToolsLastMileConfig, ToolsVerifierConfig
    cfg = AppConfig()
    assert isinstance(cfg.tools, ToolsConfig)
    assert isinstance(cfg.tools.last_mile, ToolsLastMileConfig)
    assert isinstance(cfg.tools.verifier, ToolsVerifierConfig)


def test_t0_6_receipt_hmac_sign_verify_roundtrip():
    """HMAC sign → verify 闭环（即使是 stub）。"""
    from datetime import datetime, timezone
    from deskpet.tools.receipt import make_receipt, hmac_verify
    r = make_receipt(
        tool_name="x",
        args={"k": "v"},
        started_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        ok=True,
    )
    assert hmac_verify(r) is True
    # 篡改 → verify 失败
    r.tool_name = "evil"
    assert hmac_verify(r) is False


def test_t0_7_verify_gate_off_mode_passes():
    from deskpet.agent.verify_gate import VerifyGate, RegexExtractor
    gate = VerifyGate(extractor=RegexExtractor(patterns=[]), mode="off")
    outcome = gate.check(assistant_text="anything", ledger=None)
    assert outcome.passed is True


def test_t0_8_verify_gate_invalid_mode_rejected():
    import pytest
    from deskpet.agent.verify_gate import VerifyGate, RegexExtractor
    with pytest.raises(ValueError, match="invalid mode"):
        VerifyGate(extractor=RegexExtractor(patterns=[]), mode="bananas")
