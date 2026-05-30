# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-S-2 实机 boot smoke — /goal command 真路径 + GoalChecker 真调用.

跑法：
    G:\\projects\\deskpet\\backend\\.venv\\Scripts\\python.exe scripts/manual_goal_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


async def main():
    print("=" * 70)
    print("MR-S-2 boot smoke — /goal command 真路径 + GoalChecker 接电")
    print("=" * 70)

    # 1. 真构造 SessionGoalStore
    from deskpet.agent.goal_store import SessionGoalStore
    store = SessionGoalStore()
    print(f"\n[1] SessionGoalStore() OK")

    # 2. 调真 dispatch_slash_command('/goal write a haiku')
    from deskpet.commands import dispatch_slash_command
    print("\n[2] dispatch_slash_command('goal', 'write a haiku about cats', 'sid-test')")
    res = await dispatch_slash_command(
        "goal", "write a haiku about cats", "sid-test",
        session_goal_store=store,
    )
    print(f"    type: {res['type']}")
    print(f"    text: {res.get('text', '')!r}")
    print(f"    max_iterations: {res.get('max_iterations')}")
    assert res["type"] == "goal_set"
    assert res["text"] == "write a haiku about cats"

    # 3. 验证 store 真 set
    goal = store.get("sid-test")
    assert goal is not None
    assert goal.text == "write a haiku about cats"
    print(f"    [OK] store.get('sid-test') 真返 SessionGoal 实例")

    # 4. /goal clear
    print("\n[3] dispatch_slash_command('goal', 'clear', 'sid-test')")
    res2 = await dispatch_slash_command(
        "goal", "clear", "sid-test", session_goal_store=store,
    )
    assert res2["type"] == "goal_cleared"
    assert res2["ok"] is True
    assert store.get("sid-test") is None
    print(f"    [OK] /goal clear 真清除")

    # 5. GoalChecker 真调用 — mock LLM
    from deskpet.agent.goal_checker import GoalChecker

    mock_llm = AsyncMock(return_value='{"done": false, "hint": "no haiku yet"}')
    checker = GoalChecker(llm_call=mock_llm)
    done, hint = await checker.check(
        "write a haiku",
        [{"role": "assistant", "content": "let me think"}],
    )
    print(f"\n[4] GoalChecker.check (mock LLM 返 done=false)")
    print(f"    done: {done}, hint: {hint!r}")
    assert done is False
    assert "haiku" in hint
    print(f"    [OK] GoalChecker 真接电 + JSON parse 正确")

    # 6. metrics.jsonl 应不变（GoalChecker 单独调用不直接 emit；agent_loop 接电才 emit）
    from paths import user_data_dir
    metrics_path = Path(user_data_dir()) / "metrics.jsonl"
    if metrics_path.exists():
        before_lines = metrics_path.read_text(encoding="utf-8").splitlines()
        before_goal_count = sum(1 for ln in before_lines if "goal_checker_invoked" in ln)
        print(f"\n[5] metrics.jsonl 当前 goal_checker_invoked 行数: {before_goal_count}")

    # 7. 真 emit metric 验证写盘
    from observability.metrics_sink import record
    ok = record("goal_checker_invoked", {"ok": True, "count": 1})
    assert ok, "record() should return True"
    print(f"    [6] record('goal_checker_invoked', ...) 返 True")

    if metrics_path.exists():
        after_lines = metrics_path.read_text(encoding="utf-8").splitlines()
        after_goal_count = sum(1 for ln in after_lines if "goal_checker_invoked" in ln)
        diff = after_goal_count - before_goal_count
        print(f"    [7] metrics.jsonl 新增 goal_checker_invoked 行数: {diff}")
        assert diff >= 1, "应有新增 goal_checker_invoked event"
        # 打印最后一条
        last = [ln for ln in after_lines if "goal_checker_invoked" in ln][-1]
        print(f"    [OK] 最新 event: {last}")

    print("\n" + "=" * 70)
    print("MR-S-2 BOOT SMOKE PASSED — /goal 真路径 + metrics.jsonl 真写")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
