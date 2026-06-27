# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase 1.3 — 4-breakpoint prompt cache + 前缀稳定纪律 (OpenSpec
2026-05-15-context-1m-rearch, design.md D4).

OpenAI-compat (the relay / deepseek-v4-pro) 走 prefix cache：命中条件是
history 前缀的字节流跨轮稳定。本测试覆盖三件事，全部在 provider 层
（`backend/providers/openai_compatible.py`）做：

1. 1.3.1 固定 messages 装配顺序契约：
   `_cache_breakpoint_order` 给出 [system, tools, memory, history,
   last-user-pre] 的逻辑断点序，可断言上游装配没把易变块插到前缀里。

2. 1.3.2 前缀稳定纪律（D4 抄 DeepSeek-TUI）：`_stabilize_prefix`
   保证 assistant tool-call-only 消息也带稳定 `content` placeholder，
   且 `reasoning_content` 的"有/无"不在同一逻辑轮之间抖动 —— 否则
   字节哈希被破，prefix cache 全 miss。

3. 1.3.4 cache 命中率日志：从回包 usage 的 `cached_tokens` /
   `prompt_tokens_details.cached_tokens` 防御式取值；字段缺失时
   记 `cached_tokens=unknown`，绝不崩。

1.3.3 的 Anthropic `cache_control` 部分：`backend/llm/anthropic_adapter.py`
已经实现（`_split_system_messages` / `_convert_tools` 给最后一个
system block + 最后一个 tool 打 `cache_control={"type":"ephemeral"}`，
并已读 cache_read/creation tokens）—— 无新增工作，详见 evidence。
本文件只覆盖 OpenAI-compat 这条线（the relay 是本项目唯一在跑的真 provider）。
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest
import structlog

from providers.openai_compatible import (
    OpenAICompatibleProvider,
    _cache_breakpoint_order,
    _extract_cached_tokens,
    _stabilize_prefix,
)


@pytest.fixture
def structlog_to_stdlib():
    """把 structlog 路由到 stdlib logging，让 caplog 抓得到记录。

    与 backend/main.py 生产配置一致。不做这步的话 structlog 默认
    ``PrintLoggerFactory`` 直接写 stdout，caplog 抓 0 条记录、断言会
    因错误的原因失败。测试结束恢复默认。
    （沿用 test_p5s2_sse_diagnostic.py 已确立的同款 fixture。）
    """
    prior = structlog.get_config()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.KeyValueRenderer(sort_keys=False),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    try:
        yield
    finally:
        structlog.configure(**prior)


# ──────────────────────────────────────────────────────────────────────
# SSE helpers (复用 test_openai_compatible.py 的 wire 格式)
# ──────────────────────────────────────────────────────────────────────


def _sse(frames: list[dict | str]) -> bytes:
    lines: list[str] = []
    for frame in frames:
        if isinstance(frame, str):
            lines.append(f"data: {frame}\n")
        else:
            lines.append(f"data: {json.dumps(frame)}\n")
        lines.append("\n")
    return "".join(lines).encode("utf-8")


def _delta(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }


# ──────────────────────────────────────────────────────────────────────
# 1.3.1 — 固定 messages 装配顺序契约
# ──────────────────────────────────────────────────────────────────────


def test_breakpoint_order_canonical_assembly():
    """system → tools-marker → memory → history → last-user-pre。

    `tools` 不是一条 message（它是 payload 顶层字段），所以装配顺序里
    它的"占位"由是否传 tools 决定；序里给出逻辑断点标签即可。
    """
    messages = [
        {"role": "system", "content": "persona 静态指令"},
        {"role": "system", "content": "<memory_block>repo map</memory_block>"},
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答"},
        {"role": "user", "content": "最近一轮问题"},
    ]
    order = _cache_breakpoint_order(messages, has_tools=True)
    assert order == [
        "system",          # [0] persona+静态  ← breakpoint 1
        "tools",           # [1] tools schema  ← breakpoint 2
        "memory",          # [2] memory/repo-map block ← breakpoint 3
        "history",         # [3..] 历史 + tool_results
        "last-user-pre",   # [last] 最近 user turn 之前 ← breakpoint 4
    ]


