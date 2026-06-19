# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""superpowers Layer 1B — 偏好记忆(BGE-M3 语义匹配)。

决策1/2 的"第一次问/确认，后续相同的直接做"靠这个组件落地:

* **计划记忆 (kind="plan")** — 用户在 plan-confirm 硬门点 [执行] → record(任务文本,
  "approved")。下次来一个 *语义相似* 的任务，在挂门前 match 命中 → **自动确认**
  (跳过等待)，省得每次点。
* **意图记忆 (kind="intent")** — 记 {请求 → "ask"|"task"}。用户问"你用什么模型"这类
  纯提问被记成 "ask"，后续同类免再走澄清；派活记成 "task"。

匹配用复用的 BGE-M3 embedder 算 cosine 相似度，≥ 阈值(默认 0.86)算命中。
存储是单用户桌宠的本地 JSON(``<userdata>/preference_memory.json``)。

误记防护(决策1/2 的风险标注):
* 只在**明确信号**时写(plan: 用户真点了[执行]; intent: 一轮干净的 outcome)。
* 记忆**可查看/可清除**(``list_entries`` / ``clear``，前端 /prefs 入口)。
* 每个 (kind, text) 去重(同文本只留最新)，避免同一请求灌爆。
"""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

# embed_fn: async (list[str]) -> list[list[float]] — 复用 memory.embedder.embed
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

# WI-3.3: 半衰期衰减率（per day）。对齐 preference 类别 decay_rate 0.005。
# 200 天老偏好衰减到 exp(-0.005*200) ≈ 0.37，依旧命中但排序靠后。
_PREF_DECAY_RATE: float = 0.005


def _cosine(a: list[float], b: list[float]) -> float:
    """L2-normalized cosine. BGE-M3 一般已归一化，这里仍防御性归一。"""
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return -1.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class PreferenceMemory:
    """语义偏好记忆 — 计划记忆 + 意图记忆。"""

    def __init__(
        self,
        path: Path,
        embed_fn: EmbedFn,
        *,
        threshold: float = 0.86,
        max_entries: int = 500,
        now_fn: Callable[[], float] = time.time,
        pref_decay: bool = False,
    ) -> None:
        self._path = Path(path)
        self._embed = embed_fn
        self._threshold = float(threshold)
        self._max = int(max_entries)
        self._now = now_fn
        # WI-3.3: recency decay gate. False (default) → legacy behaviour unchanged.
        self._pref_decay = bool(pref_decay)
        self._entries: list[dict[str, Any]] = self._load()

    # ---- persistence ----------------------------------------------------
    def _load(self) -> list[dict[str, Any]]:
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [e for e in data if isinstance(e, dict)]
        except Exception as exc:  # noqa: BLE001
            log.warning("preference_memory load failed: %s", exc)
        return []

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._entries, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("preference_memory save failed: %s", exc)

    # ---- embedding ------------------------------------------------------
    async def _vec(self, text: str) -> Optional[list[float]]:
        text = (text or "").strip()
        if not text:
            return None
        try:
            out = await self._embed([text])
        except Exception as exc:  # noqa: BLE001
            log.warning("preference_memory embed failed: %s", exc)
            return None
        if not out or not out[0]:
            return None
        return list(map(float, out[0]))

    # ---- public API -----------------------------------------------------
    async def record(
        self, text: str, label: str, kind: str, *, pin: bool = False,
    ) -> bool:
        """记一条偏好。同 (kind, 归一化 text) 去重，只留最新。返回是否写入。

        WI-3.3: optional ``pin=True`` → entry 加 ``pinned=True``，
        match() 时该条 decay=1.0（不衰减）。旧 JSON 无此字段 → 当 False（BC）。
        """
        vec = await self._vec(text)
        if vec is None:
            return False
        norm = (text or "").strip()
        # 去重：删掉同 kind 且文本完全相同的旧条目
        self._entries = [
            e for e in self._entries
            if not (e.get("kind") == kind and (e.get("text") or "").strip() == norm)
        ]
        entry: dict[str, Any] = {
            "text": norm,
            "embedding": vec,
            "label": label,
            "kind": kind,
            "ts": self._now(),
        }
        if pin:
            entry["pinned"] = True
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]
        self._save()
        log.info("preference_memory recorded kind=%s label=%s text=%r",
                 kind, label, norm[:60])
        return True

    async def match(self, text: str, kind: str) -> Optional[dict[str, Any]]:
        """语义匹配同 kind 的最相似条目；≥ 阈值返回 {label, score, text}，否则 None。

        WI-3.3 recency decay（仅 pref_decay=True 时生效）：
        - 阈值比较用**原始 cosine**（保命中精度 — 老偏好仍能命中）。
        - 排序选 best 用 **effective = cosine * decay**（老偏好沉底）。
        - pinned 条目 decay=1.0（不衰减）。
        - 旧 JSON 无 pinned 字段 → 当 False（BC，不崩）。
        """
        vec = await self._vec(text)
        if vec is None:
            return None
        best: Optional[dict[str, Any]] = None
        best_score = -1.0       # raw cosine — for threshold check
        best_effective = -1.0   # effective (decay-adjusted) — for ranking
        now = self._now()
        for e in self._entries:
            if e.get("kind") != kind:
                continue
            cosine = _cosine(vec, e.get("embedding") or [])
            if self._pref_decay:
                ts = e.get("ts") or now
                age_days = max(0.0, (now - float(ts)) / 86400.0)
                if e.get("pinned", False):
                    decay = 1.0
                else:
                    decay = math.exp(-_PREF_DECAY_RATE * age_days)
                effective = cosine * decay
            else:
                effective = cosine
            # Rank by effective score; tie-break doesn't matter
            if effective > best_effective:
                best_effective = effective
                best_score = cosine
                best = e
        if best is not None and best_score >= self._threshold:
            return {"label": best.get("label"), "score": best_score,
                    "text": best.get("text")}
        return None

    def list_entries(self, kind: Optional[str] = None) -> list[dict[str, Any]]:
        """查看（不含 embedding，给 /prefs UI）。"""
        return [
            {"text": e.get("text"), "label": e.get("label"),
             "kind": e.get("kind"), "ts": e.get("ts")}
            for e in self._entries
            if kind is None or e.get("kind") == kind
        ]

    def clear(self, kind: Optional[str] = None) -> int:
        """清除偏好（kind=None 全清）。返回清除条数。"""
        before = len(self._entries)
        if kind is None:
            self._entries = []
        else:
            self._entries = [e for e in self._entries if e.get("kind") != kind]
        removed = before - len(self._entries)
        if removed:
            self._save()
        return removed
