# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G3 — memory_search / memory_write / memory_read 工具端到端测试
（记忆系统严测 Phase 1）。

## 背景
盘点发现工具层覆盖仅 20%：`memory_search` / `memory_write` / `memory_read`
**零测试**（只有 memory_forget 有 8 个）。这三个工具是 agent 实际调用的记忆
读写入口，"真不真能用"之前没人验过。

## 本文件验什么
通过 `memory_tools.bind()` 注入**真 FactsStore**（非 mock 桩），调真 handler，
验证闭环：
- G3.1 write → read 闭环：写一条 → 用返回的 memory_id 读回 → value 一致
- G3.2 write → search 闭环：写一条 → search 精确子串 → 命中且内容正确
- G3.3 bind 状态：默认配置（memory_forget=False）下工具不再 not bound（F3 回归）
- G3.5 错误处理：未 bind / 缺参 / 不存在 id → 优雅返回 ok:false，不抛异常

## confound 排除
- G3.2 用**精确子串** query（不是自然语言）—— 因为 F5 未修，自然语言搜不出；
  这里只验"工具链路通 + 能搜到精确子串的"，语义召回有效性留给 F5 修复后（G2 Phase 2）。
- 每个测试用独立 tmp db + 测试结束 unbind，避免 module-level `_facts_store`
  跨测试污染。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.facts import FactsStore
from deskpet.tools import memory_tools


@pytest_asyncio.fixture
async def bound_facts(tmp_path: Path):
    """注入真 FactsStore 到 memory_tools 模块级 _facts_store，测试后还原。

    模拟 main.py lifespan 的 bind()（F3 修复后：facts_store 在就 bind，
    不受 memory_forget flag 门控）。
    """
    store = FactsStore(tmp_path / "facts.db")
    memory_tools.bind(
        facts_store=store,
        embedder=None,
        llm_call=None,
        enable_natural_language=False,
    )
    try:
        yield store
    finally:
        # 还原模块级状态，防跨测试污染
        memory_tools.bind(
            facts_store=None,  # type: ignore[arg-type]
            embedder=None,
            llm_call=None,
            enable_natural_language=False,
        )


# ----------------------------------------------------------------------
# G3.1 — memory_write → memory_read 闭环（写的能读回，value 一致）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g3_1_write_then_read_roundtrip(bound_facts: FactsStore) -> None:
    w = json.loads(
        await memory_tools._memory_write_handle(
            {"text": "用户喜欢喝乌龙茶", "tier": "preference", "salience": 0.8},
            "task1",
        )
    )
    assert w["ok"] is True, f"memory_write 应成功: {w}"
    mem_id = w["memory_id"]
    assert isinstance(mem_id, int)

    r = json.loads(
        await memory_tools._memory_read_handle({"memory_id": mem_id}, "task2")
    )
    assert r["ok"] is True, f"memory_read 应读回刚写的: {r}"
    # 双向断言：读回的 value 必须等于写入的内容（不是任意非空）
    assert r["fact"]["value"] == "用户喜欢喝乌龙茶"
    assert r["fact"]["category"] == "preference"


# ----------------------------------------------------------------------
# G3.2 — memory_write → memory_search 闭环（精确子串 query 命中且正确）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g3_2_write_then_search_exact_substring(
    bound_facts: FactsStore,
) -> None:
    await memory_tools._memory_write_handle(
        {"text": "用户的生日是十月一号", "tier": "profile"}, "t1"
    )
    await memory_tools._memory_write_handle(
        {"text": "用户养了一只叫旺财的猫", "tier": "profile"}, "t2"
    )
    # 精确子串 "旺财" ⊆ "...叫旺财的猫"（F5 未修，故用子串而非自然语言）
    s = json.loads(
        await memory_tools._memory_search_handle(
            {"query": "旺财", "top_k": 5}, "t3"
        )
    )
    assert s["ok"] is True, f"memory_search 应成功: {s}"
    # 双向断言：命中含"旺财"那条，排除"生日"那条
    vals = [r["value"] for r in s["results"]]
    assert any("旺财" in v for v in vals), f"应命中'旺财'那条: {vals}"
    assert not any("生日" in v for v in vals), f"不应命中无关的'生日'那条: {vals}"


