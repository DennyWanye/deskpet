# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1 单测 — IntentTriage（决策4：Step1+3 合并，一次 analyze() LLM 调用）。

覆盖（plans/2026-06-24-... §6 WI-1 + 05 L1）：
  - Y-light：chitchat 由预分析 LLM(deepseek) 裸判后短路（删早期规则短路修 BUG-B；现调 1 次 LLM）
  - 非闲聊只调 1 次 LLM（await_count==1，证明 Step1+3 合一）
  - 复杂问题一次调用同时出 intent + contradiction；简单 factual_qa 返回 contradiction=None
  - ambiguity≥阈值 → needs_clarification 澄清出口
  - prior_task_type → problem_type 映射
  - attack_order 解析
  - LLM 失败/超时/畸形 JSON → safe-fail 保守 card（contradiction=None）
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from deskpet.agent.intent_triage import (
    Contradiction, ContradictionMap, IntentCard, IntentTriage,
    contradiction_to_system_message, intent_to_system_message,
)


def _complex_payload() -> str:
    return json.dumps({
        "restated_intent": "修复登录报错",
        "problem_type": "debug",
        "ambiguity_score": 0.2,
        "clarifying_questions": [],
        "needs_investigation": True,
        "needs_decomposition": False,
        "contradiction": {
            "contradictions": [
                {"id": 1, "desc": "token 过期", "severity": 0.8, "aspect": "认证"},
                {"id": 2, "desc": "UI 文案误导", "severity": 0.3, "aspect": "前端"},
            ],
            "principal": 1,
            "principal_aspect": "认证链路",
            "attack_order": [1, 2],
            "rationale": "先解决 token 再修文案",
        },
    })


def _chitchat_payload() -> str:
    return json.dumps({
        "restated_intent": "打招呼闲聊",
        "problem_type": "chitchat",
        "ambiguity_score": 0.0,
        "clarifying_questions": [],
        "needs_investigation": False,
        "needs_decomposition": False,
        "contradiction": None,
    })


def test_chitchat_shortcircuit_via_llm() -> None:
    """Y-light：闲聊判断权交给预分析 LLM(deepseek)。LLM 判 chitchat + 低歧义 → short_circuit。

    （重构前是"靠 classifier task_type 的早期纯规则短路、0 次 LLM"；BUG-B：坏 classifier 会把
     真实非闲聊误判成 chat 致整条流水线短路。Y-light 删早期规则短路，由 deepseek 裸判，故现在调 1 次 LLM。）
    """
    mock_llm = AsyncMock(return_value=_chitchat_payload())
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("你好呀今天天气真好", prior_task_type="chat"))
    assert card.short_circuit is True
    assert card.problem_type == "chitchat"
    assert card.needs_investigation is False     # Y-light：chitchat 强制不取证
    assert card.contradiction is None
    assert mock_llm.await_count == 1             # 不再是 0 次：闲聊判断也走 LLM


def test_emotion_shortcircuit_via_llm() -> None:
    mock_llm = AsyncMock(return_value=_chitchat_payload())
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("我心情不好", prior_task_type="emotion"))
    assert card.short_circuit is True
    assert card.needs_investigation is False
    assert mock_llm.await_count == 1


def test_chitchat_no_early_shortcircuit_when_classifier_wrong() -> None:
    """★ BUG-B 回归：classifier 误判 chat，但 deepseek 判出真实类型(debug) → 不短路、进流水线。"""
    mock_llm = AsyncMock(return_value=_complex_payload())  # LLM 裸判为 debug + 矛盾段
    triage = IntentTriage(mock_llm)
    # prior_task_type="chat" 模拟坏 classifier；Y-light 下不再据此早期短路
    card = asyncio.run(triage.analyze("我的登录功能报错了帮我看看", prior_task_type="chat"))
    assert mock_llm.await_count == 1
    assert card.problem_type == "debug"
    assert card.short_circuit is False
    assert card.contradiction is not None


def test_complex_single_llm_call_with_contradiction() -> None:
    """非闲聊：只调 1 次 LLM，同时返回 intent + contradiction（决策4 合一证明）。"""
    mock_llm = AsyncMock(return_value=_complex_payload())
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("我的登录功能报错了帮我看看", prior_task_type="code"))
    assert mock_llm.await_count == 1                 # ★ 只调一次
    assert card.problem_type == "debug"
    assert card.contradiction is not None            # 矛盾段在同一次调用产出
    assert card.contradiction.principal == 1
    assert card.contradiction.attack_order == [1, 2]
    assert len(card.contradiction.contradictions) == 2


