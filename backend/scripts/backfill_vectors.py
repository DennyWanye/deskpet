# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""一次性补齐历史 messages 的向量索引。

向 messages_vec 写入所有 messages.embedding IS NULL 的历史消息的
BGE-M3 向量。可中断重启 — backfill_missing 看每条 message 的
indexed_at 字段，已索引的不会重复写。

要求：backend 不在跑（共享 state.db 写锁会冲突）。

使用：
    cd backend && python -m scripts.backfill_vectors
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

DEFAULT_DB = (
    Path.home() / "AppData" / "Roaming" / "deskpet" / "data" / "state.db"
    if sys.platform == "win32"
    else Path.home() / ".local" / "share" / "deskpet" / "data" / "state.db"
)
DEFAULT_MODEL = (
    Path.home() / "AppData" / "Local" / "deskpet" / "models" / "bge-m3-int8"
    if sys.platform == "win32"
    else Path.home() / ".local" / "share" / "deskpet" / "models" / "bge-m3-int8"
)


async def main() -> int:
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.vector_worker import VectorWorker

    sdb = SessionDB(db_path=DEFAULT_DB)
    await sdb.initialize()

    emb = Embedder(
        model_path=DEFAULT_MODEL, mode="subprocess", use_mock_when_missing=False,
    )
    print("[backfill] warming up embedder…", flush=True)
    await emb.warmup()
    if emb.is_mock():
        print("[backfill] FAIL: embedder fell back to mock", flush=True)
        return 1
    print(f"[backfill] embedder ready (device=cuda)", flush=True)

    vw = VectorWorker(
        embedder=emb, session_db=sdb, batch_size=32, flush_interval_s=1.0,
    )
    await vw.start()

    t0 = perf_counter()
    print("[backfill] backfill_missing() starting…", flush=True)
    n = await vw.backfill_missing()
    elapsed = perf_counter() - t0
    rate = n / elapsed if elapsed > 0 else 0.0
    print(
        f"[backfill] PASS: indexed {n} messages in {elapsed:.1f}s ({rate:.0f}/s)",
        flush=True,
    )

    await vw.stop()
    await emb.close()
    await sdb.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
