"""TaskClassifier — three-tier cascade (P4-S7 tasks 12.1-12.4).

Stages in order:

1. **Rule** (< 2ms, task 12.2) — cheap deterministic patterns that
   short-circuit the cascade. Examples: ``/`` prefix → ``command``,
   "还记得/记得/之前" → ``recall``, imperative verbs → ``task``.

2. **Embed** (≤ 15ms, task 12.3) — cosine similarity vs an exemplar
   pool (~100 hand-labelled sentences loaded from
   ``policies/exemplars.jsonl``). Highest-scoring exemplar's label
   wins when its score > ``embed_threshold`` (default 0.75).

3. **LLM** (≤ 300ms, task 12.4) — final fallback. Calls
   ``claude-haiku-4-5`` via the injected LLM registry with a tight
   system prompt asking it to return one of the 8 task_types. D8:
   LLM tier is ENABLED by default; can be disabled by setting
   ``config.context.assembler.classifier_mode = ["rule", "embed"]``.

If all three tiers fail to identify a task type, classifier falls back
to ``chat`` (spec "Unknown task_type falls back to chat").
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import structlog

from deskpet.agent.assembler.bundle import TASK_TYPES

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Classifier result
# ---------------------------------------------------------------------------
@dataclass
class ClassifierResult:
    """Outcome of :meth:`TaskClassifier.classify`."""

    task_type: str
    path: str  # "rule" | "embed" | "llm" | "default"
    confidence: float
    latency_ms: float
    rationale: str = ""


# ---------------------------------------------------------------------------
# Rule tier
# ---------------------------------------------------------------------------
# Order matters — first match wins. Patterns are cheap regex / substring.
# Keep this small; embed tier is the catch-all.
_RULE_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"^\s*/\S"), "command", "slash-command prefix"),
    (
        re.compile(r"(还记得|记得|之前.{0,5}(说|提|讲))"),
        "recall",
        "memory-probe trigger",
    ),
    (
        re.compile(
            r"(写.{0,4}代码|修.{0,12}bug|debug|报错|stack\s*trace|python|javascript|typescript)",
            re.IGNORECASE,
        ),
        "code",
        "code/debug keyword",
    ),
    (
        re.compile(r"(搜索|查一下|帮我查|百度|谷歌|search|google)", re.IGNORECASE),
        "web_search",
        "web-search keyword",
    ),
    (
        re.compile(r"(计划|规划|安排.{0,3}(行程|任务|日程)|todo|待办)", re.IGNORECASE),
        "plan",
        "plan/todo keyword",
    ),
    (
        re.compile(r"(难过|开心|生气|抑郁|emo|心情|我觉得|孤单)", re.IGNORECASE),
        "emotion",
        "emotional-state keyword",
    ),
)


def _rule_classify(text: str) -> Optional[tuple[str, str]]:
    """Match against ``_RULE_PATTERNS``. Returns ``(task_type, rationale)`` or None."""
    if not text:
        return None
    for pattern, task_type, rationale in _RULE_PATTERNS:
        if pattern.search(text):
            return task_type, rationale
    return None


# ---------------------------------------------------------------------------
# Embed tier — exemplars
# ---------------------------------------------------------------------------
@dataclass
class _Exemplar:
    text: str
    label: str
    vector: Optional[list[float]] = None  # lazy-filled


class _ExemplarPool:
    """Lazy-loaded exemplars from ``policies/exemplars.jsonl``.

    File format: one JSON object per line, ``{"text": "...", "label": "chat"}``.
    Missing file → empty pool → embed tier silently skipped.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._exemplars: Optional[list[_Exemplar]] = None

    def load(self) -> list[_Exemplar]:
        if self._exemplars is not None:
            return self._exemplars
        exemplars: list[_Exemplar] = []
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        text = obj.get("text")
                        label = obj.get("label")
                        if (
                            isinstance(text, str)
                            and text
                            and isinstance(label, str)
                            and label in TASK_TYPES
                        ):
                            exemplars.append(_Exemplar(text=text, label=label))
            except OSError as exc:
                logger.warning(
                    "classifier.exemplars_io_error",
                    path=str(self._path),
                    error=str(exc),
                )
        self._exemplars = exemplars
        return exemplars

    async def ensure_vectors(self, embedder: Any) -> list[_Exemplar]:
        """Embed any exemplar that hasn't been vectorised yet."""
        items = self.load()
        pending = [e for e in items if e.vector is None]
        if not pending or embedder is None:
            return items
        try:
            vectors = await embedder.embed([e.text for e in pending])
        except Exception as exc:
            logger.warning(
                "classifier.exemplar_embed_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return items
        for e, v in zip(pending, vectors):
            e.vector = list(v)
        return items


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)


