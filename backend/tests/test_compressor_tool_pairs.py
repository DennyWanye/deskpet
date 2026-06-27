# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""2026-06-12 真机 400 回归: 压缩切割点切断 tool 配对 → 孤儿 tool /
悬空 tool_calls → 上游 400 Bad Request。守护 _partition 对齐 +
_sanitize_tool_pairs 兜底。"""
from __future__ import annotations

import pytest

from deskpet.agent.context_compressor import _partition, _sanitize_tool_pairs


def _assert_legal(messages):
    """断言序列符合 OpenAI 协议: tool 必须紧跟配对 assistant.tool_calls。"""
    open_ids = set()
    for m in messages:
        role = m.get("role")
        if role == "tool":
            assert m.get("tool_call_id") in open_ids, f"孤儿 tool: {m}"
        elif role == "assistant" and m.get("tool_calls"):
            open_ids = {tc["id"] for tc in m["tool_calls"]}
            continue
        else:
            open_ids = set()
    # 悬空 tool_calls: 每个 assistant.tool_calls 的 id 都要有紧随响应
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            want = {tc["id"] for tc in m["tool_calls"]}
            got = set()
            for f in messages[i + 1:]:
                if f.get("role") == "tool" and f.get("tool_call_id") in want:
                    got.add(f.get("tool_call_id"))
                else:
                    break
            assert not (want - got), f"悬空 tool_calls: {want - got}"


def _tool_pair(i: int):
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "web_fetch", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": f"result {i}" * 50},
    ]


def _convo(n_pairs: int):
    msgs = [{"role": "system", "content": "persona"},
            {"role": "user", "content": "帮我调研"}]
    for i in range(n_pairs):
        msgs += _tool_pair(i)
    msgs.append({"role": "assistant", "content": "好的,继续"})
    msgs.append({"role": "user", "content": "继续"})
    return msgs


def test_partition_tail_never_starts_with_orphan_tool():
    """tail 切割点落在 tool 上 → 对齐后 tail 不以孤儿 tool 开头。"""
    msgs = _convo(6)
    for first_n in (1, 2, 3):
        for last_n in (1, 2, 3, 4, 5):
            _, head, middle, tail = _partition(msgs, first_n, last_n)
            if tail:
                assert tail[0].get("role") != "tool", (
                    f"first_n={first_n} last_n={last_n} tail 以孤儿 tool 开头"
                )
            if head:
                last = head[-1]
                assert not (
                    last.get("role") == "assistant" and last.get("tool_calls")
                ), f"first_n={first_n} last_n={last_n} head 以悬空 tool_calls 结尾"


def test_partition_no_middle_when_alignment_eats_it():
    """对齐后 middle 空 → 返回不压缩形态(而不是炸)。"""
    msgs = _convo(1)  # 很短
    _, head, middle, tail = _partition(msgs, 2, 2)
    assert middle == [] or middle  # 不抛即可;合法性下面统一断言
    _assert_legal(head + middle + tail)


def test_sanitize_drops_orphan_tool():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "[前文摘要] ..."},
        {"role": "tool", "tool_call_id": "cX", "content": "orphan"},
        {"role": "user", "content": "继续"},
    ]
    out = _sanitize_tool_pairs(msgs)
    assert all(m.get("role") != "tool" for m in out)
    _assert_legal(out)


def test_sanitize_strips_dangling_tool_calls():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "assistant", "content": "[前文摘要] ..."},  # 响应被压缩掉了
    ]
    out = _sanitize_tool_pairs(msgs)
    _assert_legal(out)
    # 悬空 tool_calls 被剥,内容兜底非空
    a = out[1]
    assert "tool_calls" not in a
    assert str(a.get("content") or "").strip()


def test_sanitize_keeps_legal_pairs_intact():
    msgs = [{"role": "user", "content": "q"}] + _tool_pair(0) + [
        {"role": "assistant", "content": "答案"}]
    out = _sanitize_tool_pairs(msgs)
    assert out == msgs  # 合法序列原样保留
    _assert_legal(out)


@pytest.mark.asyncio
async def test_compress_end_to_end_produces_legal_sequence():
    """端到端: 模拟 LLM 总结,压缩 web_fetch 重会话 → 输出序列协议合法。"""
    from deskpet.agent.context_compressor import ContextCompressor

    class _FakeLLM:
        async def chat_with_fallback(self, messages, **kw):
            class R:  # noqa: N801
                content = "压缩摘要: 调研了 6 个网页,要点略。"
            return R()

    comp = ContextCompressor(llm_registry=_FakeLLM(), first_n=2, last_n=3)
    msgs = _convo(8)
    result = await comp.compress(msgs)
    assert result.compressed is True
    _assert_legal(result.messages)
