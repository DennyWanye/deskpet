"""P4-S2 smoke — end-to-end Embedder + VectorWorker + SessionDB hook.

Scenario:
  1. 起临时 state.db（tmp dir，跑完删）
  2. 起 mock-mode Embedder + VectorWorker
  3. 把 VectorWorker.enqueue 作为 SessionDB.on_message_written hook 接上
  4. 写 20 条 user/assistant 消息
  5. 等 flush（interval 2s + 兜底 1s）
  6. 查 messages_vec 确认有 20 行
  7. 打印 `[OK] mock embedder smoke passed: 20 rows in messages_vec`

Extra（可选）：
  * 如果 %LocalAppData%\\deskpet\\models\\bge-m3-int8 已就位 → 再跑一次
    真模型 time 5 条 encode 的 latency 打印出来。

Exit codes:
  0 — mock 链路全通（ship 门槛）
  1 — mock 流程任一步失败（stderr 打印细节）

Real model 测试只会打印 latency 不会影响 exit code（没模型就跳过）。

Usage:
    python backend/scripts/smoke_deskpet_embedder.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import time
from pathlib import Path

# 让脚本能在 git worktree / dev shell 下直接跑 —— 把 backend/ 塞 sys.path。
_REPO_BACKEND = Path(__file__).resolve().parent.parent
if str(_REPO_BACKEND) not in sys.path:
    sys.path.insert(0, str(_REPO_BACKEND))

from deskpet.memory.embedder import EMBEDDING_DIM, Embedder  # noqa: E402
from deskpet.memory.session_db import SessionDB  # noqa: E402
from deskpet.memory.vector_worker import VectorWorker  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("smoke_embedder")


async def _mock_smoke(db_path: Path) -> int:
    """Mock 链路 end-to-end。返回 exit code（0 = OK）。"""
    embedder = Embedder(
        model_path=db_path.parent / "no-bge-dir",  # 不存在 → mock
        use_mock_when_missing=True,
    )
    await embedder.warmup()
    assert embedder.is_mock(), "expected mock mode when model dir missing"

    session_db = SessionDB(db_path)

    worker = VectorWorker(
        embedder,
        session_db,
        batch_size=8,
        flush_interval_s=2.0,
    )

    # 接上 hook：session_db.append_message → worker.enqueue
    # 这是 MemoryManager (P4-S4) 将要做的典型装配。这里直接手工模拟。
    async def _hook(msg_id: int, content: str) -> None:
        await worker.enqueue(msg_id, content)

    session_db._on_message_written = _hook  # direct set：smoke 允许
    await session_db.initialize()
    await worker.start()

    try:
        sid = await session_db.create_session({"origin": "smoke"})
        # 20 条消息，user/assistant 交替
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            await session_db.append_message(sid, role, f"smoke msg #{i}")

        # 等 flush：interval=2s，额外留 1s 兜底
        await asyncio.sleep(3.0)
    finally:
        # drain 所有残留
        await worker.stop(drain=True)

    # 验证 messages_vec 行数
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            vec_rows = conn.execute(
                "SELECT COUNT(*) FROM messages_vec"
            ).fetchone()[0]
        except Exception:  # noqa: BLE001
            log.warning("sqlite-vec not available, falling back to messages.embedding count")
            vec_rows = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL"
            ).fetchone()[0]

        blob_rows = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    await embedder.close()
    await session_db.close()

    stats = worker.stats()
    log.info("worker stats: %s", stats)

    if vec_rows != 20:
        print(
            f"[FAIL] mock embedder smoke: expected 20 rows in messages_vec, got {vec_rows}",
            file=sys.stderr,
        )
        return 1
    if blob_rows != 20:
        print(
            f"[FAIL] messages.embedding BLOB: expected 20, got {blob_rows}",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] mock embedder smoke passed: {vec_rows} rows in messages_vec")
    return 0


async def _real_smoke_if_available() -> None:
    """If BGE-M3 weights present，跑一次 5-text encode 打印 latency。

    失败/跳过都不影响 exit code。"""
    e = Embedder(use_mock_when_missing=True)  # 有模型就用真，没模型走 mock
    try:
        await e.warmup()
    except Exception as exc:  # noqa: BLE001
        log.info("real embedder warmup skipped: %s", exc)
        return

    if e.is_mock():
        # 默认 path 不存在，跳过真模型 bench
        log.info(
            "skipping real-model latency bench: model not downloaded at default path"
        )
        await e.close()
        return

    texts = [
        "今天天气很好",
        "我想学 python",
        "DeskPet 启动冷启动验收",
        "agent harness + long-term memory",
        "一起学新技能",
    ]
    t0 = time.perf_counter()
    vecs = await e.encode(texts)
    dt_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "real BGE-M3 latency: %d texts → shape=%s in %.1fms",
        len(texts),
        vecs.shape,
        dt_ms,
    )
    print(f"[OK] real bge-m3 latency: 5 texts in {dt_ms:.1f}ms")
    await e.close()


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="deskpet-smoke-") as td:
        db_path = Path(td) / "state.db"
        t0 = time.perf_counter()
        rc = await _mock_smoke(db_path)
        dt_mock = (time.perf_counter() - t0) * 1000
        log.info("mock smoke total wall time: %.1fms", dt_mock)
        if rc != 0:
            return rc

        # 真模型 latency（可选）
        await _real_smoke_if_available()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("[INTERRUPTED]", file=sys.stderr)
        raise SystemExit(130)
