"""WorkspaceMemoryComponent — 记忆系统升级 WI-M1.6。

把 Phase D 的 ``WorkspaceMemoryStore`` 接进 assembler：code 任务装配
context 时，注入「本 session 已读/已写过哪些文件 + 摘要」，让 agent 不必
盲目重读早先处理过的文件。

与既有 ``components/workspace.py`` 的区别（命名刻意区分）：
  * ``WorkspaceComponent``       —— 枚举工作目录磁盘文件（无状态快照）。
  * ``WorkspaceMemoryComponent`` —— 本 session 内 agent 自己的文件动作
    历史（persisted scratchpad，来自 file_read/file_write 工具记录）。

flag ``memory.v2.workspace_memory`` 关时 main.py 不构造 store → 本组件
拿到 ``store=None`` → 返回空 Slice（Strangler-Fig：assembler 不受影响）。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext

_MAX_ENTRIES = 15


class WorkspaceMemoryComponent:
    """注入本 session 的文件工作记忆。"""

    name: str = "workspace_memory"

    def __init__(self, store: Any | None = None) -> None:
        # WorkspaceMemoryStore 实例；None = flag 关 / 未注入 → 组件空转。
        self._store = store

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        if self._store is None:
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=45,
                bucket="dynamic",
                meta={"status": "no_store"},
            )
        session_id = ctx.session_id
        if not session_id:
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=45,
                bucket="dynamic",
                meta={"status": "no_session"},
            )
        try:
            rows = await self._store.list_session(
                session_id, limit=_MAX_ENTRIES,
            )
        except Exception as exc:  # noqa: BLE001 — 组件不得 raise
            return Slice(
                component_name=self.name,
                text_content="",
                priority=45,
                bucket="dynamic",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )
        if not rows:
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=45,
                bucket="dynamic",
                meta={"status": "empty", "entries": 0},
            )

        lines = ["## 本次任务已处理的文件 (工作记忆)"]
        for r in rows:
            path = r.get("path") or "?"
            action = r.get("last_action") or "?"
            summary = (r.get("content_summary") or "").strip()
            line = f"- [{action}] {path}"
            if summary:
                if len(summary) > 80:
                    summary = summary[:80] + "…"
                line += f" — {summary}"
            lines.append(line)
        lines.append(
            "（重读这些文件前先 workspace_recall 查一下，避免重复读取。）"
        )
        text = "\n".join(lines)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return Slice(
            component_name=self.name,
            text_content=text,
            tokens=max(1, len(text) // 4),
            priority=45,
            bucket="dynamic",
            meta={
                "entries": len(rows),
                "latency_ms": round(elapsed_ms, 2),
            },
        )


_ASSERT_PROTOCOL: Component = WorkspaceMemoryComponent()
