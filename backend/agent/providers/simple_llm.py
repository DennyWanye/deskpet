"""SimpleLLMAgent — V5 §12 要求的 '降级档' Agent 实现。

直接代理 LLMProvider.chat_stream，无工具调用、无记忆检索。
作为 deskpet 默认 Agent 实现，也是 Hermes 不可用时的 fallback。

未来扩展点（留给后续 slice）：
- S2 记忆注入：chat_stream 前按 session_id 检索记忆，拼进 messages
- S3 工具路由：chat_stream 输出解析工具调用，经 ToolRouter 执行
- Phase 2：替换为 HermesAgentProvider
"""
from __future__ import annotations

from typing import AsyncIterator

from providers.base import LLMProvider


class SimpleLLMAgent:
    """最小 Agent：纯代理 LLMProvider。"""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str = "default",  # 当前未使用；S2 接记忆时激活
    ) -> AsyncIterator[str]:
        async for token in self._llm.chat_stream(messages):
            yield token
