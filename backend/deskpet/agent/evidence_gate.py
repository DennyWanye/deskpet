# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step2 EvidenceGate — 先调查·后发言（没有调查就没有发言权 + 实事求是，核心闸①）。

IN-LOOP 硬门：当 needs_investigation=True，且模型在「尚未发生任何取证类工具调用」
就想 end_turn 下结论时，拦截并注入 <调查> nudge，逼它先取证。

设计纪律（对齐 VerifyGate/completion_probe 的 nudge 上限语义）：
  - 纯同步、无 LLM 调用（轻量，不进延迟预算）。
  - max_nudges 上限：超限放行避免死循环，记 evidence_gate_exhausted。
  - 工具白名单可配（investigative_tools）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import structlog

logger = structlog.get_logger(__name__)

# 算「取证」的工具白名单（可被 config.evidence.investigative_tools 覆盖）
_DEFAULT_INVESTIGATIVE_TOOLS: frozenset[str] = frozenset({
    "search", "web_search", "read", "read_file", "grep", "glob",
    "inspect", "retrieve", "fetch_url", "list_files", "deepresearch",
    "recall", "memory_search",
})


@dataclass
class EvidenceDecision:
    blocked: bool
    reason: str = ""
    nudge: str = ""          # blocked=True 时要注入的 system nudge
    nudge_count: int = 0
    exhausted: bool = False


class EvidenceGate:
    """end_turn 前调用。flag off 时调用方传 None 不构造。"""

    def __init__(
        self,
        *,
        investigative_tools: Iterable[str] | None = None,
        max_nudges: int = 2,
    ) -> None:
        self._tools = frozenset(investigative_tools) if investigative_tools else _DEFAULT_INVESTIGATIVE_TOOLS
        self._max_nudges = max_nudges

    def is_investigative(self, tool_name: str) -> bool:
        """该 tool 是否算「取证」（白名单命中）。agent_loop dispatch 时调它置 evidence_gathered 布尔。
        把白名单判定收口到 gate 内部，避免 agent_loop 重复持有白名单。"""
        return tool_name in self._tools

    def check(
        self,
        *,
        needs_investigation: bool,
        evidence_gathered: bool,
        nudges_used: int,
    ) -> EvidenceDecision:
        """判定是否拦截 end_turn。

        ⚠️ R1（round-2）：入参从 `tool_names_so_far`（依赖 working_messages 切片，对 compaction 不鲁棒）
        改为 `evidence_gathered: bool`（agent_loop 在 dispatch 时按白名单 + history 快照累积置位）。
        BLOCK 条件：needs_investigation && not evidence_gathered && nudges_used < max。
        """
        if not needs_investigation:
            return EvidenceDecision(blocked=False, reason="no_investigation_needed")

        if evidence_gathered:
            return EvidenceDecision(blocked=False, reason="evidence_present")

        if nudges_used >= self._max_nudges:
            logger.warning("evidence_gate_exhausted", nudges_used=nudges_used)
            return EvidenceDecision(
                blocked=False, reason="exhausted",
                nudge_count=nudges_used, exhausted=True,
            )

        nudge = (
            "<调查>\n"
            "你还没做任何取证调查就要下结论。**没有调查就没有发言权**——"
            f"先用取证类工具（{', '.join(sorted(self._tools)[:6])} 等）查清事实再回答。"
        )
        logger.info("evidence_gate.blocked", nudges_used=nudges_used + 1)
        return EvidenceDecision(
            blocked=True, reason="no_evidence",
            nudge=nudge, nudge_count=nudges_used + 1,
        )


__all__ = ["EvidenceGate", "EvidenceDecision"]
