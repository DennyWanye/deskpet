# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.2 + WI-T2.3 v3 — P0 bug 修复回归测试.

WI-T2.2 P0-2: ReceiptStore retention 被 `min(retention, 7)` 截断
  - 修：取消截断，按 cfg.tools.last_mile.artifact_dir_retention_days 真值
WI-T2.3 P0-3: emit_receipt duration_ms ~0
  - 修：execute_tool 顶部捕 _started_at，emit_receipt 处 ended_at=now()，
        让 duration_ms 反映真实 dispatch 时长

测试组对照 plans/2026-05-24-tool-layer-optimization-v3/01-TDD.md §A2/§A3。
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ─── WI-T2.2 retention 不截断 ────────────────────────────────────


def test_t2_2_receipt_store_30day_retention_not_capped_at_7():
    """ReceiptStore(retention_days=30) 真按 30 天 cutoff 跑."""
    from deskpet.tools.receipt_store import ReceiptStore

    with tempfile.TemporaryDirectory() as td:
        store = ReceiptStore(Path(td), retention_days=30, key=b"\x42" * 32)
        assert store.retention_days == 30, (
            "WI-T2.2: ReceiptStore must honor caller-supplied retention. "
            "If this is 7, main.py min() truncation is back."
        )


def test_t2_2_main_py_does_not_truncate_retention():
    """main.py 非注释代码不再含 `min(retention, 7)`.

    （注释里提到历史 bug 名是允许的；只检查真实代码行。）
    """
    main_py_path = Path(__file__).parent.parent / "main.py"
    lines = main_py_path.read_text(encoding="utf-8").splitlines()
    code_only = [
        ln for ln in lines
        if "min(retention, 7)" in ln and not ln.lstrip().startswith("#")
    ]
    assert not code_only, (
        f"WI-T2.2 regression: main.py truncates retention at 7 days "
        f"(matched code lines: {code_only}). Last-mile P0-2 bug returned."
    )


# ─── WI-T2.3 duration_ms 真实 ────────────────────────────────────


def _trivial_handler(params, task_id):
    """Toy handler that sleeps a bit then returns ok envelope."""
    import time
    time.sleep(0.05)  # 50ms — ensure duration_ms > 0
    return json.dumps({"ok": True, "result": "done"}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_t2_3_receipt_duration_ms_reflects_real_dispatch():
    """execute_tool 一个 ~50ms tool → receipt.duration_ms ≥ 40ms.

    last-mile P0-3 修复证据：原 emit_receipt 用两次 now() → duration_ms ~0；
    本测试断言 duration_ms 真正反映 dispatch 时长。
    """
    from deskpet.tools.registry import ToolRegistry, ToolSpec
    from deskpet.tools.receipt_store import ReceiptStore

    with tempfile.TemporaryDirectory() as td:
        store = ReceiptStore(Path(td), key=b"\x42" * 32)
        registry = ToolRegistry()
        spec = ToolSpec(
            name="sleeper",
            toolset="test",
            schema={"description": "t2.3 dispatch timing probe", "parameters": {}},
            handler=_trivial_handler,
            permission_category="read_file",
        )
        with registry._lock:
            registry._tools["sleeper"] = spec
        registry.set_receipt_store_provider(lambda: store)

        result = await registry.execute_tool(
            "sleeper", params={}, session_id="t2_3_dispatch_timing", task_id="t",
        )
        assert result["ok"] is True

        # Receipt 已写盘，加载回来查 duration_ms
        ledger = store.load_session("t2_3_dispatch_timing")
        assert len(ledger) == 1, (
            "execute_tool should emit exactly one receipt; got %d" % len(ledger)
        )
        rec = ledger[0]
        assert rec.duration_ms >= 40, (
            f"WI-T2.3: receipt duration_ms = {rec.duration_ms}ms, expected ≥40ms. "
            f"emit_receipt is back to using two now() calls; duration is wallclock noise."
        )
        # 上限健康 sanity（避免某天 dispatch 永远 hang 后误判）
        assert rec.duration_ms <= 5000, (
            f"duration_ms={rec.duration_ms}ms unreasonably high — dispatch hung?"
        )


@pytest.mark.asyncio
async def test_t2_3_receipt_duration_ms_zero_for_instant_handler():
    """瞬时 handler 也有 duration_ms ≥ 0（不是负数）— sanity 验证.

    防御性测试：如果 _started_at 被错位（譬如放到 handler 内），
    duration_ms 可能算成负数。
    """
    from deskpet.tools.registry import ToolRegistry, ToolSpec
    from deskpet.tools.receipt_store import ReceiptStore

    def _instant(params, task_id):
        return json.dumps({"ok": True}, ensure_ascii=False)

    with tempfile.TemporaryDirectory() as td:
        store = ReceiptStore(Path(td), key=b"\x42" * 32)
        registry = ToolRegistry()
        spec = ToolSpec(
            name="instant",
            toolset="test",
            schema={"description": "t2.3 instant dispatch", "parameters": {}},
            handler=_instant,
            permission_category="read_file",
        )
        with registry._lock:
            registry._tools["instant"] = spec
        registry.set_receipt_store_provider(lambda: store)

        await registry.execute_tool(
            "instant", params={}, session_id="t2_3_instant", task_id="t",
        )
        ledger = store.load_session("t2_3_instant")
        assert len(ledger) == 1
        assert ledger[0].duration_ms >= 0  # not negative
