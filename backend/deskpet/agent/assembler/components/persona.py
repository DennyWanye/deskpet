"""Persona component (P4-S7 task 12.5).

Emits the pet's persona / system-prompt header. Content is pulled from
``config.agent.persona`` (or a default if unset) and is **frozen** —
it only changes when the user edits config, so prompt cache survives
across turns.
"""
from __future__ import annotations

from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


_DEFAULT_PERSONA = (
    "你是 DeskPet，一只陪伴用户工作的桌面宠物 AI。\n"
    "- 语气自然、简短，偶尔撒娇；\n"
    "- 熟悉用户偏好（见 USER.md / MEMORY.md）；\n"
    "- 工具调用失败时坦率说明，不编造结果。"
)


class PersonaComponent:
    """Emits the pet's persona block (frozen, cache-friendly)."""

    name: str = "persona"

    async def provide(self, ctx: ComponentContext) -> Slice:
        persona_text = _resolve_persona(ctx.config)
        return Slice(
            component_name=self.name,
            text_content=persona_text,
            tokens=_approx_tokens(persona_text),
            priority=90,
            bucket="frozen",
            meta={"source": "config" if ctx.config.get("agent") else "default"},
        )


def _resolve_persona(config: dict[str, Any]) -> str:
    agent_cfg = config.get("agent") if isinstance(config, dict) else None
    if isinstance(agent_cfg, dict):
        text = agent_cfg.get("persona")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return _DEFAULT_PERSONA


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


_ASSERT_PROTOCOL: Component = PersonaComponent()
