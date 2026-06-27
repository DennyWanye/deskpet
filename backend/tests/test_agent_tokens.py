# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""统一 token 计数(deskpet.agent.tokens)测试 — 优化 #1+#3。"""
from __future__ import annotations

from deskpet.agent.tokens import count_text_tokens, count_messages_tokens


def test_empty():
    assert count_text_tokens("") == 0
    assert count_text_tokens(None) == 0  # type: ignore[arg-type]
    assert count_messages_tokens([]) == 0


def test_cjk_not_underestimated():
    """CJK 文本不可像裸 len//4 那样低估到 ~1/4(否则压缩永不触发 → 爆窗,FP-2 事故)。"""
    cjk = "钠离子电池技术规范" * 20   # 180 CJK 字
    n = count_text_tokens(cjk)
    # CJK ≈ 1 token/字,应接近 180,绝不能掉到字符数的 1/4(45)
    assert n >= 150, f"CJK 180 字被估成 {n}, 低估爆窗风险"


def test_ascii_reasonable():
    """英文约 3.5-4 char/token,不应爆高也不应过低。"""
    ascii_txt = "the quick brown fox jumps over the lazy dog " * 10  # 440 chars
    n = count_text_tokens(ascii_txt)
    assert 90 <= n <= 160, f"ASCII 440 chars 估成 {n}"


def test_cross_module_consistency():
    """compressor / budget / token_budget 必须同口径(优化 #3 核心)。"""
    from deskpet.agent import context_compressor as cc
    from deskpet.agent.assembler import budget as bg
    from agent import token_budget as tb
    t = "钠离子电池 GB/T 44265-2024 electric storage station 技术规范"
    a = count_text_tokens(t)
    assert cc._approx_tokens(t) == a
    assert bg._count_tokens(t) == a
    msgs = [{"role": "user", "content": t}, {"role": "assistant", "content": "好的" * 50}]
    assert count_messages_tokens(msgs) == tb.estimate_tokens(msgs)


def test_messages_counts_tool_calls():
    """tool_calls 载荷也计入(防止工具密集会话低估)。"""
    msgs = [{
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "1", "function": {"name": "x"},
                        "args": '{"q":"' + "钠离子电池" * 30 + '"}'}],
    }]
    assert count_messages_tokens(msgs) > 30   # 工具参数里的中文被算进去