# ---------------------------------------------------------------------------
# TaskClassifier
# ---------------------------------------------------------------------------
class TaskClassifier:
    """Three-tier cascade classifier.

    Parameters
    ----------
    embedder:
        Anything with ``async embed(list[str]) -> list[list[float]]``.
        ``None`` → embed tier skipped.
    llm_registry:
        Anything with ``async chat_with_fallback(messages, ...)`` returning
        an object with ``.content`` text. ``None`` → LLM tier skipped.
    exemplars_path:
        Path to ``exemplars.jsonl``. Default: the packaged
        ``policies/exemplars.jsonl``.
    modes:
        Tiers to run, in order. Default ``("rule", "embed", "llm")``.
        Disable LLM tier by passing ``("rule", "embed")`` (D8 opt-out).
    embed_threshold:
        Cosine similarity above which embed tier returns a hit. 0.75 per spec.
    llm_model:
        Model name for the LLM fallback. Default ``"claude-haiku-4-5"`` (D8).
    """

    def __init__(
        self,
        *,
        embedder: Any = None,
        llm_registry: Any = None,
        exemplars_path: Optional[Path] = None,
        modes: tuple[str, ...] = ("rule", "embed", "llm"),
        embed_threshold: float = 0.75,
        llm_model: str = "claude-haiku-4-5",
        llm_timeout_s: float = 2.0,
    ) -> None:
        self._embedder = embedder
        self._llm = llm_registry
        self._modes = modes
        self._embed_threshold = embed_threshold
        self._llm_model = llm_model
        self._llm_timeout_s = llm_timeout_s
        self._exemplars = _ExemplarPool(
            exemplars_path
            or Path(__file__).parent / "policies" / "exemplars.jsonl"
        )

    async def classify(self, user_message: str) -> ClassifierResult:
        start = time.monotonic()

        if "rule" in self._modes:
            rule_hit = _rule_classify(user_message)
            if rule_hit is not None:
                task_type, rationale = rule_hit
                return ClassifierResult(
                    task_type=task_type,
                    path="rule",
                    confidence=1.0,
                    latency_ms=(time.monotonic() - start) * 1000.0,
                    rationale=rationale,
                )

        if "embed" in self._modes and self._embedder is not None:
            embed_result = await self._embed_tier(user_message, start)
            if embed_result is not None:
                return embed_result

        if "llm" in self._modes and self._llm is not None:
            llm_result = await self._llm_tier(user_message, start)
            if llm_result is not None:
                return llm_result

        return ClassifierResult(
            task_type="chat",
            path="default",
            confidence=0.0,
            latency_ms=(time.monotonic() - start) * 1000.0,
            rationale="no tier matched",
        )

    async def _embed_tier(
        self, user_message: str, start: float
    ) -> Optional[ClassifierResult]:
        exemplars = await self._exemplars.ensure_vectors(self._embedder)
        exemplars = [e for e in exemplars if e.vector is not None]
        if not exemplars:
            return None

        try:
            query_vec_list = await self._embedder.embed([user_message])
        except Exception as exc:
            logger.warning(
                "classifier.query_embed_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if not query_vec_list:
            return None
        query_vec = list(query_vec_list[0])

        best_score = -1.0
        best_label: Optional[str] = None
        for e in exemplars:
            if e.vector is None:
                continue
            score = _cosine(query_vec, e.vector)
            if score > best_score:
                best_score = score
                best_label = e.label

        if best_label is None:
            return None

        if best_score >= self._embed_threshold:
            return ClassifierResult(
                task_type=best_label,
                path="embed",
                confidence=best_score,
                latency_ms=(time.monotonic() - start) * 1000.0,
                rationale=f"exemplar cosine={best_score:.3f}",
            )

        # Below threshold — let LLM tier take over.
        return None

    async def _llm_tier(
        self, user_message: str, start: float
    ) -> Optional[ClassifierResult]:
        prompt = _LLM_CLASSIFY_SYSTEM
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_message},
        ]
        try:
            response = await asyncio.wait_for(
                self._llm.chat_with_fallback(
                    messages,
                    model=self._llm_model,
                    max_tokens=32,
                    temperature=0.0,
                ),
                timeout=self._llm_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "classifier.llm_timeout", timeout_s=self._llm_timeout_s
            )
            return None
        except Exception as exc:
            logger.warning(
                "classifier.llm_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        content = str(getattr(response, "content", "") or "").strip().lower()
        # The LLM prompt says "ONLY the task-type word". Be strict —
        # tolerate only trailing punctuation / quotes, NOT arbitrary
        # phrases that happen to contain a task-type word.
        #   ✔ "task"       → accept
        #   ✔ "task."      → accept
        #   ✘ "do a task"  → reject (LLM was supposed to echo one word)
        #   ✘ "gibberish_not_a_task_type" → reject
        stripped = content.strip(" \t\n\r\"'.,;:!?`()[]{}")
        if stripped in TASK_TYPES:
            return ClassifierResult(
                task_type=stripped,
                path="llm",
                confidence=0.5,
                latency_ms=(time.monotonic() - start) * 1000.0,
                rationale=f"llm returned {content!r}",
            )
        # LLM returned garbage — fall through to default 'chat'.
        logger.warning(
            "classifier.llm_unknown_task_type",
            content=content,
        )
        return None


_LLM_CLASSIFY_SYSTEM = (
    "You classify a user utterance into EXACTLY ONE of these task types:\n"
    "  chat, recall, task, code, web_search, plan, emotion, command\n"
    "Rules:\n"
    "- 'recall' = user asks what we discussed / remembered before.\n"
    "- 'task' = user requests an action (write, schedule, generate, ...).\n"
    "- 'code' = about code, programming, or debugging.\n"
    "- 'web_search' = needs external web lookup.\n"
    "- 'plan' = user asks for a plan / todo / schedule.\n"
    "- 'emotion' = user expresses feelings / seeks comfort.\n"
    "- 'command' = slash command or explicit control directive.\n"
    "- 'chat' = casual conversation, none of the above.\n"
    "Respond with ONLY the task type word, nothing else."
)
