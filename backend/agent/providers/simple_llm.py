"""SimpleLLMAgent — V5 §12 要求的 '降级档' Agent 实现。

直接代理 LLMProvider.chat_stream,无工具调用。S2 起可选注入 MemoryStore
为当前 session 提供最近 N 轮对话历史。当 memory=None 时行为完全等价于 S0。

扩展点：
- S3 工具路由：见 ToolUsingAgent(AgentProvider)（独立类,不 merge 到这里）
- Phase 2：替换为 HermesAgentProvider
"""
from __future__ import annotations

from typing import AsyncIterator

from memory.base import MemoryStore
from providers.base import LLMProvider


class SimpleLLMAgent:
    """最小 Agent：代理 LLMProvider,可选注入会话记忆。"""

    def __init__(
        self,
        llm: LLMProvider,
        memory: MemoryStore | None = None,
        history_limit: int = 6,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._history_limit = history_limit

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str = "default",
    ) -> AsyncIterator[str]:
        # Build effective messages: system prompts + history + user/assistant msgs.
        # System messages MUST come first so the persona identity overrides any
        # stale self-identification that may linger in conversation history
        # (e.g. a local model once claiming to be "Gemma 4").
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system_msgs = [m for m in messages if m.get("role") != "system"]
        if self._memory is not None:
            history = await self._memory.get_recent(session_id, self._history_limit)
            history_msgs = [{"role": t.role, "content": t.content} for t in history]
            effective = system_msgs + history_msgs + non_system_msgs
        else:
            effective = list(messages)

        # Stream tokens, capture full response for persistence
        full_response = ""
        async for token in self._llm.chat_stream(effective):
            full_response += token
            yield token

        # Persist exchange after stream completes (only user + assistant turns)
        if self._memory is not None and messages:
            last_user = next(
                (m for m in reversed(messages) if m.get("role") == "user"),
                None,
            )
            if last_user and last_user.get("content"):
                await self._memory.append(session_id, "user", last_user["content"])
            if full_response.strip():
                await self._memory.append(session_id, "assistant", full_response)
