# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Replay QA set against Retriever and compute hit@k / MRR.

Definitions
-----------
* **hit@k**: 1 if ``expected_msg_id`` appears in the top-k retrieved
  results, 0 otherwise. We report hit@1, hit@5, hit@10 by default.
* **MRR** (Mean Reciprocal Rank): mean of ``1/rank`` over all QAs.
  A QA where the target sits at rank 1 contributes 1.0; rank 5 → 0.2;
  not-in-top-k → 0.

We deliberately do NOT compute precision/recall-at-k beyond hit@k,
because each QA has exactly one ground-truth target — those metrics
collapse to hit@k / k.

Tolerance
---------
``_TOPK_FOR_MRR`` is the cap we look at for MRR (default 20). Above
that, contributions are 0. This matches what users would actually see
in the rendered context.

Output
------
:class:`EvalReport` is a dataclass with all metrics + the raw per-item
results. ``run()`` also persists a row to ``memory_eval_run`` for
historical comparison.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

import aiosqlite

from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

log = logging.getLogger(__name__)


_TOPK_FOR_MRR = 20


class RetrieverProto(Protocol):
    """Duck-typed surface that both real and fake retrievers expose."""

    async def recall(
        self,
        query: str,
        top_k: int | None = ...,
        **kwargs: Any,
    ) -> list[Any]: ...


@dataclass
class PerItemResult:
    qa_id: int
    query: str
    expected_msg_id: int
    hit_rank: int  # 1-based; 0 means "not in top-k window"
    hit_at_1: bool
    hit_at_5: bool
    hit_at_10: bool
    reciprocal_rank: float
    returned_ids: list[int]
    # 记忆系统升级 WI-M0.2 / D11: 召回结果渲染进 system prompt 的文本
    # token 估算（仅 L3/facts 段，不含 L1 静态档案、不含对话历史）。
    rendered_tokens: int = 0


@dataclass
class EvalReport:
    qa_set_size: int
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    mrr: float
    duration_ms: float
    # 记忆系统升级 WI-M0.2 / D11: 平均每 query 召回结果渲染进 system
    # prompt 的文本 token。门控指标 —— 召回链路改动后增幅须 ≤ +30%。
    token_per_query: float = 0.0
    per_item: list[PerItemResult] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "qa_set_size": self.qa_set_size,
            "hit@1": round(self.hit_at_1, 4),
            "hit@5": round(self.hit_at_5, 4),
            "hit@10": round(self.hit_at_10, 4),
            "mrr": round(self.mrr, 4),
            "token_per_query": round(self.token_per_query, 2),
            "duration_ms": round(self.duration_ms, 2),
        }