def test_simple_factual_returns_null_contradiction() -> None:
    """简单 factual_qa：contradiction=None（即便 LLM 误填也不解析，因 problem_type 不触发）。"""
    payload = json.dumps({
        "restated_intent": "查个事实",
        "problem_type": "factual_qa",
        "ambiguity_score": 0.1,
        "clarifying_questions": [],
        "needs_investigation": True,
        "needs_decomposition": False,
        "contradiction": None,
    })
    mock_llm = AsyncMock(return_value=payload)
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("中国首都是哪", prior_task_type="recall"))
    assert mock_llm.await_count == 1
    assert card.problem_type == "factual_qa"
    assert card.contradiction is None


def test_ambiguity_triggers_clarification() -> None:
    payload = json.dumps({
        "restated_intent": "?",
        "problem_type": "ambiguous",
        "ambiguity_score": 0.9,
        "clarifying_questions": ["你指的是 A 还是 B？"],
        "needs_investigation": False,
        "needs_decomposition": False,
        "contradiction": None,
    })
    mock_llm = AsyncMock(return_value=payload)
    triage = IntentTriage(mock_llm, clarify_threshold=0.7)
    card = asyncio.run(triage.analyze("那个东西", prior_task_type=None))
    assert card.needs_clarification is True
    assert card.clarifying_questions == ["你指的是 A 还是 B？"]
    assert card.short_circuit is False


def test_tasktype_to_problem_mapping() -> None:
    triage = IntentTriage(None)  # llm_call=None → safe-fail 走派生
    card = asyncio.run(triage.analyze("做个 PPT", prior_task_type="task"))
    assert card.problem_type == "creation"
    assert card.needs_decomposition is True


def test_safe_fail_on_llm_exception() -> None:
    async def _boom(_prompt: str) -> str:
        raise RuntimeError("relay 500")

    triage = IntentTriage(_boom)
    card = asyncio.run(triage.analyze("帮我调研下行业", prior_task_type="web_search"))
    assert card.problem_type == "research"        # safe-fail 用派生类型
    assert card.contradiction is None             # 不填矛盾段
    assert card.needs_clarification is False


def test_safe_fail_on_malformed_json() -> None:
    mock_llm = AsyncMock(return_value="这不是 JSON，是一段废话")
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("修 bug", prior_task_type="code"))
    assert card.problem_type == "debug"           # fallback 到派生
    assert card.contradiction is None


def test_safe_fail_malformed_json_with_chat_classifier_does_not_shortcircuit() -> None:
    """★ 真机 2026-06-25 回归：deepseek 畸形 JSON + 坏 classifier 给 prior_task_type='chat'
    → 旧实现 safe-fail 派生 chitchat 再派生 short_circuit=True，把真实问题误短路成闲聊（BUG-B 经
    safe-fail 路径复活）。修复后 safe-fail **绝不短路**（降级裸 ReAct，进流水线由主 loop 兜）。"""
    mock_llm = AsyncMock(return_value='{"restated_intent": "截断了的畸形 JSON')  # 不可解析
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("我的导出功能报错了帮我查查", prior_task_type="chat"))
    assert mock_llm.await_count == 1
    assert card.short_circuit is False            # ★ 关键：safe-fail 绝不短路
    assert card.contradiction is None


def test_pua_char_json_still_parses_correct_type() -> None:
    """真机 2026-06-25：deepseek 偶发把私用区字符(U+E160)塞进 JSON 字符串值 → 原实现 parse_failed
    → safe-fail。净化私用区/控制字符后正确 problem_type=debug 存活，不再误降级。"""
    payload = json.dumps({
        "restated_intent": "调查 export_report.py 的 KeyError",
        "problem_type": "debug",
        "ambiguity_score": 0.2,
        "clarifying_questions": [],
        "needs_investigation": True,
        "needs_decomposition": False,
        "contradiction": None,
    })
    # 注入一个私用区字符(U+E160)模拟 deepseek 真机输出
    dirty = payload.replace("KeyError", "KeyError")  # 注入控制字符 U+001F，json.loads strict 会拒
    mock_llm = AsyncMock(return_value=dirty)
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("export_report.py 报 KeyError", prior_task_type="chat"))
    assert card.problem_type == "debug"           # 净化后正确判断存活
    assert card.short_circuit is False


