"""Agent 层 Provider 抽象协议。

V5 §2.3 ServiceContext 要求 agent_engine 与 llm_engine 分层；
V5 §5 Pipeline 中 "Agent Chat" 是独立 stage，输入文本+上下文、输出 token 流；
V5 §12 风险矩阵要求 "Agent 抽象层 + SimpleLLMProvider 降级" 作为
Hermes 不稳定的规避措施。

本文件定义 AgentProvider Protocol — 所有 Agent 实现（SimpleLLMAgent、
未来的 HermesAgent、ToolUsingAgent 等）通过此接口接入 ServiceContext。
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class AgentProvider(Protocol):
    """Agent 层抽象：管理会话级对话循环。

    当前 SimpleLLMAgent 只转发 LLMProvider；未来 Agent 实现可在此层
    注入记忆检索（S2）、工具路由（S3）、迭代推理（Phase 2 Hermes）。

    设计要点：
    - 返回 token 流，匹配 V5 §5 Pipeline 输出契约
    - session_id 从一开始就暴露，S2 接记忆时无需破坏性改签
    """

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str = "default",
    ) -> AsyncIterator[str]: ...
