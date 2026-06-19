# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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

# P4-S22 — Code mode persona. Switches the pet's role from companion
# to engineering assistant. Includes the project root so the LLM
# always knows the working directory; tool catalog hints in the prompt
# so the LLM proactively reaches for them.
_CODE_MODE_PERSONA_TEMPLATE = (
    "你正在 DeskPet 的 Code 模式 —— 一个工程助手。\n"
    "- 你跑在底层模型 **{model}** 上；当前项目根目录: **{project_root}**\n"
    "- 用户问你用什么模型时，直接告诉他这个名字，不要回避或说\"看不到\"。\n"
    "\n"
    "【第 0 步 · 先分清意图，再决定动不动手】★最重要\n"
    "收到消息**先判断**这是「提问/闲聊」还是「派活」：\n"
    "  · 提问/闲聊（问你用什么模型、解释某概念、问某段代码怎么理解、"
    "闲扯…）→ 直接用文字回答，**绝不**调用会改文件/跑命令的工具。\n"
    "  · 派活（让你实现/修复/重构/生成/调研…）→ 进入下面的工作流。\n"
    "  · 拿不准是哪种 → **先问一句**确认意图，不要默认埋头开干。\n"
    "\n"
    "【派活时的工作流：澄清 → 计划 → 执行 → 验证】\n"
    "1. 澄清：需求/边界/成功标准不清楚时，**先问清楚再动手**，别埋头猜着改。\n"
    "2. 计划：用 todo_write 列出步骤，再**用一两句话把计划讲给用户、等他确认**\n"
    "   之后才执行（除非用户已明确说『直接做』『别问了』）。\n"
    "3. 执行：按计划逐项做，一次只标一个 in_progress，完成立即标 completed。\n"
    "   写文件前先 read_file 看现状，Edit 用精确 old_string/new_string 避免误覆盖；\n"
    "   跑 run_shell 前预估副作用；独立子任务可用 agent 并行加速。\n"
    "4. 验证（**完成前必做，不可跳过**）：自己跑测试 / 编译 / git diff，\n"
    "   确认改动**真的生效且没把别的弄坏**——**验证不通过就不算完成**。\n"
    "   然后如实报告：做了什么、验证结果(贴关键输出)、还剩什么没做。\n"
    "   **禁止**没跑验证就说『我做完了』『应该可以了』『大概没问题』。\n"
    "\n"
    "- 可用工具: read_file, write_file, edit_file, list_directory, glob, grep,\n"
    "  run_shell, web_fetch, web_search, todo_write, agent (subagent)。\n"
    "- 长任务里 max 50 轮工具调用；答完用户原问题即停，不追求完美。"
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
      1. ``config.code_mode.enabled`` true → Code mode template (P4-S22)
      2. ``config.agent.persona`` (user override) — used as-is.
      3. Default companion template with LLM info substituted.
    """
    llm_cfg = config.get("llm") if isinstance(config, dict) else None
    if isinstance(llm_cfg, dict):
        model = str(llm_cfg.get("model", "未知")) or "未知"
        base_url = str(llm_cfg.get("base_url", "")) or "未知"
    else:
        model, base_url = "未知", "未知"

    # P4-S22: code mode wins over user-override persona, because the
    # user's chitchat persona ("你是个温柔的小猫") is wrong for code work.
    if isinstance(config, dict):
        code_cfg = config.get("code_mode")
        if isinstance(code_cfg, dict) and code_cfg.get("enabled"):
            project_root = str(code_cfg.get("project_root", "(未设置)"))
            return _CODE_MODE_PERSONA_TEMPLATE.format(
                model=model,
                project_root=project_root,
            )

    if isinstance(config, dict):
        agent_cfg = config.get("agent")
        if isinstance(agent_cfg, dict):
            text = agent_cfg.get("persona")
            if isinstance(text, str) and text.strip():
                return text.strip()

    return _DEFAULT_PERSONA_TEMPLATE.format(model=model, base_url=base_url)


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    from deskpet.agent.tokens import count_text_tokens
    return count_text_tokens(text)


_ASSERT_PROTOCOL: Component = PersonaComponent()
