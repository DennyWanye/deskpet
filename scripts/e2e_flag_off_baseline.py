# SPDX-License-Identifier: BUSL-1.1
"""R-T5 — flag-OFF 字节基线：goal_mode OFF 时 session_goals 不建表。

用法：python scripts/e2e_flag_off_baseline.py（从 backend 目录跑：
python ../scripts/e2e_flag_off_baseline.py）
退出码 0 = 基线通过；非 0 = 字节契约被破坏。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import sys
import tempfile


async def _main() -> int:
    from deskpet.memory.session_db import SessionDB

    tmp = tempfile.mkdtemp(prefix="deskpet_flagoff_")
    db_path = os.path.join(tmp, "state.db")

    # flag-OFF 模拟：SessionDB 初始化但绝不调 goal store / ensure goal 路径
    db = SessionDB(db_path=db_path)
    await db.initialize()
    # 模拟一轮对话写入（session_plans 等既有表会建，goal 表不应建）
    await db.upsert_session_plan("s1", "r", [], False)

    with sqlite3.connect(db_path) as conn:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    if "session_goals" in names:
        print("FAIL: session_goals table created with goal_mode OFF", file=sys.stderr)
        return 1
    if "goal_tasks" in names:
        print("FAIL: goal_tasks table created with goal_mode OFF", file=sys.stderr)
        return 1

    # 字节快照（供后续 FP 比对 hash 漂移）
    with open(db_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print(f"PASS: flag-OFF baseline ok; no session_goals table; sha256={digest[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