def test_breakpoint_order_without_tools_drops_tools_slot():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "hi"},
    ]
    order = _cache_breakpoint_order(messages, has_tools=False)
    assert "tools" not in order
    assert order[0] == "system"
    assert order[-1] == "last-user-pre"


def test_breakpoint_order_no_memory_block_collapses_memory_slot():
    """没有 <memory_block> 时不硬塞 memory 断点（避免 breakpoint 浪费）。"""
    messages = [
        {"role": "system", "content": "persona only"},
        {"role": "user", "content": "q"},
    ]
    order = _cache_breakpoint_order(messages, has_tools=False)
    assert "memory" not in order


# ──────────────────────────────────────────────────────────────────────
# 1.3.2 — 前缀稳定纪律 (D4)
# ──────────────────────────────────────────────────────────────────────


def test_stabilize_prefix_tool_call_only_assistant_gets_placeholder():
    """assistant tool-call-only（content 缺失/None）→ 稳定 "" placeholder。

    OpenAI 协议里 tool-call 轮的 assistant.content 经常是 None；如果
    上游某轮发 None 某轮发 "" 某轮丢字段，前缀字节就抖。统一成 ""。
    """
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u"},
        {  # content 完全缺失
            "role": "assistant",
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
    ]
    out = _stabilize_prefix(msgs)
    asst = out[2]
    assert asst["content"] == ""          # 注入稳定 placeholder
    assert "tool_calls" in asst           # 不破坏 tool_calls
    # 原 list 不被原地改（返回 copy）
    assert "content" not in msgs[2]


def test_stabilize_prefix_none_content_normalized_to_empty_string():
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "x", "type": "function",
                         "function": {"name": "f", "arguments": "{}"}}]},
    ]
    out = _stabilize_prefix(msgs)
    assert out[0]["content"] == ""


def test_stabilize_prefix_drops_empty_reasoning_content_consistently():
    """reasoning_content 为空/None 时一律删除该 key —— 不能某轮有空串
    某轮没字段，否则字节哈希在"有 key 但空"和"无 key"之间抖。"""
    msgs = [
        {"role": "assistant", "content": "a", "reasoning_content": ""},
        {"role": "assistant", "content": "b", "reasoning_content": None},
        {"role": "assistant", "content": "c", "reasoning_content": "真有推理"},
    ]
    out = _stabilize_prefix(msgs)
    assert "reasoning_content" not in out[0]
    assert "reasoning_content" not in out[1]
    assert out[2]["reasoning_content"] == "真有推理"   # 非空保留


