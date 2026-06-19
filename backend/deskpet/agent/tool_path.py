# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""WI-1.6 — 工具路径录制（喂 FP-5 的 4.3 技能自创）。

本 FP 只录不消费：记录每个目标完成时走过的工具序列 + ok/corrected/
recovered 标记 + goal_text。消费者（4.3 触发器）在 FP-5。纯内存 per-run，
不落库（持久化由 FP-5 按需决定）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolStep:
    name: str
    ok: bool = True
    corrected: bool = False   # 被用户/反思纠正过
    recovered: bool = False   # 从错误中恢复（先 fail 后 ok）


@dataclass
class ToolPath:
    session_id: str
    goal_id: str
    goal_text: str
    steps: list[ToolStep] = field(default_factory=list)


class ToolPathRecorder:
    """per-session 活跃工具序缓冲；complete() 时快照为 ToolPath。"""

    def __init__(self) -> None:
        self._active: dict[str, list[ToolStep]] = {}
        # (session_id, goal_id) -> ToolPath
        self._completed: dict[tuple[str, str], ToolPath] = {}

    def record_tool(
        self, session_id: str, *, name: str,
        ok: bool = True, corrected: bool = False, recovered: bool = False,
    ) -> None:
        self._active.setdefault(session_id, []).append(
            ToolStep(name=name, ok=ok, corrected=corrected, recovered=recovered)
        )

    def complete(
        self, session_id: str, *, goal_id: str, goal_text: str,
    ) -> ToolPath:
        steps = self._active.pop(session_id, [])
        path = ToolPath(
            session_id=session_id, goal_id=goal_id,
            goal_text=goal_text, steps=steps,
        )
        self._completed[(session_id, goal_id)] = path
        return path

    def get_completed_path(
        self, session_id: str, goal_id: str,
    ) -> Optional[ToolPath]:
        """冻结 §1.4 契约。无记录 → None。"""
        return self._completed.get((session_id, goal_id))


__all__ = ["ToolStep", "ToolPath", "ToolPathRecorder"]
