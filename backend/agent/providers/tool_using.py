"""ToolUsingAgent — wraps any AgentProvider with text-protocol tool routing.

Text protocol (MVP): LLM emits `<tool>NAME</tool>` anywhere in its reply.
After the base agent's stream completes, ToolUsingAgent scans the
accumulated text, looks up the tool by name, invokes it, and appends the
result to the user-visible stream as `[tool:NAME] RESULT\\n`.

No recursive calling — one tool per turn, max. Result is inline, not
fed back to the LLM. Phase 2 can upgrade to ReAct.

Design choice: ToolUsingAgent does NOT own memory persistence — that's
the inner agent's job (SimpleLLMAgent). Tool-result text is purely
additive on the user-facing stream; it doesn't get written to memory
(we want memory to reflect the LLM's own output, not post-hoc
enrichment).

Confirmation policy (V5 §6): a ToolSpec can declare
``requires_confirmation=True`` for destructive actions. ToolUsingAgent
consults the injected ``confirm`` callback before invoking such tools;
if the callback returns False the tool is skipped and a refusal marker
is streamed instead. The default callback denies everything — this is
intentionally fail-closed so wiring up the frontend dialog is a
deliberate step.
"""
from __future__ import annotations

import re
from typing import AsyncIterator, Awaitable, Callable

from agent.providers.base import AgentProvider
from observability.metrics import stage_timer
from tools.registry import ToolRegistry

_TOOL_TAG = re.compile(r"<tool>\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*</tool>")


# Callback signature: (tool_name, session_id) -> bool. May be sync or async.
ConfirmCallback = Callable[[str, str], "bool | Awaitable[bool]"]


async def _deny_all(name: str, session_id: str) -> bool:  # noqa: ARG001
    """Default confirm handler — refuses any high-risk invocation.

    Fail-closed by design: frontends must explicitly wire a real dialog
    before tools flagged ``requires_confirmation`` can run.
    """
    return False


class ToolUsingAgent:
    """Wraps an AgentProvider. If inner agent's reply contains a <tool>
    tag, the referenced tool is invoked and its result is streamed out
    as a follow-up line."""

    def __init__(
        self,
        base: AgentProvider,
        registry: ToolRegistry,
        inject_system_prompt: bool = True,
        confirm: ConfirmCallback | None = None,
    ) -> None:
        self._base = base
        self._registry = registry
        self._inject = inject_system_prompt
        self._confirm: ConfirmCallback = confirm or _deny_all

    def _maybe_prepend_system(
        self, messages: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        if not self._inject:
            return messages
        hint = self._registry.prompt_hint()
        if not hint:
            return messages
        # Only inject if first message isn't already system
        if messages and messages[0].get("role") == "system":
            return messages
        return [{"role": "system", "content": hint}, *messages]

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str = "default",
    ) -> AsyncIterator[str]:
        effective = self._maybe_prepend_system(messages)
        first_pass = ""
        async for tok in self._base.chat_stream(effective, session_id=session_id):
            first_pass += tok
            yield tok

        # Extract tool invocation (first match wins; MVP = one tool/turn)
        match = _TOOL_TAG.search(first_pass)
        if not match:
            return

        name = match.group(1)
        tool = self._registry.get(name)
        if tool is None:
            yield f"\n[tool not found: {name}]\n"
            return

        # Confirmation gate for destructive tools. The callback can be sync or
        # async; we inspect the return for awaitability to keep both paths.
        if getattr(tool.spec, "requires_confirmation", False):
            result = self._confirm(name, session_id)
            if hasattr(result, "__await__"):
                approved = await result  # type: ignore[assignment]
            else:
                approved = bool(result)
            if not approved:
                yield f"\n[tool refused: {name} (user declined confirmation)]\n"
                return

        try:
            async with stage_timer("tool_invoke", tool_name=name, session_id=session_id):
                result = await tool.invoke()
        except Exception as exc:
            yield f"\n[tool error: {name}: {exc}]\n"
            return

        yield f"\n[tool:{name}] {result}\n"
