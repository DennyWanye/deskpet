"""TTS pre-narration hook (P4-S7 tasks 12.14, 12.15).

Goal (spec design.md §TTS): user hears the pet speak within 500ms of
hitting send, even when the LLM first-chunk takes longer. We do this by
speaking a short filler ("嗯..." / "让我查一下...") on turn start,
*then* letting the real model stream carry the response.

The pre-narration speaker is fire-and-forget; if TTS fails we log a
warning and the turn carries on unchanged. No awaiting — the assembler
returns immediately.

Integration point: ``agent.run_conversation`` calls
``await assembler.assemble(...)`` and THEN, before entering the loop,
``prenarration.speak(task_type)``.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Callable, Optional, Sequence

import structlog

logger = structlog.get_logger(__name__)


# Spec task 12.15 — fixed 2-phrase pool for v1. Phase 4-S11 UX pass
# will replace with contextually-selected lines by task_type.
_DEFAULT_PHRASES: dict[str, tuple[str, ...]] = {
    "chat": ("嗯...", "让我想想..."),
    "recall": ("让我查一下...", "翻一下记忆..."),
    "task": ("好的...", "马上来..."),
    "code": ("让我看看...", "稍等一下..."),
    "web_search": ("我来查一下...", "搜搜看..."),
    "plan": ("好的...", "让我整理一下..."),
    "emotion": ("嗯...", "我在的..."),
    "command": ("收到。",),
    "default": ("嗯...", "让我想想..."),
}


class TTSPreNarrator:
    """Fire-and-forget TTS speaker for turn-start filler phrases.

    Parameters
    ----------
    tts_callable:
        ``async fn(text: str, *, voice: str | None = None) -> None``. If
        ``None``, :meth:`speak` is a no-op.
    enabled:
        Master toggle wired to ``config.agent.tts_pre_narration``.
    phrases:
        Map from ``task_type`` to candidate phrase tuple. The ``default``
        key is used when ``task_type`` isn't in the map.
    rng:
        Optional ``random.Random`` — tests inject a seeded RNG.
    """

    def __init__(
        self,
        *,
        tts_callable: Optional[Callable[..., Any]] = None,
        enabled: bool = True,
        phrases: Optional[dict[str, Sequence[str]]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._tts = tts_callable
        self._enabled = bool(enabled)
        self._phrases = dict(phrases) if phrases else dict(_DEFAULT_PHRASES)
        self._rng = rng or random.Random()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    def pick_phrase(self, task_type: str) -> str:
        bucket = self._phrases.get(task_type) or self._phrases.get("default")
        if not bucket:
            return ""
        return self._rng.choice(list(bucket))

    def speak(self, task_type: str) -> Optional[asyncio.Task[Any]]:
        """Speak a filler phrase. Returns the background task (or None).

        Never raises; TTS failures go to the structured log.
        """
        if not self._enabled or self._tts is None:
            return None
        phrase = self.pick_phrase(task_type)
        if not phrase:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "tts_prenarration.no_running_loop", task_type=task_type
            )
            return None
        return loop.create_task(self._speak_safely(phrase, task_type))

    async def _speak_safely(self, phrase: str, task_type: str) -> None:
        try:
            await self._tts(phrase)
        except Exception as exc:
            logger.warning(
                "tts_prenarration.speak_failed",
                task_type=task_type,
                phrase=phrase,
                error=str(exc),
                error_type=type(exc).__name__,
            )
