# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Lane-aware 有界并发调度器 — 子代理并发驱动的调度地基。

plan: plans/2026-06-21-subagent-concurrency-driver/ WI-0.2

偷师 OpenClaw 的 lane 队列（纯 asyncio，无外部依赖）：每种事务（kind）一个
``asyncio.Semaphore`` lane + 一个全局 cap。超 cap 自然**排队**（背压，不丢任务）。
进度通过 ``progress_sink`` 回调发出（queued → running → completed/failed）。

为什么纯 asyncio 够用
--------------------
deskpet 是单进程单事件循环。``asyncio.Semaphore`` 的 acquire 即排队等待，
天然背压；不需要线程池 / Redis / celery（feedback_no_sandbox_constraints：
单机桌宠不过度工程）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


class SubagentScheduler:
    """全局 cap + 每 kind lane cap 的有界并发调度器。

    Args:
        global_concurrency: 全局同时运行的子代理上限（背压第一道闸）。
        lane_caps: ``{kind: cap}`` 每 kind lane 并发上限（第二道闸）。
        progress_sink: 可选回调，收 ``dict`` 进度事件（queued/running/
            completed/failed）。**永不阻断调度**——回调抛异常被吞。
    """

    def __init__(
        self,
        *,
        global_concurrency: int = 4,
        lane_caps: dict[str, int] | None = None,
        progress_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._global = asyncio.Semaphore(max(1, int(global_concurrency)))
        self._lane_caps: dict[str, int] = dict(lane_caps or {})
        self._lanes: dict[str, asyncio.Semaphore] = {}
        self._progress = progress_sink
        self._running = 0
        self._queued = 0
        # WI-OC-2 累计观测层（纯增量计数，不影响调度行为）：
        #   peak_concurrent: 历史运行峰值（≤ global cap，验证背压真生效）。
        #   total_queued:    累计入队总数（不随出队回落，是吞吐口径）。
        #   total_rejected:  累计拒绝/取消计数（排队或运行中被取消 → 终态 failed
        #                    +reason="cancelled" 时 +1；真错误不计入）。
        #   lane_wait_ms:    每次跑完记一笔队列等待耗时样本，供 P50/P95 估算。
        self._peak_concurrent = 0
        self._total_queued = 0
        self._total_rejected = 0
        self._lane_wait_ms: list[float] = []

    def _lane(self, kind: str) -> asyncio.Semaphore:
        """惰性创建该 kind 的 lane semaphore（默认 cap=2）。"""
        if kind not in self._lanes:
            cap = max(1, int(self._lane_caps.get(kind, 2)))
            self._lanes[kind] = asyncio.Semaphore(cap)
        return self._lanes[kind]

    def _emit(self, payload: dict[str, Any]) -> None:
        if self._progress is None:
            return
        # WI-OC-2：在每条进度事件 payload 上附累计观测字段（纯增字段，旧字段
        # 不变 → BC：旧前端忽略未知 key，新前端读取展示；旧后端不推 → 前端缺省 0）。
        try:
            payload = {**payload, **self._metrics()}
        except Exception as exc:  # noqa: BLE001 — 累计字段附加永不阻断进度
            log.debug("subagent scheduler metrics attach failed: %s", exc)
        try:
            self._progress(payload)
        except Exception as exc:  # noqa: BLE001 — 进度永不阻断调度
            log.debug("subagent scheduler progress emit failed: %s", exc)

    def _metrics(self) -> dict[str, int]:
        """WI-OC-2 累计观测字段（peak / total_queued / total_rejected）。"""
        return {
            "peak_concurrent": self._peak_concurrent,
            "total_queued": self._total_queued,
            "total_rejected": self._total_rejected,
        }

    @staticmethod
    def _percentile(samples: list[float], pct: float) -> float:
        """最近邻分位数（无 numpy 依赖）。空样本返回 0.0。"""
        if not samples:
            return 0.0
        ordered = sorted(samples)
        if len(ordered) == 1:
            return ordered[0]
        k = (len(ordered) - 1) * pct
        lo = int(k)
        hi = min(lo + 1, len(ordered) - 1)
        frac = k - lo
        return ordered[lo] + (ordered[hi] - ordered[lo]) * frac

    def _record_lane_wait(self, kind: str, wait_ms: int) -> None:
        """把单次队列等待耗时落到 metrics_sink（失败静默吞，不阻断调度）。"""
        try:
            from observability.metrics_sink import record  # noqa: PLC0415

            record(
                "subagent_lane_wait",
                {"kind": kind, "duration_ms": int(wait_ms)},
            )
        except Exception as exc:  # noqa: BLE001 — 观测失败永不影响调度
            log.debug("subagent lane_wait metric failed: %s", exc)

    async def run(
        self,
        *,
        kind: str,
        run_id: str,
        task_id: str,
        parent_sid: str,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """在双闸（全局 + lane）背压下跑一个子代理协程。

        Args:
            kind: 事务类型，决定 lane。
            run_id / task_id / parent_sid: 仅用于进度事件标识。
            coro_factory: 无参可调用，返回子代理协程（**延迟构造**——
                只有在两道闸都拿到后才真正起子代理，避免占位浪费）。
        """
        lane = self._lane(kind)
        self._queued += 1
        self._total_queued += 1  # WI-OC-2 累计入队（只增不减，吞吐口径）
        t_queued = time.time()
        self._emit(
            {
                "run_id": run_id,
                "kind": kind,
                "task_id": task_id,
                "parent_sid": parent_sid,
                "status": "queued",
                "ts": t_queued,
            }
        )
        # 是否已进入 running（拿到双闸）。决定排队阶段被取消时谁来补发终态：
        # 未进 running → 由下面 except 补发；已进 running → 内层 try/except 已发。
        entered_running = False
        try:
            # 双闸背压：全局 cap → kind-lane cap。超 cap 在此 await 排队。
            # ★ 排队期间若 Task.cancel()，CancelledError 在此抛出（还没进 try
            #   块），由本函数最外层 except 补发终态进度，避免前端卡片卡 queued。
            async with self._global:
                async with lane:
                    self._queued -= 1
                    self._running += 1
                    if self._running > self._peak_concurrent:
                        self._peak_concurrent = self._running  # WI-OC-2 运行峰值
                    entered_running = True
                    # WI-OC-2 队列等待样本（拿到双闸的时刻 - 入队时刻），供 P50/P95。
                    lane_wait_ms = int((time.time() - t_queued) * 1000)
                    self._lane_wait_ms.append(float(lane_wait_ms))
                    self._record_lane_wait(kind, lane_wait_ms)
                    # F9: 日志锚点供真机 E2E grep
                    log.info(
                        "subagent_scheduled kind=%s run_id=%s task_id=%s",
                        kind,
                        run_id,
                        task_id,
                    )
                    self._emit(
                        {
                            "run_id": run_id,
                            "kind": kind,
                            "task_id": task_id,
                            "parent_sid": parent_sid,
                            "status": "running",
                            "ts": time.time(),
                        }
                    )
                    t0 = time.time()
                    try:
                        out = await coro_factory()
                        self._emit(
                            {
                                "run_id": run_id,
                                "kind": kind,
                                "task_id": task_id,
                                "parent_sid": parent_sid,
                                "status": "completed",
                                "duration_ms": int((time.time() - t0) * 1000),
                                "ts": time.time(),
                            }
                        )
                        return out
                    except BaseException as exc:
                        # BaseException 含 CancelledError —— 取消也要发 failed 进度。
                        # 区分主动取消 vs 真失败：取消时带 reason="cancelled"，前端据此
                        # 渲染「🚫 已取消」而非「❌ 失败」（与排队期取消的归一一致）。
                        _ev = {
                            "run_id": run_id,
                            "kind": kind,
                            "task_id": task_id,
                            "parent_sid": parent_sid,
                            "status": "failed",
                            "duration_ms": int((time.time() - t0) * 1000),
                            "ts": time.time(),
                        }
                        if isinstance(exc, asyncio.CancelledError):
                            _ev["reason"] = "cancelled"
                            self._total_rejected += 1  # WI-OC-2 运行中被取消计入拒绝
                        self._emit(_ev)
                        raise
                    finally:
                        self._running -= 1
        except BaseException:
            # 排队阶段（尚未进 running）被取消/异常：内层 except 没机会跑，
            # 否则前端 SubagentProgressPanel 该行永远停在 "queued"。在此补发一条
            # 终态 failed 进度并修复 queued 计数泄漏，再 re-raise 保持取消语义。
            if not entered_running:
                self._queued -= 1
                self._total_rejected += 1  # WI-OC-2 排队中被取消计入拒绝
                self._emit(
                    {
                        "run_id": run_id,
                        "kind": kind,
                        "task_id": task_id,
                        "parent_sid": parent_sid,
                        "status": "failed",
                        "reason": "cancelled",
                        "duration_ms": int((time.time() - t_queued) * 1000),
                        "ts": time.time(),
                    }
                )
            raise

    def snapshot(self) -> dict[str, int]:
        """当前调度状态快照（observability）。

        瞬时字段：``running`` / ``queued``（既有，BC 不变）。
        WI-OC-2 累计字段：``peak_concurrent`` / ``total_queued`` /
        ``total_rejected`` + 队列等待分位 ``lane_wait_p50_ms`` /
        ``lane_wait_p95_ms``（无样本时为 0）。
        """
        return {
            "running": self._running,
            "queued": self._queued,
            "peak_concurrent": self._peak_concurrent,
            "total_queued": self._total_queued,
            "total_rejected": self._total_rejected,
            "lane_wait_p50_ms": int(self._percentile(self._lane_wait_ms, 0.50)),
            "lane_wait_p95_ms": int(self._percentile(self._lane_wait_ms, 0.95)),
        }


__all__ = ["SubagentScheduler"]
