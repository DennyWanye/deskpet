"""Time component (P4-S7 task 12.5).

Emits current local time / date / timezone for the LLM. Placed in the
*dynamic* bucket because it changes every turn — but it's tiny (~30
tokens) so it doesn't hurt cache much in practice.

Module is named ``time_component.py`` (not ``time.py``) to avoid
colliding with the stdlib ``time`` module in local imports.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


class TimeComponent:
    """Emits current local time / date / DOW block."""

    name: str = "time"

    def __init__(self, *, clock: Optional[object] = None) -> None:
        # ``clock`` lets tests freeze the clock without monkeypatching
        # the datetime module. Expected interface: ``clock.now()``.
        self._clock = clock

    async def provide(self, ctx: ComponentContext) -> Slice:
        if self._clock is not None and hasattr(self._clock, "now"):
            now = self._clock.now()  # type: ignore[attr-defined]
        else:
            now = datetime.now().astimezone()

        weekday = _CN_WEEKDAYS[now.weekday()]
        tz_name = now.tzname() or "local"
        text = (
            f"## 当前时间\n"
            f"{now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"{weekday} ({tz_name})"
        )
        return Slice(
            component_name=self.name,
            text_content=text,
            tokens=max(1, len(text) // 4),
            priority=10,
            bucket="dynamic",
            meta={"tz": tz_name},
        )


_CN_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


_ASSERT_PROTOCOL: Component = TimeComponent()
