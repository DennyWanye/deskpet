# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""验证 Stage 2 接电（修 v2 评审 P0-1/P0-2）。

P0-1: registry.execute_tool 在 receipt_store_provider 注入后必须真 emit。
P0-2: VerifyGate matching 必须按 tool_hint 严格匹配，不放任一 ok receipt。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from deskpet.agent.verify_gate import (
    Claim,
    ClaimPattern,
    RegexExtractor,
    VerifyGate,
)
from deskpet.tools.receipt import make_receipt


# ─── P0-1 registry → ReceiptStore 接电验证 ──────────────────

@pytest.mark.asyncio
async def test_p0_1_no_provider_means_no_receipt(tmp_path):
    """provider=None (BC) → 不产 receipt（字节级一致）。"""
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()

    def _h(p, t):
        return {"ok": True, "path": "/tmp/x.pptx"}

    registry.register(
        "fake_p0_1a", "t",
        {"name": "fake_p0_1a", "description": "x", "parameters": {}}, _h,
    )
    await registry.execute_tool("fake_p0_1a", {}, session_id="s_p0a")
    # 没有 ReceiptStore → 不产 receipt 文件
    assert not (tmp_path / "receipts").exists()


@pytest.mark.asyncio
async def test_p0_1_provider_set_emits_receipt(tmp_path):
    """provider 返回 ReceiptStore → execute_tool 后 receipt jsonl 写盘。"""
    from deskpet.tools.receipt_store import ReceiptStore
    from deskpet.tools.registry import ToolRegistry

    store = ReceiptStore(tmp_path, key=b"\x11" * 32)
    registry = ToolRegistry()
    registry.set_receipt_store_provider(lambda: store)

    def _h(p, t):
        return {"ok": True, "path": "/tmp/x.pptx"}

    registry.register(
        "fake_p0_1b", "t",
        {"name": "fake_p0_1b", "description": "x", "parameters": {}}, _h,
    )
    await registry.execute_tool("fake_p0_1b", {"k": "v"},
                                session_id="s_p0b")

    # receipt 应已写入
    path = tmp_path / "receipts" / "s_p0b.jsonl"
    assert path.exists()
    content = path.read_text(encoding="utf-8").strip()
    assert "fake_p0_1b" in content
    # iteration 字段
    assert '"iteration": 1' in content or '"iteration":1' in content


@pytest.mark.asyncio
async def test_p0_1_provider_exception_does_not_break_dispatch(tmp_path, caplog):
    """provider 抛异常 → warn log + envelope 仍正常 (never break dispatch)。"""
    import logging
    caplog.set_level(logging.WARNING)
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.set_receipt_store_provider(lambda: (_ for _ in ()).throw(
        RuntimeError("synthetic")
    ))

    def _h(p, t):
        return {"ok": True}

    registry.register(
        "fake_p0_1c", "t",
        {"name": "fake_p0_1c", "description": "x", "parameters": {}}, _h,
    )
    env = await registry.execute_tool("fake_p0_1c", {}, session_id="s")
    assert env["ok"] is True
    assert any("receipt_store_provider raised" in r.message
               for r in caplog.records)


# ─── P0-2 VerifyGate matching 严格化 ────────────────────────

def _gate_with_pattern(tool_hint, mode="strict"):
    pat = ClaimPattern(
        id="zh_gen",
        regex=r"已生成 (?P<title>\S+)",
        artifact_kind="file",
        tool_hint=tool_hint,
    )
    return VerifyGate(extractor=RegexExtractor([pat]), mode=mode)


def test_p0_2_no_receipt_at_all_blocks():
    """空 ledger → claim 必拒。"""
    gate = _gate_with_pattern(["ppt_create"])
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[])
    assert o.passed is False
    assert len(o.unmatched_claims) == 1


def test_p0_2_matching_tool_name_passes():
    """ledger 有 ppt_create ok=True → 匹配通过。"""
    r = make_receipt(
        tool_name="ppt_create", args={},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    gate = _gate_with_pattern(["ppt_create"])
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[r])
    assert o.passed is True


def test_p0_2_mismatched_tool_name_blocks():
    """**关键修复点**：ledger 仅有 excel_create receipt，但 claim 指向
    ppt_create → 必拒（修前会放行）。"""
    r = make_receipt(
        tool_name="excel_create", args={},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    gate = _gate_with_pattern(["ppt_create"])  # claim 只接受 ppt
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[r])
    assert o.passed is False, (
        "P0-2 修复关键场景：claim 指 ppt 但 ledger 只有 excel → 必须拒"
    )


def test_p0_2_failed_receipt_does_not_satisfy():
    """ok=False receipt 不算匹配（工具调过但失败）。"""
    r = make_receipt(
        tool_name="ppt_create", args={},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=False,
    )
    gate = _gate_with_pattern(["ppt_create"])
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[r])
    assert o.passed is False


def test_p0_2_multi_tool_hint_any_match_passes():
    """pattern.tool_hint = [ppt_create, excel_create] → ledger 任一即匹配。"""
    r = make_receipt(
        tool_name="excel_create", args={},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    gate = _gate_with_pattern(["ppt_create", "excel_create"])
    o = gate.check(assistant_text="已生成 x.xlsx", ledger=[r])
    assert o.passed is True


def test_p0_2_empty_tool_hint_uses_file_tool_whitelist():
    """tool_hint=[] 的通用 pattern → ledger 有任一 file-gen 工具放行（保守扩展）。"""
    r = make_receipt(
        tool_name="ppt_create", args={},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    gate = _gate_with_pattern([])  # 无 hint
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[r])
    assert o.passed is True


def test_p0_2_empty_hint_random_tool_blocks():
    """tool_hint=[] + ledger 仅有 non-file 工具 (如 get_time) → 拒。"""
    r = make_receipt(
        tool_name="get_time", args={},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    gate = _gate_with_pattern([])  # 无 hint
    o = gate.check(assistant_text="已生成 x.pptx", ledger=[r])
    assert o.passed is False, (
        "P0-2: 无 hint pattern 不应放行任意工具，只放行 file-类"
    )


# ─── Fake-completion 真抓获率（P0-2 修复关键证据）────────────

def test_p0_2_fake_claim_no_tool_call_blocked():
    """模拟 LLM 撒谎：声称生成 PPT 但完全没调任何工具 → 必拒。"""
    gate = _gate_with_pattern(["ppt_create"])
    o = gate.check(
        assistant_text="已生成 marketing-weekly.pptx，请查收。",
        ledger=[],  # 完全没调
    )
    assert o.passed is False
    assert len(o.unmatched_claims) == 1
    assert o.unmatched_claims[0].reason == "no_receipt"
