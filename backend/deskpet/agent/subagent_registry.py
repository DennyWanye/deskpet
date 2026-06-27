# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""SubagentRegistry — 非阻塞子代理 run 记录 + completion queue + 取消级联。

plan: plans/2026-06-21-subagent-concurrency-driver/ WI-3.1

偷师 Hermes 的 process_registry / OpenClaw 的 registerSubagentRun：
* ``register`` 在 spawn 返回前**同步**持有 ``asyncio.Task`` 强引用（防 GC，gap4）。
* 子代理完成 → ``complete``/``fail`` 把 run 入 ``completion_queue``，由 agent_loop
  在回合边界 drain 并注入父上下文（模式 2，回合边界注入）。
* ``cancel_all`` 供 ``/stop`` 取消级联（D10/V5）：取消所有活 run 的 task。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_ACTIVE = ("queued", "running")


@dataclass
class SubagentRun:
    run_id: str
    kind: str
    task_id: str
    status: str = "queued"  # queued | running | completed | failed | cancelled
    task: "asyncio.Task | None" = None
    summary: str = ""
    stats: dict = field(default_factory=dict)
    error: str | None = None

    def to_result(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "status": self.status,
            "output": self.summary if self.status == "completed" else "",
            "error": self.error,
        }


class SubagentRegistry:
    """非阻塞子代理 run 的中央登记 + 完成队列 + 取消级联。"""

    def __init__(self) -> None:
        self._runs: dict[str, SubagentRun] = {}
        self.completion_queue: "asyncio.Queue[SubagentRun]" = asyncio.Queue()

    # --- 登记 / 查询 -----------------------------------------------------
    def register(self, run: SubagentRun) -> None:
        """spawn 返回前同步调用，持 Task 强引用防 GC（gap4）。"""
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> "SubagentRun | None":
        return self._runs.get(run_id)

    def list(self, *, active_only: bool = False) -> list[SubagentRun]:
        runs = list(self._runs.values())
        if active_only:
            return [r for r in runs if r.status in _ACTIVE]
        return runs

    def set_running(self, run_id: str) -> None:
        r = self._runs.get(run_id)
        if r is not None:
            r.status = "running"

    # --- 完成 / 失败（入 completion queue） ------------------------------
    def complete(self, run_id: str, *, summary: str, stats: dict | None = None) -> None:
        r = self._runs.get(run_id)
        if r is None:
            return
        r.status = "completed"
        r.summary = summary or ""
        if stats:
            r.stats = stats
        self.completion_queue.put_nowait(r)

    def fail(self, run_id: str, error: str) -> None:
        r = self._runs.get(run_id)
        if r is None:
            return
        r.status = "failed"
        r.error = str(error)
        r.summary = f"[失败] {error}"
        self.completion_queue.put_nowait(r)

    # --- 取消级联（绑 /stop，D10/V5） -----------------------------------
    def cancel_all(self) -> int:
        n = 0
        for r in self._runs.values():
            if r.status in _ACTIVE and r.task is not None:
                try:
                    r.task.cancel()
                    r.status = "cancelled"
                    n += 1
                except Exception:  # noqa: BLE001 — 取消尽力而为
                    continue
        if n:
            log.info("subagent_cancel_all n=%d", n)
        return n


__all__ = ["SubagentRun", "SubagentRegistry"]