# ----------------------------------------------------------------------
# WI-OH-2 path(a) — memory_write pinned=True → 产生 pinned preference fact
# 用户「记住我喜欢用 neovim，别忘了」→ LLM 调 memory_write(pinned=true)。
# 验收：fact 落库 + pinned=1 + 跳 daily_decay（即便老化）。
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wi_oh2_memory_write_pinned_creates_pinned_pref_fact(
    bound_facts: FactsStore,
) -> None:
    w = json.loads(
        await memory_tools._memory_write_handle(
            {"text": "记住我喜欢用 neovim", "pinned": True}, "t1"
        )
    )
    assert w["ok"] is True, f"memory_write 应成功: {w}"
    assert w["pinned"] is True, f"响应应标 pinned=True: {w}"
    # tier 默认 auto → preference 类别（长期保留）
    assert w["category"] == "preference"
    mem_id = w["memory_id"]

    # fact 行真带 pinned=1
    row = await bound_facts.get_by_id(mem_id)
    assert row is not None
    assert row["pinned"] == 1, f"fact 行应 pinned=1, 实际 {row.get('pinned')}"


@pytest.mark.asyncio
async def test_wi_oh2_pinned_pref_fact_skips_daily_decay(
    bound_facts: FactsStore,
) -> None:
    """pinned preference fact 即使老化也跳 daily_decay（用户逃生口生效）。"""
    import time as _time

    import aiosqlite

    # pinned 偏好
    w_pin = json.loads(
        await memory_tools._memory_write_handle(
            {"text": "永远记住我用 dvorak 键位", "pinned": True}, "t1"
        )
    )
    # 非 pinned 偏好（对照组）
    w_norm = json.loads(
        await memory_tools._memory_write_handle(
            {"text": "我现在在用 qwerty"}, "t2"
        )
    )
    assert w_pin["pinned"] is True
    assert w_norm["pinned"] is False

    # 把两条都改成 30 天前最后召回 + 非零 decay_rate，制造衰减条件
    stale = _time.time() - 86400 * 30
    async with aiosqlite.connect(bound_facts._db_path) as conn:
        await conn.execute(
            "UPDATE facts SET last_recalled = ?, decay_rate = 0.05", (stale,)
        )
        await conn.commit()

    await bound_facts.daily_decay()

    r_pin = await bound_facts.get_by_id(w_pin["memory_id"])
    r_norm = await bound_facts.get_by_id(w_norm["memory_id"])
    # memory_write 默认 salience=0.5 → confidence=0.5。pinned 跳衰减 → 仍 0.5；
    # 非 pinned 30 天衰减 → < 0.5。
    assert r_pin["confidence"] == pytest.approx(0.5), (
        f"pinned 偏好 confidence 应不衰减(=初始0.5)，实际 {r_pin['confidence']}"
    )
    assert r_norm["confidence"] < 0.5, (
        f"非 pinned 偏好应衰减(< 初始0.5)，实际 {r_norm['confidence']}"
    )


@pytest.mark.asyncio
async def test_wi_oh2_memory_write_default_not_pinned(
    bound_facts: FactsStore,
) -> None:
    """不带 pinned 参数 → 默认 False（BC，老调用方不受影响）。"""
    w = json.loads(
        await memory_tools._memory_write_handle({"text": "随手记一条"}, "t1")
    )
    assert w["ok"] is True
    assert w["pinned"] is False
    row = await bound_facts.get_by_id(w["memory_id"])
    assert (row.get("pinned") or 0) == 0


# ----------------------------------------------------------------------
# G3.3 — bind 状态（F3 回归：默认配置下工具可用，非 not bound）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g3_3_search_bound_under_default_config(
    bound_facts: FactsStore,
) -> None:
    """F3 回归：memory_forget=False（默认）时 memory_search 仍可用。

    旧 bug：bind 被 memory_forget flag 门控 → 默认配置下 memory_search not bound。
    F3 修复后：facts_store 在就 bind。这里 bound_facts fixture 即模拟修复后行为。
    """
    s = json.loads(
        await memory_tools._memory_search_handle({"query": "anything"}, "t")
    )
    assert s["ok"] is True, "F3: 默认配置下 memory_search 应可用，不是 not bound"
    assert "not bound" not in json.dumps(s)


