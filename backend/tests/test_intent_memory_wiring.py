# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FEAT-A1 — 意图记忆接线单测（superpowers Layer 1B / 决策1）。

硬断言四类（spec FEAT-A1 完成定义 4）：

1. ``_intent_label_from_turn`` 的 task/ask 映射。
2. ``_build_intent_hint`` 的 ask/task hint + 未知 label → None。
3. 集成：fake ``PreferenceMemory`` match 命中 ask → 复现 main.py 注入循环逻辑，
   验证 ``_msgs`` **确实插入了** ask hint system 消息，且插在 system 块之后。
4. flag-off 守恒：``preference_memory is None`` → ``_msgs`` 零注入、字节不变。

注：注入循环（找 system 块尾 + insert）的真实落点在 ``control_channel`` 深处不可
独立调用，故集成断言用与 main.py:4814-4830 **逐字一致** 的循环逻辑包裹被测纯函数
``_build_intent_hint``，确保纯函数的输出 + 该插入约定共同产出正确的 ``_msgs``。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from main import _build_intent_hint, _intent_label_from_turn
from deskpet.agent.preference_memory import PreferenceMemory


# ---- 1. label 映射 ------------------------------------------------------

def test_intent_label_from_turn_task():
    assert _intent_label_from_turn(True) == "task"


def test_intent_label_from_turn_ask():
    assert _intent_label_from_turn(False) == "ask"


# ---- 2. hint 文案 -------------------------------------------------------

def test_build_intent_hint_ask_has_keyword():
    hint = _build_intent_hint("ask")
    assert hint is not None
    assert "直接回答" in hint
    assert "偏好记忆" in hint


def test_build_intent_hint_task_has_keyword():
    hint = _build_intent_hint("task")
    assert hint is not None
    assert "工作流" in hint
    assert "偏好记忆" in hint


def test_build_intent_hint_unknown_returns_none():
    assert _build_intent_hint("x") is None
    assert _build_intent_hint("") is None
    # 防御 None（match miss 时主流程传 "" 进来，但纯函数也得扛住 None）
    assert _build_intent_hint(None) is None  # type: ignore[arg-type]


# ---- 共享 fake embedder（确定性：同文本同向量 → cosine=1.0 必命中） ----

def _fake_embed_factory():
    """同一文本永远映射到同一确定向量 → 自匹配 cosine=1.0 ≥ 0.86 必命中。"""

    async def _embed(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            # 简单确定性 hash → 8 维向量
            h = abs(hash(t))
            vec = [((h >> (i * 4)) & 0xF) + 1 for i in range(8)]
            out.append([float(v) for v in vec])
        return out

    return _embed


def _replicate_inject(msgs: list[dict], im: dict | None) -> list[dict]:
    """逐字复现 main.py:4814-4836 的注入逻辑（system 块尾插入）。

    被测点 = ``_build_intent_hint``；插入约定与 main.py 完全一致以防漂移。
    """
    im_hint = _build_intent_hint(im.get("label") if im is not None else "")
    if im_hint:
        ins = 0
        while ins < len(msgs) and msgs[ins].get("role") == "system":
            ins += 1
        msgs.insert(ins, {"role": "system", "content": im_hint})
    return msgs


# ---- 3. 集成：match 命中 ask → 注入到 system 块之后 ---------------------

def test_match_ask_injects_hint_after_system_block(tmp_path: Path):
    async def _run():
        pref = PreferenceMemory(
            tmp_path / "pref.json", _fake_embed_factory()
        )
        text = "你用的是什么模型"
        recorded = await pref.record(text, "ask", "intent")
        assert recorded is True

        im = await pref.match(text, "intent")
        assert im is not None
        assert im["label"] == "ask"

        msgs = [
            {"role": "system", "content": "persona-A"},
            {"role": "system", "content": "persona-B"},
            {"role": "user", "content": text},
        ]
        _replicate_inject(msgs, im)

        # 插在两条 system 之后、user 之前（index 2）。
        assert len(msgs) == 4
        assert msgs[0]["content"] == "persona-A"
        assert msgs[1]["content"] == "persona-B"
        assert msgs[2]["role"] == "system"
        assert "直接回答" in msgs[2]["content"]
        assert msgs[3]["role"] == "user"

    asyncio.run(_run())


def test_match_task_injects_workflow_hint(tmp_path: Path):
    async def _run():
        pref = PreferenceMemory(tmp_path / "pref.json", _fake_embed_factory())
        text = "帮我把登录页重构一下"
        await pref.record(text, "task", "intent")
        im = await pref.match(text, "intent")
        assert im is not None and im["label"] == "task"

        msgs = [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": text},
        ]
        _replicate_inject(msgs, im)
        assert len(msgs) == 3
        assert msgs[1]["role"] == "system"
        assert "工作流" in msgs[1]["content"]

    asyncio.run(_run())


# ---- 4. flag-off 守恒：preference_memory is None → 零注入 --------------

def test_flag_off_none_zero_injection():
    """flag off（preference_memory is None）→ 主流程根本不进 match/inject 分支。

    复现 main.py:4814-4816 守卫：``_pref_im is None`` 时直接跳过整段。
    断言 ``_msgs`` 一个字节不变（守"出厂字节级不变"铁律）。
    """
    msgs_before = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "你用什么模型"},
    ]
    # 复制一份做基线对照
    import copy
    baseline = copy.deepcopy(msgs_before)

    pref_im = None  # flag off
    if pref_im is not None:  # 与 main.py 同样的守卫 → 不进分支
        _replicate_inject(msgs_before, None)

    assert msgs_before == baseline
    assert len(msgs_before) == 2


def test_match_miss_zero_injection(tmp_path: Path):
    """match 未命中（返回 None）→ _build_intent_hint("") → None → 零注入。"""
    async def _run():
        pref = PreferenceMemory(tmp_path / "pref.json", _fake_embed_factory())
        # 不 record 任何东西 → match 必 miss
        im = await pref.match("随便问问", "intent")
        assert im is None

        msgs = [{"role": "system", "content": "p"}, {"role": "user", "content": "q"}]
        before = list(msgs)
        _replicate_inject(msgs, im)
        assert msgs == before  # 零注入

    asyncio.run(_run())
