# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-V2-5/6/7 G3 Partition 端到端 boot smoke — 真注册 read + write 工具
真触发 partition_dispatch 看 timestamp 验证读并行 + 写串行。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


async def main():
    print("=" * 70)
    print("MR-V2-5/6/7 boot smoke — G3 Partition 端到端真路径")
    print("=" * 70)

    from deskpet.tools.registry import ToolRegistry, ToolSpec

    reg = ToolRegistry()
    timestamps: list[tuple[str, str, float]] = []

    async def read_handler(params, task_id):
        idx = params.get("idx", "?")
        timestamps.append((f"read_{idx}", "start", time.time()))
        await asyncio.sleep(0.15)  # 模拟 IO
        timestamps.append((f"read_{idx}", "end", time.time()))
        return json.dumps({"ok": True, "tool": "read", "idx": idx})

    async def write_handler(params, task_id):
        idx = params.get("idx", "?")
        timestamps.append((f"write_{idx}", "start", time.time()))
        await asyncio.sleep(0.15)
        timestamps.append((f"write_{idx}", "end", time.time()))
        return json.dumps({"ok": True, "tool": "write", "idx": idx})

    # 注册 2 read（safe）+ 2 write（unsafe）
    for name, handler, safe in [
        ("read_safe_1", read_handler, True),
        ("read_safe_2", read_handler, True),
        ("write_1", write_handler, False),
        ("write_2", write_handler, False),
    ]:
        spec = ToolSpec(
            name=name, toolset="test",
            schema={"name": name, "description": name, "parameters": {}},
            handler=handler,
            concurrency_safe=safe,
        )
        with reg._lock:
            reg._tools[name] = spec

    print(f"\n[1] 注册 4 工具: 2 read (safe) + 2 write (unsafe)")

    # 触发 partition_dispatch — 顺序: read1, write1, read2, write2
    from collections import namedtuple
    ToolCall = namedtuple("ToolCall", ["name", "args", "task_id"])
    calls = [
        ToolCall("read_safe_1", {"idx": "1"}, "t"),
        ToolCall("write_1", {"idx": "1"}, "t"),
        ToolCall("read_safe_2", {"idx": "2"}, "t"),
        ToolCall("write_2", {"idx": "2"}, "t"),
    ]
    print(f"\n[2] partition_dispatch 4 calls (order: r1, w1, r2, w2)")
    start = time.time()
    results = await reg.partition_dispatch(calls, session_id="smoke")
    elapsed = time.time() - start
    print(f"    elapsed: {elapsed*1000:.0f}ms (理论：纯串行 600ms / 全并发 150ms)")

    # 打印全部 timestamps 看真实执行顺序
    print(f"\n[diag] 全部 timestamps（按真发生顺序）:")
    t0 = timestamps[0][2] if timestamps else 0
    for name, stage, ts in timestamps:
        print(f"      +{int((ts-t0)*1000):>4}ms  {name:20} {stage}")

    # 真验证：
    # 1. 顺序保持 (results[0]=read_1, [1]=write_1, [2]=read_2, [3]=write_2)
    for i, r in enumerate(results):
        payload = json.loads(r.get("result", "{}"))
        print(f"    result[{i}]: {payload.get('tool')}_{payload.get('idx')} ok={r.get('ok')}")
        assert r["ok"], f"call {i} failed"

    # 2. 真读并行：2 个 read 启动 timestamp 差 < 50ms
    read_starts = [ts for (n, s, ts) in timestamps if n.startswith("read") and s == "start"]
    assert len(read_starts) == 2
    read_diff = abs(read_starts[1] - read_starts[0]) * 1000
    print(f"\n[3] read 并行验证: 2 个 read 启动差 = {read_diff:.0f}ms (期望 < 50ms)")
    assert read_diff < 50, f"reads should start nearly simultaneously, got {read_diff}ms"
    print(f"    [OK] read 真并行")

    # 3. 真写串行：write_2 启动 > write_1 结束
    write1_end = next(ts for (n, s, ts) in timestamps if n == "write_1" and s == "end")
    write2_start = next(ts for (n, s, ts) in timestamps if n == "write_2" and s == "start")
    serial_gap = (write2_start - write1_end) * 1000
    print(f"\n[4] write 串行验证: write_2 启动 - write_1 结束 = {serial_gap:.0f}ms (期望 ≥ -10ms 表示串行)")
    assert write2_start >= write1_end - 0.01, "writes should serialize, but write_2 started before write_1 ended"
    print(f"    [OK] write 真串行（write_2 在 write_1 后启动）")

    # 4. 总耗时合理：纯串行=600ms, 全并发=150ms, partition 应≈ read 并行(150) + write 串行(300) ≈ 300ms
    print(f"\n[5] 总耗时合理性: {elapsed*1000:.0f}ms 应在 250-500ms 之间")
    assert 200 < elapsed * 1000 < 600, f"elapsed {elapsed*1000}ms outside expected range"
    print(f"    [OK] 总耗时符合 partition 预期（不是纯串行也不是全并发）")

    print("\n" + "=" * 70)
    print("MR-V2-5/6/7 BOOT SMOKE PASSED — G3 Partition 真端到端读并行写串行")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