class MetricsRunner:
    """Replay every QA against a retriever; aggregate; persist."""

    def __init__(
        self,
        db_path: str | Path,
        retriever: RetrieverProto,
        *,
        config_snapshot: Optional[dict[str, Any]] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._retriever = retriever
        self._config_snapshot = config_snapshot or {}

    async def run(
        self,
        *,
        max_items: int = 200,
        top_k: int = _TOPK_FOR_MRR,
        notes: str = "",
    ) -> EvalReport:
        await ensure_memory_v2_tables(self._db_path)
        qa_items = await self._load_qa(max_items)
        if not qa_items:
            log.warning(
                "MetricsRunner.run: qa set is empty; "
                "build one first with QASetBuilder.build()"
            )
            return EvalReport(
                qa_set_size=0,
                hit_at_1=0.0,
                hit_at_5=0.0,
                hit_at_10=0.0,
                mrr=0.0,
                duration_ms=0.0,
                config=self._config_snapshot,
            )

        started = time.monotonic()
        run_id = await self._open_run(len(qa_items))
        per_item: list[PerItemResult] = []
        for qa in qa_items:
            res = await self._evaluate_one(qa, top_k=top_k)
            per_item.append(res)
        elapsed_ms = (time.monotonic() - started) * 1000.0

        n = len(per_item)
        report = EvalReport(
            qa_set_size=n,
            hit_at_1=sum(1 for r in per_item if r.hit_at_1) / n,
            hit_at_5=sum(1 for r in per_item if r.hit_at_5) / n,
            hit_at_10=sum(1 for r in per_item if r.hit_at_10) / n,
            mrr=sum(r.reciprocal_rank for r in per_item) / n,
            token_per_query=sum(r.rendered_tokens for r in per_item) / n,
            duration_ms=elapsed_ms,
            per_item=per_item,
            config=self._config_snapshot,
        )
        await self._close_run(run_id, report, notes=notes)
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_qa(self, limit: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT id, query, expected_msg_id FROM memory_qa_set "
                "ORDER BY id LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [
            {"id": int(r[0]), "query": r[1], "expected": int(r[2])}
            for r in rows
        ]

    async def _evaluate_one(
        self, qa: dict[str, Any], *, top_k: int
    ) -> PerItemResult:
        hits = await self._retriever.recall(qa["query"], top_k=top_k)
        returned_ids = [_extract_message_id(h) for h in hits]
        # Strip Nones — retriever shouldn't return them but be defensive.
        returned_ids = [i for i in returned_ids if i is not None]
        target = qa["expected"]
        try:
            rank = returned_ids.index(target) + 1
        except ValueError:
            rank = 0
        return PerItemResult(
            qa_id=qa["id"],
            query=qa["query"],
            expected_msg_id=target,
            hit_rank=rank,
            hit_at_1=(rank == 1),
            hit_at_5=(1 <= rank <= 5),
            hit_at_10=(1 <= rank <= 10),
            reciprocal_rank=(1.0 / rank) if rank > 0 else 0.0,
            returned_ids=returned_ids[:top_k],
            rendered_tokens=_rendered_tokens(hits[:top_k]),
        )

    async def _open_run(self, qa_size: int) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO memory_eval_run("
                "started_at, qa_set_size, config_json"
                ") VALUES (?, ?, ?)",
                (time.time(), qa_size, json.dumps(self._config_snapshot)),
            )
            run_id = int(cur.lastrowid or 0)
            await cur.close()
            await db.commit()
        return run_id

    async def _close_run(
        self, run_id: int, report: EvalReport, *, notes: str
    ) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE memory_eval_run SET finished_at = ?, "
                "metrics_json = ?, notes = ? WHERE id = ?",
                (
                    time.time(),
                    json.dumps(report.as_dict()),
                    notes or None,
                    run_id,
                ),
            )
            await db.commit()


def _rendered_tokens(hits: Iterable[Any]) -> int:
    """Estimate the token cost of rendering ``hits`` into the system prompt.

    记忆系统升级 WI-M0.2 / PRD D11: 量的是 L3/facts 召回段渲染进 system
    prompt 的文本 token —— 不含 L1 静态档案、不含对话历史。复刻
    ``assembler/components/memory.py`` 的渲染（``_render_l3_only``）与
    粗略 tokenizer（``_approx_tokens``: 1 token ≈ 4 chars），口径与 agent
    一致。空召回 → 0 token（不渲染段头）。
    """
    lines: list[str] = []
    for h in hits:
        text = getattr(h, "text", None)
        if text is None and isinstance(h, dict):
            text = h.get("text")
        text = (str(text) if text else "").strip()
        if not text:
            continue
        if len(text) > 240:
            text = text[:240] + "…"
        src = getattr(h, "source", None)
        if src is None and isinstance(h, dict):
            src = h.get("source")
        score = getattr(h, "score", None)
        if score is None and isinstance(h, dict):
            score = h.get("score")
        score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "?"
        lines.append(f"- [{src or '?'} {score_str}] {text}")
    if not lines:
        return 0
    block = "## 相关记忆片段 (L3, RRF recall)\n\n" + "\n".join(lines)
    return max(1, len(block) // 4)


def _extract_message_id(hit: Any) -> Optional[int]:
    """Pull message_id from whatever shape the retriever returned."""
    if hit is None:
        return None
    # dataclass / Hit-like
    mid = getattr(hit, "message_id", None)
    if mid is not None:
        try:
            return int(mid)
        except (TypeError, ValueError):
            return None
    if isinstance(hit, dict):
        v = hit.get("message_id") or hit.get("id")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None
