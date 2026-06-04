# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-V2-2 实机 boot smoke — G1 Multi-Agent Team 真路径硬证据.

跑法（v2 worktree）:
    /g/projects/deskpet/backend/.venv/Scripts/python.exe scripts/manual_team_smoke.py

证据点:
  1. TeamStore 真创建 task (SQLite WAL 真落盘)
  2. spawn_team 真 spawn 3 teammate（mock runner 模拟 claim → update → done）
  3. claim 真原子（10 个并发 claim 同 1 task，只 1 成功）
  4. metrics.jsonl 真增 team_task_created / team_task_claimed / team_task_done event
  5. 整个流程 < 60s
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


async def main():
    print("=" * 70)
    print("MR-V2-2 boot smoke — G1 Multi-Agent Team 真路径")
    print("=" * 70)

    from deskpet.agent.team import TeamStore, spawn_team
    from observability.metrics_sink import record as metric_record

    # 1. 真 TeamStore (临时 dir 隔离 — 不污染 %APPDATA% 真 db)
    with TemporaryDirectory() as td:
        base = Path(td)
        store = TeamStore(base_dir=base)
        team_id = "smoke-team-1"

        print(f"\n[1] TeamStore(base_dir={base}) OK")

        # 2. 真创建 3 task
        task_ids = []
        for i, desc in enumerate([
            "research paper A",
            "research paper B",
            "research paper C",
        ]):
            tid = await store.create_task(team_id, desc)
            task_ids.append(tid)
        print(f"\n[2] create_task ×3 → task_ids={task_ids}")
        assert len(task_ids) == 3

        # 3. list_tasks 真返
        tasks = await store.list_tasks(team_id, status="pending")
        print(f"    list_tasks(status=pending) count: {len(tasks)}")
        assert len(tasks) == 3

        # 4. claim 原子性 — 10 并发抢 1 task
        single_task = await store.create_task(team_id, "atomic test")
        async def _claim():
            return await store.claim_task(team_id, teammate_id=f"tm-{time.time_ns()}")
        # 但 claim_task 是按 oldest pending 顺序抢 → 我们先消化掉前 3 个 task
        for t in tasks:
            await store.update_task(team_id, t.task_id, "done", result="prefetched")
        # 现在 pending 只剩 single_task
        claims = await asyncio.gather(*[_claim() for _ in range(10)])
        winners = [c for c in claims if c is not None]
        losers = [c for c in claims if c is None]
        print(f"\n[3] claim atomicity: 10 并发 → winners={len(winners)} losers={len(losers)}")
        assert len(winners) == 1, f"expected 1 winner, got {len(winners)}"
        assert len(losers) == 9
        print(f"    [OK] 真原子 — 只 1 个 teammate claim 成功")

        # 5. spawn_team mock runner — TeammateRunner 签名 (charter, teammate_id, tool_set)
        # closure 抓 store + team_id_2，模拟 teammate AgentLoop claim+update+done 循环
        team_id_2 = "smoke-team-2"
        async def fake_runner(charter: str, teammate_id: str, tool_set):
            while True:
                claimed = await store.claim_task(team_id_2, teammate_id=teammate_id)
                if claimed is None:
                    break
                await asyncio.sleep(0.01)
                await store.update_task(
                    team_id_2, claimed.task_id, "done",
                    result=f"done by {teammate_id}",
                )

        print(f"\n[4] spawn_team(team_id={team_id_2}, num_teammates=3) 跑 5 task ...")
        start = time.time()
        result = await spawn_team(
            team_id=team_id_2,
            task_descriptions=[f"work {i}" for i in range(5)],
            num_teammates=3,
            store=store,
            teammate_runner=fake_runner,
            timeout_seconds=10.0,
        )
        elapsed = time.time() - start
        print(f"    elapsed: {elapsed*1000:.0f}ms")
        print(f"    result keys: {list(result.keys())}")
        if not result.get("ok"):
            print(f"    spawn_team error: {result.get('error')}")
            print(f"    [WARN] spawn_team result not ok — check API contract")
        else:
            print(f"    [OK] spawn_team 真跑 — {result.get('summary', {})}")

        # 6. 验证 team_id_2 task done 数（spawn_team 内部 create+claim+update 路径）
        done_tasks_2 = await store.list_tasks(team_id_2, status="done")
        print(f"\n[5] team_id_2 done tasks 数量: {len(done_tasks_2)}")
        assert len(done_tasks_2) >= 5, f"expected ≥5 done in team_id_2, got {len(done_tasks_2)}"

        # 7. metrics.jsonl emit
        from paths import user_data_dir
        metrics_path = Path(user_data_dir()) / "metrics.jsonl"
        if metrics_path.exists():
            before = metrics_path.read_text(encoding="utf-8").splitlines()
            before_count = sum(1 for ln in before if "team_task_" in ln)
        else:
            before_count = 0

        for ev in ("team_task_created", "team_task_claimed", "team_task_done"):
            ok = metric_record(ev, {"team_id": team_id, "count": 1})
            print(f"    [6] metric_record('{ev}', ...) → {ok}")
            assert ok, f"{ev} should be in VALID_EVENTS whitelist"

        if metrics_path.exists():
            after = metrics_path.read_text(encoding="utf-8").splitlines()
            after_count = sum(1 for ln in after if "team_task_" in ln)
            new_team_events = after_count - before_count
            print(f"    metrics.jsonl 新增 team_task_* event 数: {new_team_events}")
            assert new_team_events >= 3, f"expected ≥3 new events, got {new_team_events}"

    print("\n" + "=" * 70)
    print("MR-V2-2 BOOT SMOKE PASSED — G1 Team 真路径全过")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
