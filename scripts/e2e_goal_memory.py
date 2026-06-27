# SPDX-License-Identifier: BUSL-1.1
"""FP-4 TDD Task 6 — goal↔facts 双源契约 live-smoke.

防 `feedback_cross_layer_contract` 坑：B-10 双写钩让一个 goal 同时存在两处——
  1. P0-1 goal store（session_goals 表，via SessionGoalStore + SessionDB）
  2. facts 表（category=goal, scope=session，via FactsStore.upsert）

契约不变量：set 一个 goal 后，两处查到的是**同一份 goal 文本**，不漂移。

本脚本复刻 main.py:1305-1323 的 `_goal_to_facts` 双写钩接线（goal_store
不 import facts → 钩由闭包注入），跑真 SessionDB + 真 FactsStore（临时 db，
mock embedder），三个场景验证契约。

跑法（必须 exit 0 + 打印 PASS）：
    PYTHONPATH=/path/to/deskpet/backend \
      /path/to/deskpet/backend/.venv/Scripts/python.exe \
      /path/to/deskpet/scripts/e2e_goal_memory.py

退出码 0 = 双源一致；非 0 = 契约被破坏（详情打印到 stderr）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

# Windows 控制台默认 GBK，会噎住中文/特殊字符的 print → 强制 stdout/stderr UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — 老 Python / 非 TextIO 时静默跳过
        pass


# ─── 测试时序关键：钩是 fire-and-forget task ──────────────────────────────
# SessionGoalStore.set() 内部调 _fire_on_goal_set → asyncio.ensure_future(_run())
# 并把 task 加进 store._fanout_tasks 强引用 set（done 后 discard）。
# 单纯 `await asyncio.sleep(0)` 只让出一次事件循环，钩里有多个 await（upsert →
# _ensure_schema → aiosqlite connect/commit）时一次让出不够。
# → 显式 gather 当前所有未完成的 fanout task，确保全部 await 到底。
async def _drain_hook_tasks(store) -> None:
    """等 store 里所有 fire-and-forget 钩 task 跑完（确定性，不靠 sleep 猜）。"""
    # 先让出一次，给 ensure_future 排进的 task 一个起跑机会
    await asyncio.sleep(0)
    pending = [t for t in list(store._fanout_tasks) if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # 二次兜底：钩里若链式再 spawn（本钩不会，但稳妥）——再扫一遍
    pending2 = [t for t in list(store._fanout_tasks) if not t.done()]
    if pending2:
        await asyncio.gather(*pending2, return_exceptions=True)


def _make_goal_to_facts(facts_store):
    """复刻 main.py:1305-1323 的 _goal_to_facts 闭包（B-10 双写钩）。

    goal_store 不 import facts（§1.7 冻结约束）→ 钩由 main.py 注入闭包；
    这里 1:1 复刻该闭包的 upsert 参数，确保 smoke 测的就是生产接线。
    """
    _fs_ref = facts_store  # closure capture

    async def _goal_to_facts(sid: str, text: str) -> None:
        try:
            await _fs_ref.upsert(
                category="goal",
                subject="user",
                key=f"goal_{sid}",
                value=text,
                confidence=0.9,
                source_msg_id=None,
                evidence=f"goal set for session {sid}",
                scope="session",
            )
        except Exception as exc:  # noqa: BLE001 — safe-fail（同生产）
            print(f"  [hook] goal_to_facts_upsert_failed sid={sid}: {exc}",
                  file=sys.stderr)

    return _goal_to_facts


async def _facts_goal_value(facts_store, sid: str):
    """查 facts 表里 category=goal / key=goal_<sid> 那条的 value，无则 None。"""
    rows = await facts_store.list_active(category="goal", scope="session")
    for r in rows:
        if r.get("key") == f"goal_{sid}":
            return r.get("value")
    return None


async def _session_goal_text(session_db, sid: str):
    """查 P0-1 goal store（session_goals 表）里 active 目标文本，无则 None。"""
    rows = await session_db.get_active_goals(sid)
    return rows[0]["text"] if rows else None


async def _main() -> int:
    from deskpet.agent.goal_store import SessionGoalStore
    from deskpet.memory.facts import FactsStore
    from deskpet.memory.session_db import SessionDB

    tmp = tempfile.mkdtemp(prefix="deskpet_goalmem_")
    db_path = os.path.join(tmp, "state.db")
    print(f"[setup] 临时 db = {db_path}（不碰真实 user DB）")

    # ── 构造真栈：SessionDB + FactsStore（mock embedder=None）+ goal_store ──
    session_db = SessionDB(db_path=db_path)
    await session_db.initialize()
    facts_store = FactsStore(db_path, embedder=None)  # None → 走 LIKE 兜底，不写向量

    failures: list[str] = []

    # ──────────────────────── 场景 1：双写一致 ────────────────────────────
    # set 一个 goal → P0-1 与 facts 两处都查到同一份文本。
    sid1 = "sess-双写一致"
    goal_text1 = "整理本周会议纪要并生成PPT"

    store = SessionGoalStore()
    store.bind_persistence(session_db)
    store.bind_on_goal_set(_make_goal_to_facts(facts_store))  # 复刻 main.py 接线

    goal = store.set(sid1, goal_text1)
    await store.persist(goal)          # P0-1 落库（session_goals 表）
    await _drain_hook_tasks(store)     # 等 facts 双写钩跑完

    p01_text = await _session_goal_text(session_db, sid1)
    facts_text = await _facts_goal_value(facts_store, sid1)

    print("\n[场景1] 双写一致")
    print(f"  P0-1  (session_goals): {p01_text!r}")
    print(f"  facts (category=goal): {facts_text!r}")

    if p01_text is None:
        failures.append("场景1: P0-1 session_goals 查不到 goal（钩/persist 断链）")
    if facts_text is None:
        failures.append("场景1: facts 表查不到 category=goal（B-10 双写钩没 fire）")
    if (p01_text is not None and facts_text is not None
            and p01_text != facts_text):
        failures.append(
            f"场景1: 双源漂移！P0-1={p01_text!r} != facts={facts_text!r}"
        )
    if (p01_text == goal_text1 and facts_text == goal_text1):
        print("  [OK] 两处文本相等且 == 原始 goal 文本")

    # ──────────────────────── 场景 2：GC 不丢 ─────────────────────────────
    # 连 set 3 个不同 session 的 goal，全部 await 钩完成 → facts 表有 3 条
    # category=goal（防 fire-and-forget task 被 asyncio GC 丢；主线刚修了
    # _fanout_tasks 强引用，这里兜底验证）。
    print("\n[场景2] GC 不丢（连 set 3 个 session）")
    gc_sids = ["sess-gc-1", "sess-gc-2", "sess-gc-3"]
    gc_texts = ["写一首关于猫的俳句", "调研竞品定价策略", "重构 goal_store 持久化"]
    store2 = SessionGoalStore()
    store2.bind_persistence(session_db)
    store2.bind_on_goal_set(_make_goal_to_facts(facts_store))
    for sid, text in zip(gc_sids, gc_texts):
        g = store2.set(sid, text)
        await store2.persist(g)
    await _drain_hook_tasks(store2)    # 一次性等齐全部 3 个钩

    goal_rows = await facts_store.list_active(category="goal", scope="session")
    gc_keys = {f"goal_{s}" for s in gc_sids}
    got_keys = {r["key"] for r in goal_rows if r["key"] in gc_keys}
    print(f"  facts category=goal 里命中 3 个 gc session 的条数: {len(got_keys)}/3")
    print(f"  命中 keys: {sorted(got_keys)}")
    if len(got_keys) != 3:
        missing = gc_keys - got_keys
        failures.append(
            f"场景2: GC 丢条！facts 只有 {len(got_keys)}/3，缺 {sorted(missing)}"
        )
    else:
        # 再校验每条文本一致（双源不漂移，逐条）
        for sid, text in zip(gc_sids, gc_texts):
            fv = await _facts_goal_value(facts_store, sid)
            if fv != text:
                failures.append(
                    f"场景2: {sid} 文本漂移 facts={fv!r} != 期望={text!r}"
                )
        print("  [OK] 3 条全在 + 逐条文本一致")

    # ──────────────────────── 场景 3：flag 语义（BC）────────────────────────
    # 不 bind 钩时 set goal → facts 表无该 session 的 category=goal 记录。
    # 证明双写钩是写 facts 的唯一路径（flag-OFF 字节级 BC）。
    print("\n[场景3] flag 语义 — 不 bind 钩则 facts 无 goal 记录")
    sid3 = "sess-no-hook"
    store3 = SessionGoalStore()
    store3.bind_persistence(session_db)
    # 故意不调 bind_on_goal_set
    g3 = store3.set(sid3, "这条不该进 facts")
    await store3.persist(g3)
    await _drain_hook_tasks(store3)    # 即便没钩也走一遍 drain，证明真没 task

    facts_text3 = await _facts_goal_value(facts_store, sid3)
    p01_text3 = await _session_goal_text(session_db, sid3)
    print(f"  P0-1  (应有):   {p01_text3!r}")
    print(f"  facts (应为None): {facts_text3!r}")
    if p01_text3 is None:
        failures.append("场景3: P0-1 该有 goal 却查不到（persist 路径独立于钩，不应受影响）")
    if facts_text3 is not None:
        failures.append(
            f"场景3: 没 bind 钩 facts 却有 goal 记录={facts_text3!r}（钩不是唯一写路径？）"
        )
    if p01_text3 is not None and facts_text3 is None:
        print("  [OK] P0-1 有、facts 无 → 钩是写 facts 的唯一路径（BC）")

    # ──────────────────────────── 判定 ──────────────────────────────────
    if failures:
        print("\n" + "=" * 64, file=sys.stderr)
        print("FAIL: goal↔facts 双源契约被破坏", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print("=" * 64, file=sys.stderr)
        return 1

    print("\n" + "=" * 64)
    print("PASS: goal↔facts 双源一致")
    print("  场景1 双写一致 [OK] | 场景2 GC 不丢 [OK] | 场景3 钩唯一写路径 [OK]")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