# ----------------------------------------------------------------------
# G3.5 — 错误处理（未 bind / 缺参 / 不存在 id → 优雅 ok:false）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g3_5a_not_bound_returns_error_not_crash() -> None:
    """未 bind 时（facts_store=None）三个工具返回 ok:false，不抛异常。"""
    memory_tools.bind(
        facts_store=None,  # type: ignore[arg-type]
        embedder=None, llm_call=None, enable_natural_language=False,
    )
    for handler, args in [
        (memory_tools._memory_write_handle, {"text": "x"}),
        (memory_tools._memory_read_handle, {"memory_id": 1}),
        (memory_tools._memory_search_handle, {"query": "x"}),
    ]:
        out = json.loads(await handler(args, "t"))
        assert out["ok"] is False
        assert "not bound" in out["error"]


@pytest.mark.asyncio
async def test_g3_5b_missing_or_bad_args(bound_facts: FactsStore) -> None:
    """缺参 / 坏参 → 优雅 ok:false。"""
    # write 缺 text
    w = json.loads(await memory_tools._memory_write_handle({}, "t"))
    assert w["ok"] is False and "text" in w["error"]
    # read 坏 id
    r = json.loads(
        await memory_tools._memory_read_handle({"memory_id": "abc"}, "t")
    )
    assert r["ok"] is False and "integer" in r["error"]
    # search 空 query
    s = json.loads(await memory_tools._memory_search_handle({"query": ""}, "t"))
    assert s["ok"] is False and "non-empty" in s["error"]


@pytest.mark.asyncio
async def test_g3_5c_read_nonexistent_id(bound_facts: FactsStore) -> None:
    """读不存在的 id → ok:false not found，不崩。"""
    r = json.loads(
        await memory_tools._memory_read_handle({"memory_id": 999999}, "t")
    )
    assert r["ok"] is False and "not found" in r["error"]


# ----------------------------------------------------------------------
# G3.6 — IDEM：同一文本 memory_write 两次 → facts 行数第二次不增（去重幂等）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g3_6_write_same_text_twice_is_idempotent(
    bound_facts: FactsStore, tmp_path: Path
) -> None:
    """重复 memory_write 同一文本不应在 facts 表堆积重复记录。

    历史 bug（2026-06-27 windows-mcp IDEM-B 真测发现）：``memory_write`` 用
    ``key = f"memory_{int(time.time()*1000)}"`` 时间戳 key → 每次新插一行 →
    重复发同句累积。修复 = 内容稳定 key（归一化哈希）+ ``find_active`` 命中即
    ``update_value`` touch 不插行。本测断言"跑两次副作用次数不变"（幂等根基）。
    """
    import sqlite3

    db = tmp_path / "facts.db"

    def count_facts() -> int:
        con = sqlite3.connect(db)
        try:
            return con.execute("SELECT count(*) FROM facts").fetchone()[0]
        finally:
            con.close()

    text = "我叫小王，是后端程序员，最爱 neovim 和 Rust。"

    w1 = json.loads(await memory_tools._memory_write_handle({"text": text}, "t1"))
    assert w1["ok"] is True
    assert w1.get("deduped") is False  # 首次落库
    n1 = count_facts()
    assert n1 == 1, f"首次写应落 1 行，实际 {n1}"

    # 完全相同文本再写一次（含前后空白/大小写无关的归一化）
    w2 = json.loads(
        await memory_tools._memory_write_handle({"text": "  " + text + "  "}, "t2")
    )
    assert w2["ok"] is True
    assert w2.get("deduped") is True, "第二次相同文本应去重（touch 不插行）"
    assert w2["memory_id"] == w1["memory_id"], "去重应复用既有 fact id"
    n2 = count_facts()
    assert n2 == n1, f"重复写同一文本 facts 行数不应增长: {n1} -> {n2}"

    # 不同文本仍应新增一行（去重不误杀不同内容）
    w3 = json.loads(
        await memory_tools._memory_write_handle(
            {"text": "我养了一只叫咪咪的猫。"}, "t3"
        )
    )
    assert w3["ok"] is True and w3.get("deduped") is False
    assert count_facts() == n1 + 1, "不同内容应新增 1 行"