def test_contradiction_nonnumeric_fields_do_not_crash() -> None:
    """★ 真机 2026-06-25：非流式裸补全(无 strict schema)时 deepseek 可能把 severity/id/principal
    填成中文/乱码 → 旧实现裸 float()/int() 抛 ValueError 让整个 run_pre_loop 崩掉跳过流水线。
    防御性转换后不崩，落默认值。"""
    payload = json.dumps({
        "restated_intent": "复合问题",
        "problem_type": "multi_task",
        "ambiguity_score": 0.2,
        "clarifying_questions": [],
        "needs_investigation": True,
        "needs_decomposition": True,
        "contradiction": {
            "contradictions": [{"id": "一", "desc": "慢", "severity": "高", "aspect": "性能"}],
            "principal": "乱码", "principal_aspect": "X",
            "attack_order": ["1", "二", 3], "rationale": "r",
        },
    })
    mock_llm = AsyncMock(return_value=payload)
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("网站又慢又崩", prior_task_type="plan"))
    assert card.problem_type == "multi_task"
    assert card.contradiction is not None           # 不崩，矛盾段照常解析
    assert card.contradiction.contradictions[0].severity == 0.0   # 乱码→默认


def test_thinking_model_cot_prefix_stripped() -> None:
    """真机 2026-06-25：deepseek-v4-pro 非流式裸补全把 CoT 包在 <think>…</think> 里再跟 JSON，
    残留 think 文本(含花括号)会让贪婪 bare regex 抓花 → parse_failed。提取前剥 <think> 块。"""
    raw = (
        "<think>我需要按要求输出 JSON，字段有 problem_type {注意这里有花括号干扰}，"
        "用户是要写代码，应该是 creation</think>\n\n"
        '{"restated_intent": "写质数函数", "problem_type": "creation", '
        '"ambiguity_score": 0.1, "clarifying_questions": [], '
        '"needs_investigation": false, "needs_decomposition": true, "contradiction": null}'
    )
    mock_llm = AsyncMock(return_value=raw)
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("帮我写个判断质数的函数", prior_task_type="task"))
    assert card.problem_type == "creation"   # 剥 think 后正确解析，不再 safe-fail
    assert card.short_circuit is False


def test_fenced_json_extraction() -> None:
    """三级容错：fenced ```json 围栏也能解析。"""
    fenced = "思考中...\n```json\n" + _complex_payload() + "\n```\n收工"
    mock_llm = AsyncMock(return_value=fenced)
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("登录报错", prior_task_type="code"))
    assert card.contradiction is not None
    assert card.contradiction.principal == 1


# ───────────────────────────────────────────────────────────────────────────
# WI-2 / WI-4：闲聊快路径 allowlist（整句锚定，命中 → 0 次 LLM 短路）
# ───────────────────────────────────────────────────────────────────────────
import pytest
from structlog.testing import capture_logs


_ALLOWLIST_SHORTCIRCUIT = [
    "你好", "谢谢", "晚安", "😄", "。。。",
    "你好呀~", "晚安啊", "在吗在吗", "早上好", "hi 你好", "嗯嗯", "哈哈",
]
_ALLOWLIST_NO_SHORTCIRCUIT = [
    "你好，帮我看下这段为什么报错", "崩了", "报错", "卡死",
    "光合作用为什么需要光", "在吗？我代码崩了", "谢谢，那这个报错怎么办",
    "hi 帮我 debug", "在吗？", "ok 那你帮我改一下", "早上代码崩了",
]


@pytest.mark.parametrize("msg", _ALLOWLIST_SHORTCIRCUIT)
def test_allowlist_shortcircuits_without_llm(msg: str) -> None:
    """★ 命中 allowlist → 短路 + problem_type=chitchat + needs_investigation=False + 0 次 LLM。"""
    mock_llm = AsyncMock(return_value=_complex_payload())  # 若被调会判成 debug，借此证明没调
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze(msg, prior_task_type="chat"))
    assert mock_llm.await_count == 0, f"allowlist 命中却调了 LLM: {msg!r}"
    assert card.short_circuit is True
    assert card.problem_type == "chitchat"
    assert card.needs_investigation is False
    assert card.needs_clarification is False
    assert card.contradiction is None


@pytest.mark.parametrize("msg", _ALLOWLIST_NO_SHORTCIRCUIT)
def test_allowlist_miss_goes_to_llm(msg: str) -> None:
    """不命中 allowlist（含祈使/故障/问号）→ 不早期短路，进 LLM 路径（await_count==1）。"""
    mock_llm = AsyncMock(return_value=_complex_payload())
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze(msg, prior_task_type="chat"))
    assert mock_llm.await_count == 1, f"非寒暄却被 allowlist 误短路: {msg!r}"
    # LLM 裸判为 debug（_complex_payload），证明走到了 LLM 路径而非 allowlist 短路
    assert card.problem_type == "debug"
    assert card.short_circuit is False