def test_stabilize_prefix_idempotent_byte_stable():
    """同一逻辑历史跑两次 stabilize，序列化字节必须一致（cache 命中前提）。"""
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "q1"},
        {"role": "assistant",
         "tool_calls": [{"id": "c", "type": "function",
                         "function": {"name": "ls", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c", "content": "out"},
    ]
    a = json.dumps(_stabilize_prefix(msgs), ensure_ascii=False, sort_keys=True)
    b = json.dumps(_stabilize_prefix(msgs), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_stabilize_prefix_does_not_mutate_input():
    msgs = [{"role": "assistant",
             "tool_calls": [{"id": "1", "type": "function",
                             "function": {"name": "n", "arguments": "{}"}}]}]
    snapshot = json.dumps(msgs, sort_keys=True)
    _stabilize_prefix(msgs)
    assert json.dumps(msgs, sort_keys=True) == snapshot


def test_stabilize_prefix_user_and_system_untouched():
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ]
    out = _stabilize_prefix(msgs)
    assert out[0] == {"role": "system", "content": "S"}
    assert out[1] == {"role": "user", "content": "U"}


# ──────────────────────────────────────────────────────────────────────
# 1.3.4 — cached_tokens 防御式提取
# ──────────────────────────────────────────────────────────────────────


def test_extract_cached_tokens_from_prompt_tokens_details():
    """OpenAI / deepseek 标准位置：usage.prompt_tokens_details.cached_tokens"""
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 768},
    }
    assert _extract_cached_tokens(usage) == 768


def test_extract_cached_tokens_from_flat_field():
    """有的中转站把 cached_tokens 平铺在 usage 顶层。"""
    usage = {"prompt_tokens": 500, "cached_tokens": 256}
    assert _extract_cached_tokens(usage) == 256


def test_extract_cached_tokens_absent_returns_none():
    """字段完全没有 → None（调用方记 unknown，绝不崩）。"""
    assert _extract_cached_tokens({"prompt_tokens": 10}) is None
    assert _extract_cached_tokens({}) is None
    assert _extract_cached_tokens(None) is None


def test_extract_cached_tokens_malformed_does_not_crash():
    """脏数据（details 不是 dict / cached_tokens 是字符串）不能抛。"""
    assert _extract_cached_tokens({"prompt_tokens_details": "garbage"}) is None
    assert _extract_cached_tokens({"cached_tokens": "not-a-number"}) is None
    assert _extract_cached_tokens({"prompt_tokens_details": {"cached_tokens": None}}) is None


# ──────────────────────────────────────────────────────────────────────
# 集成：stream 路径回包带 cached_tokens → 必须落一条命中率日志
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_logs_cache_hit_rate_when_cached_tokens_present(
    caplog, structlog_to_stdlib
):
    def handler(request: httpx.Request) -> httpx.Response:
        frames = [
            _delta("hi"),
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 5,
                    "total_tokens": 1005,
                    "prompt_tokens_details": {"cached_tokens": 600},
                },
            },
            "[DONE]",
        ]
        return httpx.Response(
            200, content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1", api_key="k", model="deepseek-v4-pro",
    )
    provider._test_transport = httpx.MockTransport(handler)

    with caplog.at_level(logging.INFO):
        evs = [
            ev async for ev in provider.chat_stream_with_tools(
                [{"role": "user", "content": "q"}]
            )
        ]
    assert any(ev.get("type") == "final" for ev in evs)
    rec = next(
        (r for r in caplog.records
         if "prompt_cache_hit" in r.getMessage()
         or getattr(r, "event", "") == "p4s25_prompt_cache_hit"),
        None,
    )
    assert rec is not None, "回包带 cached_tokens 时必须落 prompt_cache_hit 日志"


@pytest.mark.asyncio
async def test_stream_logs_cache_unknown_when_field_absent(
    caplog, structlog_to_stdlib
):
    """provider 不回 cached_tokens（the relay 现状常见）→ 日志记 unknown，不崩。"""
    def handler(request: httpx.Request) -> httpx.Response:
        frames = [
            _delta("hi"),
            {"choices": [],
             "usage": {"prompt_tokens": 100, "completion_tokens": 2,
                       "total_tokens": 102}},
            "[DONE]",
        ]
        return httpx.Response(
            200, content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1", api_key="k", model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)
    with caplog.at_level(logging.INFO):
        evs = [
            ev async for ev in provider.chat_stream_with_tools(
                [{"role": "user", "content": "q"}]
            )
        ]
    assert any(ev.get("type") == "final" for ev in evs)
    rec = next(
        (r for r in caplog.records
         if "prompt_cache_hit" in r.getMessage()
         or getattr(r, "event", "") == "p4s25_prompt_cache_hit"),
        None,
    )
    assert rec is not None
    msg = rec.getMessage()
    # structlog 渲染：cached_tokens 字段值应为 unknown 标记
    assert "unknown" in msg or getattr(rec, "cached_tokens", None) == "unknown"


@pytest.mark.asyncio
async def test_stream_outgoing_messages_are_prefix_stabilized():
    """provider 发出去的 messages：tool-call-only assistant 必须带稳定
    content placeholder（验证 _stabilize_prefix 真的作用在出站 payload 上）。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=_sse([_delta("ok"), "[DONE]"]),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1", api_key="k", model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u"},
        {  # tool-call-only：故意不带 content
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "body"},
        {"role": "user", "content": "再问"},
    ]
    _ = [ev async for ev in provider.chat_stream_with_tools(msgs)]
    sent = captured["body"]["messages"]
    asst = next(m for m in sent if m.get("role") == "assistant")
    assert asst.get("content") == "", (
        "出站 payload 里 tool-call-only assistant 必须带稳定 '' placeholder"
    )
    # 原始入参不被原地改
    assert "content" not in msgs[2]
