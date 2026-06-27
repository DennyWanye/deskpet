# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1B-4 摘要质量回路 — flag OFF 字节级 BC；ON 时检测困惑措辞 + L1 回灌。

回路逻辑（main.py _run_chat 闭包）拆成纯 helper（_sql_user_is_confused /
_sql_latest_task_snapshot / _sql_build_reinject_msg），既供闭包复用也供本测断言。

- OFF（默认 summary_quality_loop=False）→ 闭包整段 short-circuit，不调 helper、
  不改 _msgs（字节级 BC）。本测以 helper 行为 + 模拟闭包门控验证两条路径。
- ON + 发生过压缩（L1 有任务态快照）+ 用户困惑措辞 → 注入任务态 system 消息。
"""
from __future__ import annotations

from main import (
    _sql_build_reinject_msg,
    _sql_latest_task_snapshot,
    _sql_user_is_confused,
)


# ─────────── 纯 helper 单测 ───────────
def test_confused_phrasing_detection():
    for hit in ["你还记得刚才说的吗", "之前说的那个", "你忘了我要什么", "我们在弄啥来着", "回到上一个任务"]:
        assert _sql_user_is_confused(hit) is True, hit
    for miss in ["帮我生成一份 PPT", "今天天气怎么样", ""]:
        assert _sql_user_is_confused(miss) is False, miss


def test_latest_snapshot_extraction():
    entries = [
        {"text": "用户喜欢简洁回复", "salience": 0.5},
        {"text": "[任务态快照/task-state] 目标: 做宁德时代年报", "salience": 0.6},
        {"text": "[任务态快照/task-state] 目标: 做小学教育 PPT", "salience": 0.6},
    ]
    latest = _sql_latest_task_snapshot(entries)
    assert latest is not None
    assert "小学教育 PPT" in latest  # 取最近一条


def test_latest_snapshot_none_when_no_compaction():
    """L1 里没有任务态快照（= 本 session 没发生过压缩）→ None。"""
    entries = [{"text": "用户喜欢简洁回复", "salience": 0.5}]
    assert _sql_latest_task_snapshot(entries) is None
    assert _sql_latest_task_snapshot([]) is None


def test_reinject_msg_shape():
    msg = _sql_build_reinject_msg("[任务态快照/task-state] 目标: X")
    assert msg["role"] == "system"
    assert msg["_is_summary_reinject"] is True
    assert "目标: X" in msg["content"]
    assert "不要重新自我介绍" in msg["content"]


# ─────────── 闭包门控行为（OFF=不注入 / ON=注入）───────────
def _simulate_loop(*, flag_on: bool, user_text: str, entries: list[dict], msgs: list[dict]) -> list[dict]:
    """复刻 main.py _run_chat 中 1B-4 段的门控 + 注入（_is_sentinel=False）。"""
    out = list(msgs)
    if flag_on and _sql_user_is_confused(user_text):
        latest = _sql_latest_task_snapshot(entries)
        if latest:
            reinject = _sql_build_reinject_msg(latest)
            ins_at = 0
            while ins_at < len(out) and out[ins_at].get("role") == "system":
                ins_at += 1
            out.insert(ins_at, reinject)
    return out


def test_off_no_detection():
    """OFF: 即便困惑措辞 + 有压缩快照，也不注入（字节级 BC）。"""
    base = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "你还记得刚才说的吗"},
    ]
    entries = [{"text": "[任务态快照/task-state] 目标: 做年报", "salience": 0.6}]
    out = _simulate_loop(flag_on=False, user_text="你还记得刚才说的吗", entries=entries, msgs=base)
    assert out == base  # 无任何注入
    assert not any(m.get("_is_summary_reinject") for m in out)


def test_on_detects_and_reinjects():
    """ON: 发生过压缩 + 用户问'刚才在干嘛' → 注入任务态 system 消息（在 system 段尾）。"""
    base = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "我们刚才在弄啥来着"},
    ]
    entries = [{"text": "[任务态快照/task-state] 目标: 做小学教育 PPT; 最近请求: 改第3页", "salience": 0.6}]
    out = _simulate_loop(flag_on=True, user_text="我们刚才在弄啥来着", entries=entries, msgs=base)
    reinjected = [m for m in out if m.get("_is_summary_reinject")]
    assert len(reinjected) == 1
    assert "小学教育 PPT" in reinjected[0]["content"]
    # 插在 system 段之后、第一条非 system 之前（index 1，persona 之后）。
    assert out[1]["_is_summary_reinject"] is True
    assert out[2]["role"] == "user"


def test_on_but_no_compaction_no_reinject():
    """ON + 困惑措辞，但本 session 没发生过压缩（L1 无快照）→ 不注入。"""
    base = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "你忘了我要什么"},
    ]
    entries = [{"text": "用户喜欢简洁", "salience": 0.5}]  # 无任务态快照
    out = _simulate_loop(flag_on=True, user_text="你忘了我要什么", entries=entries, msgs=base)
    assert out == base
    assert not any(m.get("_is_summary_reinject") for m in out)


def test_on_no_confused_phrasing_no_reinject():
    """ON + 有压缩快照，但用户措辞不困惑（正常请求）→ 不注入。"""
    base = [{"role": "system", "content": "persona"}, {"role": "user", "content": "帮我做个表格"}]
    entries = [{"text": "[任务态快照/task-state] 目标: 做年报", "salience": 0.6}]
    out = _simulate_loop(flag_on=True, user_text="帮我做个表格", entries=entries, msgs=base)
    assert out == base
