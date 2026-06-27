# SPDX-License-Identifier: BUSL-1.1
"""2026-06-06 真机抓的 relay 鲁棒性：中转 relay 经代理间歇掉流式连接（httpx
ReadError）→ agent turn 立即崩。chat_with_fallback_stream 加重试：流在产出任何
事件前掉链 → 干净重试整个流；已产出则不重试（避免前端 delta 重复）。"""
from __future__ import annotations

import pytest

from agent.tool_use_shim import OpenAICompatibleAgentLLM


class _ReadError(Exception):
    """模拟 httpx.ReadError（name 含 'ReadError' → 被判 transient）。"""
    __name__ = "ReadError"


# 让 type(exc).__name__ == "ReadError"
ReadError = type("ReadError", (Exception,), {})


class _FlakyProvider:
    """前 N 次 chat_stream_with_tools 在产出前抛 ReadError，之后成功。"""
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def chat_stream_with_tools(self, messages, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ReadError("connection dropped by proxy")
        yield {"type": "delta", "content": "hi"}
        yield {"type": "final", "content": "hi", "tool_calls": [],
               "stop_reason": "end_turn", "model": "m", "usage": {}}


class _MidStreamDropProvider:
    """已产出 delta 后掉链 → 不应重试（会重复）→ 抛。"""
    def __init__(self):
        self.calls = 0

    async def chat_stream_with_tools(self, messages, **kw):
        self.calls += 1
        yield {"type": "delta", "content": "partial"}
        raise ReadError("dropped mid-stream")


@pytest.mark.asyncio
async def test_stream_retries_on_connection_drop_before_output():
    """前 2 次掉链 → 重试 → 第 3 次成功产出 final。"""
    prov = _FlakyProvider(fail_times=2)
    shim = OpenAICompatibleAgentLLM(provider=prov)
    evs = [ev async for ev in shim.chat_with_fallback_stream([{"role": "user", "content": "x"}])]
    assert prov.calls == 3, f"应重试到第3次成功,实际 calls={prov.calls}"
    assert any(e.get("type") == "final" for e in evs)


@pytest.mark.asyncio
async def test_stream_no_retry_after_output_yielded():
    """已 yield delta 后掉链 → 不重试（避免重复）→ 抛 ReadError。"""
    prov = _MidStreamDropProvider()
    shim = OpenAICompatibleAgentLLM(provider=prov)
    with pytest.raises(ReadError):
        async for _ in shim.chat_with_fallback_stream([{"role": "user", "content": "x"}]):
            pass
    assert prov.calls == 1, "已产出后不应重试"


@pytest.mark.asyncio
async def test_stream_gives_up_after_max_retries():
    """持续掉链 → 重试用尽（3 次）→ 抛。"""
    prov = _FlakyProvider(fail_times=99)
    shim = OpenAICompatibleAgentLLM(provider=prov)
    with pytest.raises(ReadError):
        async for _ in shim.chat_with_fallback_stream([{"role": "user", "content": "x"}]):
            pass
    assert prov.calls == 3, f"应重试3次后放弃,实际 {prov.calls}"