def test_allowlist_hit_emits_structured_log() -> None:
    """BUGB-3 硬证据：命中打 intent_triage.allowlist_hit，且无 intent_triage.done/llm_failed。"""
    mock_llm = AsyncMock(return_value=_chitchat_payload())
    triage = IntentTriage(mock_llm)
    with capture_logs() as logs:
        card = asyncio.run(triage.analyze("你好呀", prior_task_type="chat"))
    events = [e.get("event") for e in logs]
    assert "intent_triage.allowlist_hit" in events
    assert "intent_triage.done" not in events       # 证 0 次 LLM 完成
    assert "intent_triage.llm_failed" not in events
    assert card.short_circuit is True
    assert mock_llm.await_count == 0


def test_allowlist_hit_even_when_llm_call_none() -> None:
    """llm_call=None（pipeline 降级）时寒暄仍走 allowlist 短路，不落 safe-fail。"""
    triage = IntentTriage(None)
    card = asyncio.run(triage.analyze("晚安", prior_task_type="chat"))
    assert card.short_circuit is True
    assert card.problem_type == "chitchat"


# ───────────────────────────────────────────────────────────────────────────
# WI-2b：safe-fail 绝不产出 chitchat（修取证门漏洞）
# ───────────────────────────────────────────────────────────────────────────
def test_safe_fail_chat_classifier_does_not_derive_chitchat() -> None:
    """★ 真问题 + 坏 classifier(prior='chat') + LLM 异常 → safe-fail 兜底 factual_qa（非 chitchat），
    且 needs_investigation=True（不跳过取证），short_circuit=False。"""
    async def _boom(_prompt: str) -> str:
        raise RuntimeError("relay 502")

    triage = IntentTriage(_boom)
    # 非寒暄（含故障词"报错"，不会被 allowlist 短路），prior='chat' → derived chitchat
    card = asyncio.run(triage.analyze("我的导出功能报错了", prior_task_type="chat"))
    assert card.problem_type != "chitchat"
    assert card.problem_type == "factual_qa"      # WI-2b 兜底
    assert card.needs_investigation is True        # 取证门不被跳过
    assert card.short_circuit is False


def test_safe_fail_emotion_classifier_does_not_derive_chitchat() -> None:
    """prior='emotion' 同样映射 chitchat → safe-fail 兜底 factual_qa。"""
    async def _boom(_prompt: str) -> str:
        raise RuntimeError("timeout")

    triage = IntentTriage(_boom)
    card = asyncio.run(triage.analyze("我的程序为什么会内存泄漏", prior_task_type="emotion"))
    assert card.problem_type == "factual_qa"


# ───────────────────────────────────────────────────────────────────────────
# WI-3（退役校验）：仓内无 bypass flag 读取点
# ───────────────────────────────────────────────────────────────────────────
def test_no_bypass_flag_read_points_in_backend() -> None:
    """plan WI-3：DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT / _CLARIFICATION 已退役，
    backend 源码（非测试）应 0 读取点。防回归引入新短路旁路。"""
    import os
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    needles = (
        "DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT",
        "DESKPET_DISABLE_CLARIFICATION",
    )
    offenders = []
    for root, dirs, files in os.walk(backend):
        # 跳过虚拟环境 / 缓存 / 测试自身
        parts = set(Path(root).parts)
        if any(p.startswith(".") and p not in (".",) for p in Path(root).relative_to(backend).parts):
            continue
        if "tests" in Path(root).relative_to(backend).parts:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = Path(root) / fn
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for needle in needles:
                if needle in text:
                    offenders.append(str(fp))
    assert not offenders, f"发现已退役 bypass flag 读取点: {offenders}"


def test_system_message_helpers() -> None:
    card = IntentCard(restated_intent="修复登录", problem_type="debug")
    msg = intent_to_system_message(card)
    assert "<意图>" in msg and "修复登录" in msg
    cmap = ContradictionMap(
        contradictions=[Contradiction(id=1, desc="token 过期", aspect="认证")],
        principal=1, principal_aspect="认证链路",
    )
    cmsg = contradiction_to_system_message(cmap)
    assert "<主要矛盾>" in cmsg and "token 过期" in cmsg and "认证链路" in cmsg
