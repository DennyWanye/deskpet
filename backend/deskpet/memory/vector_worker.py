"""P4-S2 L3 vector write worker — async batch embedder → messages_vec.

职责
----
把 ``(message_id, text)`` tuple 异步排队，按 "8 条或 2s" 先到先触发的
规则批量跑 embedder，写入：

1. ``messages_vec`` 虚拟表（sqlite-vec vec0）——用于 L3 语义召回
2. ``messages.embedding`` BLOB 列——作为 vec 扩展不可用时的 raw fallback
   （P4-S3 retriever 在 vec 不可用场景下会读这个列）

两张表都写是故意的：vec 扩展 load 失败时（sqlite-vec DLL 缺失 / 老内核）
只要 messages.embedding 还在，就能做 Python 侧暴力 cosine 搜（性能降，
但功能不断）。详见 design.md §D-ARCH-1 "L3 降级启动"。

核心设计决定
-------------
* **失败隔离**（spec "embedding 失败 MUST NOT 阻塞 message 主写入"）：
  encode / SQL 抛错只 log + 累计 ``stats['failed']``，不 re-raise，
  让队列的下一条继续跑。
* **背压**：``asyncio.Queue(maxsize=1024)``，enqueue 满时 drop 最老条目
  （log warning 到 ``dropped``）。优先保证新消息的上下文相关性不丢。
* **drain on stop**：``stop(drain=True)`` 会把 queue 里的剩余 batch 全
  处理完再返回，最多等 10s（``_DRAIN_TIMEOUT``）兜底卡死。
* **backfill**：``backfill_missing`` 扫 ``messages.embedding IS NULL`` 的
  历史消息逐批回填，低优先级 —— 每跑完一个 backfill batch 后 ``await
  asyncio.sleep(0)`` 让出 loop，这样新 enqueue 的消息能及时插进来。
* **rowid = message_id**：写 messages_vec 时用 ``INSERT ... (message_id,
  embedding) VALUES (?, ?)`` —— messages_vec 是 vec0 表，message_id 作为
  PRIMARY KEY；同一 message_id 再写一次走 REPLACE 语义手动删+插。

Ref: spec "Vector Memory (L3) — sqlite-vec + BGE-M3" +
     tasks.md §4.3 §4.4 §4.6.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any, Optional

import numpy as np

try:  # sqlite-vec 可能不可用（和 session_db 的降级策略对齐）
    import sqlite_vec  # type: ignore
    _HAS_SQLITE_VEC = True
except ImportError:
    sqlite_vec = None  # type: ignore
    _HAS_SQLITE_VEC = False

from deskpet.memory.embedder import EMBEDDING_DIM, Embedder
from deskpet.memory.session_db import SessionDB

log = logging.getLogger(__name__)

# queue 容量 —— 1024 条足够覆盖 agent 一次对话 + backfill 并行；再大则
# 意味着 embedder 已经严重掉队，此时 drop 比无限攒更好。
_QUEUE_MAXSIZE = 1024
# drain 等待上限。嵌入算很慢时（CPU 真模型）10s 也可能不够，但 10s 是
# 一个"人类能接受的退出延迟"常识值，超过用户会直接杀进程。
_DRAIN_TIMEOUT = 10.0


class VectorWorker:
    """Batch vector writer，需要外部 ``start()`` / ``stop()`` 管理生命周期。"""

    def __init__(
        self,
        embedder: Embedder,
        session_db: SessionDB,
        *,
        batch_size: int = 8,
        flush_interval_s: float = 2.0,
    ) -> None:
        self._embedder = embedder
        self._session_db = session_db
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s

        self._queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue(
            maxsize=_QUEUE_MAXSIZE
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

        # 监控计数。外部通过 stats() 读。
        self._queued_total = 0  # 累计入队条数
        self._written = 0       # 成功写 messages_vec 的数量
        self._failed = 0        # encode / write 抛错条数
        self._dropped = 0       # queue 满被丢弃的条数
        self._last_flush_ts: float | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动后台 batching task。幂等：二次调用 no-op。"""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(), name="vector-worker"
        )
        log.debug(
            "VectorWorker started (batch_size=%d, flush_interval_s=%.1f)",
            self._batch_size,
            self._flush_interval_s,
        )

    async def stop(self, *, drain: bool = True) -> None:
        """停止 worker。

        drain=True：把 queue 里残留的先跑完（上限 _DRAIN_TIMEOUT 秒）。
        drain=False：立即 cancel，丢弃 queue。
        """
        if self._task is None:
            return

        if drain:
            try:
                # 等 queue 清空 + 当前 batch 跑完。用 wait_for 包一层超时。
                await asyncio.wait_for(
                    self._wait_for_drain(), timeout=_DRAIN_TIMEOUT
                )
            except asyncio.TimeoutError:
                log.warning(
                    "VectorWorker drain exceeded %.1fs; cancelling anyway "
                    "(%d items left)",
                    _DRAIN_TIMEOUT,
                    self._queue.qsize(),
                )

        self._stop_event.set()
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None
        log.debug("VectorWorker stopped")

    async def _wait_for_drain(self) -> None:
        """Spin 直到 queue 为空。run_loop 会在每次 flush 后检查 queue。"""
        while not self._queue.empty():
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    # Enqueue / backfill
    # ------------------------------------------------------------------

    async def enqueue(self, message_id: int, text: str) -> None:
        """投一条 (message_id, text) 到内部 queue。非阻塞（背压走 drop）。

        队列满时 drop 最老的一条（``get_nowait`` 拿掉队首），然后 put 新条。
        优先保留新消息的上下文相关性。
        """
        if not text:
            # 空内容没意义 —— 跳过，不占配额。
            return
        try:
            self._queue.put_nowait((message_id, text))
        except asyncio.QueueFull:
            # drop 队首最老一条
            try:
                dropped = self._queue.get_nowait()
                self._queue.task_done()
                self._dropped += 1
                log.warning(
                    "vector_worker queue full (maxsize=%d); dropped oldest "
                    "message_id=%s",
                    _QUEUE_MAXSIZE,
                    dropped[0],
                )
            except asyncio.QueueEmpty:
                pass
            # 再 put 新条；此时一定能成功（刚 drop 了一个位置）
            try:
                self._queue.put_nowait((message_id, text))
            except asyncio.QueueFull:
                # race：另一个 enqueue 填回来了。直接丢弃新条，stats 计数。
                self._dropped += 1
                return
        self._queued_total += 1

    async def backfill_missing(self, limit: int | None = None) -> int:
        """扫描 ``messages`` 表里 ``embedding IS NULL`` 的条目，分批回填。

        返回实际处理（成功 + 失败）的条数。backfill 故意**不**走 enqueue
        队列——避免和实时消息抢资源 + 避免 queue 被一次性拉爆。

        每批处理完后 sleep(0) 让出 event loop，保证实时 enqueue 不卡。
        """
        # 优先让 embedder 就绪（mock 也算）。
        if not self._embedder.is_ready():
            await self._embedder.warmup()

        # 简单起见：短 connection + 一次性拉全部 id。真跑 10w 条时
        # 改成分页 SELECT 也可（P4-S12 perf 验收时再评估）。
        import aiosqlite
        processed = 0
        async with aiosqlite.connect(self._session_db._db_path) as db:
            sql = "SELECT id, content FROM messages WHERE embedding IS NULL"
            params: tuple = ()
            if limit is not None and limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()

        if not rows:
            return 0

        batch: list[tuple[int, str]] = []
        for msg_id, content in rows:
            if not content:
                continue
            batch.append((int(msg_id), str(content)))
            if len(batch) >= self._batch_size:
                await self._flush(batch)
                processed += len(batch)
                batch = []
                # 让出 loop —— 给 enqueue 的新消息优先处理机会
                await asyncio.sleep(0)
        if batch:
            await self._flush(batch)
            processed += len(batch)
        return processed

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """后台 batching main loop。batch 满或 flush_interval 到期都触发 flush。"""
        pending: list[tuple[int, str]] = []
        last_flush = time.monotonic()

        while not self._stop_event.is_set():
            # 计算还需等多久触发 interval flush。如果 pending 空，就
            # 阻塞等第一条到来；有 pending 就计算剩余窗口。
            if not pending:
                timeout: float | None = None
            else:
                elapsed = time.monotonic() - last_flush
                timeout = max(0.0, self._flush_interval_s - elapsed)

            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=timeout
                )
                pending.append(item)
                self._queue.task_done()
            except asyncio.TimeoutError:
                # interval 到 —— flush 当前 pending（若有）
                if pending:
                    await self._flush(pending)
                    pending = []
                    last_flush = time.monotonic()
                continue
            except asyncio.CancelledError:
                # stop(drain=False) 会走到这里
                break

            # batch 满 → 立即 flush
            if len(pending) >= self._batch_size:
                await self._flush(pending)
                pending = []
                last_flush = time.monotonic()

        # 退出前把 pending 里的最后一批也刷出去（drain 场景）
        if pending:
            await self._flush(pending)

    async def _flush(self, batch: list[tuple[int, str]]) -> None:
        """对一个 batch 跑 embedder + 写 messages_vec + messages.embedding。

        任何失败只 log + 累计 failed 计数，不抛——保持"失败隔离"契约。
        """
        if not batch:
            return
        msg_ids = [mid for mid, _ in batch]
        texts = [t for _, t in batch]
        try:
            vecs = await self._embedder.encode(texts)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "embedder.encode failed for %d messages (%s); skipping batch",
                len(batch),
                exc,
            )
            self._failed += len(batch)
            return

        if vecs.shape != (len(batch), EMBEDDING_DIM):
            log.error(
                "embedder returned unexpected shape %s (expected (%d, %d)); "
                "skipping batch",
                vecs.shape,
                len(batch),
                EMBEDDING_DIM,
            )
            self._failed += len(batch)
            return

        try:
            await self._write_rows(msg_ids, vecs)
            self._written += len(batch)
            self._last_flush_ts = time.time()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "vector_worker write failed for %d messages (%s)",
                len(batch),
                exc,
            )
            self._failed += len(batch)

    async def _write_rows(self, msg_ids: list[int], vecs: np.ndarray) -> None:
        """写入两张表：messages_vec (虚拟) + messages.embedding (BLOB)。

        两者同一 sqlite connection 里同 transaction 完成，保证要么都成要么都败。
        同步 sqlite3 连接（vec0 需要 load_extension，aiosqlite 不直接支持），
        整个过程用 run_in_executor 放到线程池里，不阻塞 loop。
        """
        db_path = self._session_db._db_path

        # 把 vecs 转成 bytes 提前做好 —— 在 executor 里拿到的就是 raw bytes，
        # 避免线程间再跑 numpy API。
        payloads = [vec.astype(np.float32).tobytes() for vec in vecs]

        def _sync_write() -> None:
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                # 尝试 load vec 扩展（即使 session_db 已经 _try_init_vec 过，
                # 这里是新 connection，必须重新 load）
                vec_available = False
                if _HAS_SQLITE_VEC:
                    try:
                        conn.enable_load_extension(True)
                        sqlite_vec.load(conn)  # type: ignore[union-attr]
                        conn.enable_load_extension(False)
                        vec_available = True
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "sqlite-vec load in worker failed (%s); "
                            "writing only messages.embedding",
                            exc,
                        )

                try:
                    conn.execute("BEGIN")
                    for msg_id, payload in zip(msg_ids, payloads):
                        if vec_available:
                            # vec0 表对重复 PK 会报错 —— 先删后插保证幂等
                            conn.execute(
                                "DELETE FROM messages_vec WHERE message_id = ?",
                                (msg_id,),
                            )
                            conn.execute(
                                "INSERT INTO messages_vec(message_id, embedding) "
                                "VALUES (?, ?)",
                                (msg_id, payload),
                            )
                        conn.execute(
                            "UPDATE messages SET embedding = ? WHERE id = ?",
                            (payload, msg_id),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            finally:
                conn.close()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _sync_write)

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Snapshot 监控数据。调用方 poll 用。"""
        return {
            "queued": self._queue.qsize(),
            "queued_total": self._queued_total,
            "written": self._written,
            "failed": self._failed,
            "dropped": self._dropped,
            "last_flush_ts": self._last_flush_ts,
            "is_running": self._task is not None and not self._task.done(),
        }
