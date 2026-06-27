# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1B-1 token 计数口径收敛 — 强回归。

收敛前三套口径并存：
  * ``main._approx_tokens`` / context breakdown 用 ``len/3.5`` → 中文低估 ~3-4 倍
  * ``skill.py`` / ``metrics.py`` 用裸 ``len//4`` → 中文低估 ~4 倍
  * ``tokens.count_text_tokens`` 才是 CJK-aware（CJK×4 加权）正确口径

收敛后全部委托 ``count_text_tokens``。本组测试钉死：CJK 文本在各路径上
**不再低估**，且与统一入口一致；ASCII 路径仍合理。
"""
from __future__ import annotations

from deskpet.agent.tokens import count_text_tokens


# 一段纯中文：CJK ≈ 1 token/字。
_CJK = "钠离子电池技术规范与安全测试报告" * 20  # 320 CJK 字


def test_main_approx_tokens_cjk_aware():
    """main._approx_tokens 收敛后委托 count_text_tokens，对中文不再低估到 1/3.5。"""
    import main

    n = main._approx_tokens(_CJK)
    expect = count_text_tokens(_CJK)
    old_3_5 = int(len(_CJK) / 3.5)

    # 1) 与统一入口逐字节一致
    assert n == expect, f"main._approx_tokens={n} != count_text_tokens={expect}"
    # 2) 显著高于旧 /3.5 口径（不再低估爆窗）—— CJK 约 1token/字，旧口径约 0.29/字
    assert n > old_3_5 * 2.5, f"未脱离旧 /3.5 低估: new={n}, old={old_3_5}"
    # 3) 边界语义保持
    assert main._approx_tokens("") == 0
    assert main._approx_tokens(None) == 0


def test_skill_tokens_cjk_aware():
    """skill.py 的 token 计数收敛到 count_text_tokens（不再裸 len//4 低估中文）。"""
    from deskpet.agent.assembler.components import skill

    # skill 模块在算 token 数的点统一用 count_text_tokens
    assert skill.count_text_tokens is count_text_tokens
    n = skill.count_text_tokens(_CJK)
    old_div4 = len(_CJK) // 4
    assert n == count_text_tokens(_CJK)
    assert n > old_div4 * 2.5, f"skill 仍按 len//4 低估中文: new={n}, old={old_div4}"


def test_metrics_l3_tokens_cjk_aware():
    """metrics._rendered_tokens 收敛后对中文召回段不再低估到 1/4。"""
    from deskpet.memory.eval import metrics

    # 构造一批中文 L3 hits（dict 形态）
    hits = [{"text": "钠离子电池循环寿命测试" * 5, "source": "L3", "score": 0.9} for _ in range(8)]
    n = metrics._rendered_tokens(hits)

    # 复刻其内部渲染算出"统一口径"应得值
    lines = []
    for h in hits:
        text = h["text"]
        if len(text) > 240:
            text = text[:240] + "…"
        lines.append(f"- [{h['source']} {h['score']:.3f}] {text}")
    block = "## 相关记忆片段 (L3, RRF recall)\n\n" + "\n".join(lines)
    expect = max(1, count_text_tokens(block))
    old_div4 = max(1, len(block) // 4)

    assert n == expect, f"metrics={n} != 统一口径={expect}"
    assert n > old_div4 * 2.0, f"metrics 仍按 len//4 低估中文: new={n}, old={old_div4}"


def test_metrics_empty_recall_zero():
    """空召回仍 → 0 token（不渲染段头），边界语义不变。"""
    from deskpet.memory.eval import metrics

    assert metrics._rendered_tokens([]) == 0
