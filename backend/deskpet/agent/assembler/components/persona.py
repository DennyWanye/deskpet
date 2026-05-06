"""Persona component (P4-S7 task 12.5, P4-S20-LLM-Unified updated).

Emits the pet's persona / system-prompt header. Content is pulled from
``config.agent.persona`` (or a default if unset) and is **frozen** —
it only changes when the user edits config, so prompt cache survives
across turns.

P4-S20 update: 把当前 LLM 的 model 名注入到 persona 文本里。这样用户问
"你用的是什么模型" 时，LLM 不会再含糊其辞 — 它知道自己跑在哪个模型上，
能直接回答 "我现在跑在 gemma4:e4b 上面"。
"""
from __future__ import annotations

from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


_DEFAULT_PERSONA_TEMPLATE = (
    "你是 DeskPet，一只陪伴用户工作的桌面宠物 AI。\n"
    "- 语气自然、简短，偶尔撒娇；\n"
    "- 熟悉用户偏好（见 USER.md / MEMORY.md）；\n"
    "- 工具调用失败时坦率说明，不编造结果；\n"
    "- 当前你跑在底层模型 **{model}** 上（endpoint: {base_url}）；\n"
    "- 用户问你用的是什么模型时，直接告诉他这个名字，不要回避或说\"看不到\"。"
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
    """Compose the persona text.

    Priority:
      1. ``config.agent.persona`` (user override) — used as-is.
      2. Default template with current LLM model + base_url substituted
         from ``config.llm.model`` / ``config.llm.base_url``.
    """
    if isinstance(config, dict):
        agent_cfg = config.get("agent")
        if isinstance(agent_cfg, dict):
            text = agent_cfg.get("persona")
            if isinstance(text, str) and text.strip():
                return text.strip()

    llm_cfg = config.get("llm") if isinstance(config, dict) else None
    if isinstance(llm_cfg, dict):
        model = str(llm_cfg.get("model", "未知")) or "未知"
        base_url = str(llm_cfg.get("base_url", "")) or "未知"
    else:
        model, base_url = "未知", "未知"

    return _DEFAULT_PERSONA_TEMPLATE.format(model=model, base_url=base_url)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


_ASSERT_PROTOCOL: Component = PersonaComponent()
