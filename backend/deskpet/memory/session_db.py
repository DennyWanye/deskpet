"""P4-S1 L2 session database — clean-room rewrite.

Attribution:
    Interface style 参考 Hermes AIAgent `SessionStore`（MIT license），但
    代码是 clean-room 重写，不含 Hermes 源码复制。设计依据在
    ``openspec/changes/p4-poseidon-agent-harness/design.md`` §D-ARCH-1 /
    §D-IMPL-2，spec Requirement "Session Database (L2) — SQLite with FTS5"。

Responsibilities:
    * 拉起 state.db（通过 ``schema.initialize_state_db`` 做迁移 + .bak 回滚）
    * WAL 模式 + 应用层 SQLITE_BUSY 重试（jitter exponential backoff，≤5 次）
    * 暴露 create_session / append_message / get_messages / search_fts /
      update_salience / close 六个 async API
    * 可选 sqlite-vec 虚拟表 ``messages_vec``（load_extension 失败 → warn + 降级）

Not here（留给后续 slice）：
    * L3 向量召回本体 → P4-S3 retriever.py
    * embedding 计算 → P4-S2 embedder.py
    * 文件记忆 / MemoryManager → P4-S4
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.memory.schema import initialize_state_db

log = logging.getLogger(__name__)


# SQLITE_BUSY retry 参数（3.3 要求）
_MAX_RETRIES = 5
_BASE_DELAY_MS = 100
_JITTER_MS = 50
_MAX_DELAY_MS = 2000

# sqlite-vec 虚拟表 SQL —— 留在 Python 侧而非 migration SQL 里，
# 因为 load_extension 是 connection-scoped，不好在纯 .sql 里表达。
_MESSAGES_VEC_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_vec USING vec0("
    "message_id INTEGER PRIMARY KEY, "
    "embedding FLOAT[1024] distance_metric=cosine"
    ")"
)


def _is_busy_error(exc: BaseException) -> bool:
    """判定某个异常是否为 SQLITE_BUSY / database is locked —— 值得重试。"""
    if not isinstance(exc, (sqlite3.OperationalError, aiosqlite.OperationalError)):
        return False
    msg = str(exc).lower()
    return "database is locked" in msg or "busy" in msg


def _backoff_delay_ms(attempt: int) -> float:
    """指数退避 + jitter（上限 _MAX_DELAY_MS ms）。

    attempt 从 0 开始：0 → ~100ms，1 → ~200ms，2 → ~400ms ... jitter 0..50ms。
    """
    base = min(_BASE_DELAY_MS * (2**attempt), _MAX_DELAY_MS)
    jitter = random.uniform(0, _JITTER_MS)
    return min(base + jitter, _MAX_DELAY_MS)


class SessionDB:
    """L2 会话存储 —— aiosqlite + WAL + FTS5（+ 可选 sqlite-vec）."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._initialized = False
        self._vec_enabled = False
        # 写锁：WAL 允许并发读，但应用层保证自己的写是串行化的更稳
        # （避免 aiosqlite 同 connection 被多 task 抢）
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """启动初始化：迁移 → WAL → 尝试建 messages_vec。幂等。"""
        if self._initialized:
            return
        # 1. 让 schema.initialize_state_db 做迁移 + 备份 + 回滚
        await initialize_state_db(self._db_path)

        # 2. WAL 模式（3.3 要求：第一件事）
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            # 让并发写等 5s 再报 SQLITE_BUSY（SQLite 层第一道防线，
            # 应用层 retry 是第二道）
            await db.execute("PRAGMA busy_timeout=5000")
            # synchronous=NORMAL 在 WAL 下是常规选择：崩溃最多丢最后一个事务
            # 而非破坏数据库。full 对单用户桌宠过于保守。
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.commit()

        # 3. 尝试加载 sqlite-vec 扩展并建 messages_vec 虚拟表
        #    spec 明确允许"降级启动"：失败只 warn，不抛。
        self._vec_enabled = await self._try_init_vec()

        self._initialized = True
        log.info(
            "SessionDB ready (db=%s, vec=%s)",
            self._db_path,
            "on" if self._vec_enabled else "off (degraded)",
        )

    async def _try_init_vec(self) -> bool:
        """加载 sqlite-vec + 建 messages_vec 虚拟表。失败返回 False。

        两步：
          1. `import sqlite_vec` 不可用 → 返回 False（最常见降级场景）
          2. 打开同步 sqlite3 connection，``enable_load_extension(True)`` +
             ``sqlite_vec.load(conn)`` + 建 ``messages_vec`` 虚拟表。同步
             路径是因为 sqlite_vec.load 要的是原生 sqlite3.Connection
             而非 aiosqlite 包装层；aiosqlite 自己也是通过 run_in_executor
             调同步 API，这里直接 run_in_executor 省了一层抽象。

        任何步骤失败都返回 False + log warning，**不抛**。spec 要求
        sqlite-vec 不可用时降级启动，L1+L2 继续工作。
        """
        try:
            import sqlite_vec  # type: ignore
        except ImportError:
            log.warning(
                "sqlite-vec not installed; L3 vector search disabled "
                "(pip install sqlite-vec to enable)"
            )
            return False

        def _sync_init() -> None:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                conn.execute(_MESSAGES_VEC_DDL)
                conn.commit()
            finally:
                conn.close()

        try:
            await asyncio.get_running_loop().run_in_executor(None, _sync_init)
            return True
        except Exception as exc:  # noqa: BLE001
            # 包括：enable_load_extension 被禁用（极少）、DLL 缺失、vec0
            # 不被识别等。一律降级。
            log.warning(
                "sqlite-vec init failed (%s); L3 disabled, L1+L2 still work",
                exc,
            )
            return False

    async def close(self) -> None:
        """目前每次调用都是 short-lived connection，无持久 conn 可关。

        保留接口以便未来切 connection pool 时签名不变。
        """
        self._initialized = False

    # ------------------------------------------------------------------
    # Write path with retry
    # ------------------------------------------------------------------

    async def _with_retry(self, coro_factory):
        """通用重试包装：对 SQLITE_BUSY 最多重试 _MAX_RETRIES 次。

        ``coro_factory`` 是一个 async 零参 callable，每次 retry 会重新调用
        （不能传已 awaited 的协程，那种不可复用）。
        """
        last_exc: BaseException | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await coro_factory()
            except Exception as exc:  # noqa: BLE001
                if not _is_busy_error(exc) or attempt == _MAX_RETRIES:
                    raise
                last_exc = exc
                delay = _backoff_delay_ms(attempt)
                log.debug(
                    "SQLITE_BUSY attempt=%d delay=%.0fms err=%s",
                    attempt,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay / 1000.0)
        # unreachable：循环要么 return 要么 raise
        raise RuntimeError("retry loop exited unexpectedly") from last_exc

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        """新建会话，返回 UUID 字符串。"""
        if not self._initialized:
            await self.initialize()
        session_id = str(uuid.uuid4())
        meta_json = json.dumps(metadata) if metadata else None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "INSERT INTO sessions(id, created_at, metadata) "
                        "VALUES (?, ?, ?)",
                        (session_id, time.time(), meta_json),
                    )
                    await db.commit()

        await self._with_retry(_do)
        return session_id

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> int:
        """写入一条 message。

        FTS5 同步通过 migration 里的 trigger 自动完成，无需额外 insert。
        返回新 message 的 id（lastrowid）。
        """
        if not self._initialized:
            await self.initialize()

        tool_calls_json = json.dumps(tool_calls) if tool_calls else None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "INSERT INTO messages("
                        "session_id, role, content, created_at, "
                        "tool_call_id, tool_calls"
                        ") VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            session_id,
                            role,
                            content,
                            time.time(),
                            tool_call_id,
                            tool_calls_json,
                        ),
                    )
                    msg_id = cursor.lastrowid
                    await cursor.close()
                    await db.commit()
                    return int(msg_id or 0)

        return await self._with_retry(_do)

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """按 created_at ASC 返回 session 的消息。limit + offset 分页。"""
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, session_id, role, content, created_at, "
                "salience, decay_last_touch, user_emotion, audio_file_path, "
                "tool_call_id, tool_calls "
                "FROM messages WHERE session_id = ? "
                "ORDER BY created_at ASC, id ASC "
                "LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        return [_row_to_dict(r) for r in rows]

    async def search_fts(
        self,
        query: str,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """FTS5 MATCH 查询；结果按 rank 升序（越小越相关）。

        query 按 FTS5 语法传入（空格 AND、``OR``、短语用双引号）。
        """
        if not self._initialized:
            await self.initialize()

        # rank 是 FTS5 内建列，ORDER BY rank 即按相关性升序
        if session_id:
            sql = (
                "SELECT m.id, m.session_id, m.role, m.content, m.created_at, "
                "m.salience, m.decay_last_touch, m.user_emotion, "
                "m.audio_file_path, m.tool_call_id, m.tool_calls, "
                "messages_fts.rank AS rank "
                "FROM messages_fts "
                "JOIN messages m ON m.id = messages_fts.rowid "
                "WHERE messages_fts MATCH ? AND m.session_id = ? "
                "ORDER BY rank LIMIT ?"
            )
            params: tuple = (query, session_id, limit)
        else:
            sql = (
                "SELECT m.id, m.session_id, m.role, m.content, m.created_at, "
                "m.salience, m.decay_last_touch, m.user_emotion, "
                "m.audio_file_path, m.tool_call_id, m.tool_calls, "
                "messages_fts.rank AS rank "
                "FROM messages_fts "
                "JOIN messages m ON m.id = messages_fts.rowid "
                "WHERE messages_fts MATCH ? "
                "ORDER BY rank LIMIT ?"
            )
            params = (query, limit)

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()

        return [_row_to_dict(r, with_rank=True) for r in rows]

    async def update_salience(
        self,
        message_id: int,
        new_salience: float,
        touch: bool = True,
    ) -> None:
        """更新一条 message 的 salience（可选同时更新 decay_last_touch）.

        P4-S3 recall feedback 会以 +0.05 boost 调这个接口。
        """
        if not self._initialized:
            await self.initialize()

        now = time.time() if touch else None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    if touch:
                        await db.execute(
                            "UPDATE messages SET salience=?, decay_last_touch=? "
                            "WHERE id=?",
                            (new_salience, now, message_id),
                        )
                    else:
                        await db.execute(
                            "UPDATE messages SET salience=? WHERE id=?",
                            (new_salience, message_id),
                        )
                    await db.commit()

        await self._with_retry(_do)


# ----------------------------------------------------------------------
# Row mapping helpers
# ----------------------------------------------------------------------

_BASE_COLUMNS = (
    "id",
    "session_id",
    "role",
    "content",
    "created_at",
    "salience",
    "decay_last_touch",
    "user_emotion",
    "audio_file_path",
    "tool_call_id",
    "tool_calls",
)


def _row_to_dict(row: tuple, with_rank: bool = False) -> dict[str, Any]:
    """把 SELECT row 转成前端友好的 dict，tool_calls 反序列化成 list。"""
    d: dict[str, Any] = {k: row[i] for i, k in enumerate(_BASE_COLUMNS)}
    tc = d.get("tool_calls")
    if tc:
        try:
            d["tool_calls"] = json.loads(tc)
        except json.JSONDecodeError:
            # 保留原字符串，避免吞掉调试信号
            pass
    if with_rank and len(row) > len(_BASE_COLUMNS):
        d["rank"] = row[len(_BASE_COLUMNS)]
    return d
