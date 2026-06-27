# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""WI-OC-2 — SubagentScheduler 累计观测层（背压/lane 指标）。

只覆盖**新增累计字段**（peak_concurrent / total_queued / total_rejected +
lane_wait 分位 + metrics_sink 接线）；既有调度/进度回归在
test_subagent_scheduler.py。BC：累计字段是纯增，旧瞬时字段不变。
"""
from __future__ import annotations

import asyncio

import pytest

from deskpet.agent.subagent_scheduler import SubagentScheduler


def _run(coro):
    return asyncio.run(coro)


def test_peak_concurrent_reaches_cap():
    """N 个超 cap 任务跑完 → peak_concurrent 应达到 global cap（背压真生效）。"""

    async def body():
        # lane cap 给足（5），让 global cap=3 成为绑定约束；否则默认 lane cap=2
        # 会先卡住，peak 顶不到 global cap。
        sched = SubagentScheduler(global_concurrency=3, lane_caps={"general": 5})

        async def one():
            await asyncio.sleep(0.02)
            return "ok"

        await asyncio.gather(
            *[
                sched.run(kind="general", run_id=f"r{i}", task_id=f"t{i}",
                          parent_sid="p", coro_factory=one)
                for i in range(8)
            ]
        )
        return sched.snapshot()

    snap = _run(body())
    # 8 个任务 / global cap=3 → 运行峰值应恰好顶到 cap（不会超）。
    assert snap["peak_concurrent"] == 3
    # 瞬时值回零（无泄漏，BC 不变）。
    assert snap["running"] == 0 and snap["queued"] == 0


def test_total_queued_counts_all_enqueues():
    """total_queued 累计入队数 ≥ N（只增不减，吞吐口径）。"""

    async def body():
        sched = SubagentScheduler(global_concurrency=2)

        async def one():
            await asyncio.sleep(0.005)

        await asyncio.gather(
            *[
                sched.run(kind="general", run_id=f"r{i}", task_id=f"t{i}",
                          parent_sid="p", coro_factory=one)
                for i in range(5)
            ]
        )
        return sched.snapshot()

    snap = _run(body())
    assert snap["total_queued"] == 5
    assert snap["total_rejected"] == 0  # 无取消 → 无拒绝


def test_total_rejected_on_queued_cancel():
    """排队中被取消 → total_rejected > 0（背压下被丢弃的任务可见）。"""

    async def body():
        # cap=1：第一个占槽，第二个排队 → 取消它时仍在 queued。
        sched = SubagentScheduler(global_concurrency=1)
        started = asyncio.Event()

        async def hog():
            started.set()
            await asyncio.sleep(0.2)

        async def victim():
            await asyncio.sleep(0.2)

        t_hog = asyncio.create_task(
            sched.run(kind="general", run_id="hog", task_id="hog",
                      parent_sid="p", coro_factory=hog)
        )
        await started.wait()
        t_victim = asyncio.create_task(
            sched.run(kind="general", run_id="vic", task_id="vic",
                      parent_sid="p", coro_factory=victim)
        )
        await asyncio.sleep(0.01)  # victim 跑到 queued 并在信号量 await
        t_victim.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t_victim
        await t_hog
        return sched.snapshot()

    snap = _run(body())
    assert snap["total_rejected"] >= 1
    assert snap["total_queued"] == 2  # hog + victim 都入过队
    assert snap["running"] == 0 and snap["queued"] == 0


def test_total_rejected_on_running_cancel():
    """运行中被取消（已拿双闸）也计入 total_rejected。"""

    async def body():
        sched = SubagentScheduler(global_concurrency=2)
        running = asyncio.Event()

        async def long_task():
            running.set()
            await asyncio.sleep(1.0)

        t = asyncio.create_task(
            sched.run(kind="general", run_id="r1", task_id="t1",
                      parent_sid="p", coro_factory=long_task)
        )
        await running.wait()
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        return sched.snapshot()

    snap = _run(body())
    assert snap["total_rejected"] >= 1


def test_genuine_failure_not_counted_as_rejected():
    """真错误（非取消）不计入 total_rejected —— 锁住「取消 vs 失败」语义。"""

    async def body():
        sched = SubagentScheduler()

        async def boom():
            raise ValueError("nope")

        await sched.run(kind="general", run_id="r1", task_id="t1",
                        parent_sid="p", coro_factory=boom)

    with pytest.raises(ValueError):
        _run(body())


def test_lane_wait_percentiles_present():
    """跑完若干任务后 snapshot 含 lane_wait p50/p95（数值 ≥ 0）。"""

    async def body():
        sched = SubagentScheduler(global_concurrency=2)

        async def one():
            await asyncio.sleep(0.01)

        await asyncio.gather(
            *[
                sched.run(kind="general", run_id=f"r{i}", task_id=f"t{i}",
                          parent_sid="p", coro_factory=one)
                for i in range(6)
            ]
        )
        return sched.snapshot()

    snap = _run(body())
    assert "lane_wait_p50_ms" in snap and "lane_wait_p95_ms" in snap
    assert snap["lane_wait_p50_ms"] >= 0
    assert snap["lane_wait_p95_ms"] >= snap["lane_wait_p50_ms"]


def test_progress_event_carries_cumulative_fields():
    """subagent_progress 事件 payload 附累计字段（前端据此渲染汇总区）。"""
    events: list[dict] = []

    async def body():
        sched = SubagentScheduler(
            global_concurrency=2,
            progress_sink=lambda p: events.append(dict(p)),
        )

        async def one():
            await asyncio.sleep(0.005)

        await asyncio.gather(
            *[
                sched.run(kind="general", run_id=f"r{i}", task_id=f"t{i}",
                          parent_sid="p", coro_factory=one)
                for i in range(3)
            ]
        )

    _run(body())
    assert events, "应至少发出一条进度事件"
    for ev in events:
        # 既有字段不变（BC）+ 新累计字段存在。
        assert "status" in ev and "run_id" in ev
        assert "peak_concurrent" in ev
        assert "total_queued" in ev
        assert "total_rejected" in ev


def test_lane_wait_recorded_to_metrics_sink(monkeypatch):
    """每次进入 running 都把 lane wait 写进 metrics_sink（事件名在白名单）。"""
    recorded: list[tuple[str, dict]] = []

    import observability.metrics_sink as ms

    def _fake_record(event, detail=None):
        recorded.append((event, dict(detail or {})))
        return True

    monkeypatch.setattr(ms, "record", _fake_record)

    async def body():
        sched = SubagentScheduler(global_concurrency=2)

        async def one():
            await asyncio.sleep(0.005)

        await asyncio.gather(
            *[
                sched.run(kind="research", run_id=f"r{i}", task_id=f"t{i}",
                          parent_sid="p", coro_factory=one)
                for i in range(3)
            ]
        )

    _run(body())
    lane_waits = [d for (ev, d) in recorded if ev == "subagent_lane_wait"]
    assert len(lane_waits) == 3  # 每个进入 running 的任务记一笔
    assert all("duration_ms" in d and d.get("kind") == "research" for d in lane_waits)


def test_lane_wait_event_in_metrics_whitelist():
    """subagent_lane_wait 真能写进真 sink（白名单接线正确，非 fallback 证据）。"""
    import tempfile
    from pathlib import Path

    from observability.metrics_sink import MetricsSink

    with tempfile.TemporaryDirectory() as td:
        sink = MetricsSink(Path(td) / "m.jsonl")
        ok = sink.record("subagent_lane_wait", {"kind": "code", "duration_ms": 12})
        assert ok is True
        rows = sink.read_all()
        assert any(r["event"] == "subagent_lane_wait" for r in rows)
        row = next(r for r in rows if r["event"] == "subagent_lane_wait")
        # detail 键已在白名单（kind + duration_ms 都存活）。
        assert row["detail"].get("kind") == "code"
        assert row["detail"].get("duration_ms") == 12
