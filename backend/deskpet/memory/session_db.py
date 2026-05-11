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

from typing import Awaitable, Callable, Optional

import aiosqlite

from deskpet.memory.schema import initialize_state_db

log = logging.getLogger(__name__)

# P4-S2 hook 类型：(message_id, content) → awaitable None。
# MemoryManager / VectorWorker 在此接入 "消息落盘后异步跑 embedding"。
OnMessageWritten = Callable[[int, str], Awaitable[None]]


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

    def __init__(
        self,
        db_path: str | Path,
        *,
        on_message_written: Optional[OnMessageWritten] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._initialized = False
        self._vec_enabled = False
        # 写锁：WAL 允许并发读，但应用层保证自己的写是串行化的更稳
        # （避免 aiosqlite 同 connection 被多 task 抢）
        self._write_lock = asyncio.Lock()
        # P4-S2: append_message 落盘后的异步回调钩子。典型使用场景是
        # VectorWorker.enqueue —— 把新消息推进 embedding queue。
        # 设计约束（严格）：
        #   * 失败只 log，**不** re-raise 给 append_message 的调用方
        #   * 不得修改 append_message 的返回值（仍是 msg_id）
        #   * None → 老 S1 行为完全不变（零开销）
        self._on_message_written: Optional[OnMessageWritten] = on_message_written

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
        reasoning_content: str | None = None,
    ) -> int:
        """写入一条 message。

        FTS5 同步通过 migration 里的 trigger 自动完成，无需额外 insert。
        返回新 message 的 id（lastrowid）。

        ``reasoning_content`` (P4-S24): 思考模式 LLM 的 chain-of-thought
        原文。仅 role='assistant' 行需要；为 None / 空字符串时落 NULL。
        多轮对话时会被读出来塞回 LLM payload，否则 DeepSeek V4 Pro
        / Qwen3 thinking 之类会以 HTTP 400 拒绝下一轮请求。
        """
        if not self._initialized:
            await self.initialize()

        tool_calls_json = json.dumps(tool_calls) if tool_calls else None
        reasoning = reasoning_content if reasoning_content else None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "INSERT INTO messages("
                        "session_id, role, content, created_at, "
                        "tool_call_id, tool_calls, reasoning_content"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            session_id,
                            role,
                            content,
                            time.time(),
                            tool_call_id,
                            tool_calls_json,
                            reasoning,
                        ),
                    )
                    msg_id = cursor.lastrowid
                    await cursor.close()
                    await db.commit()
                    return int(msg_id or 0)

        msg_id = await self._with_retry(_do)

        # P4-S2 hook：消息已落盘，异步通知订阅者（典型：VectorWorker）。
        # 契约：
        #   * hook 在写锁**之外**触发——避免 hook 阻塞主写路径
        #   * hook 抛异常只 log warn，不影响返回值
        if self._on_message_written is not None:
            try:
                await self._on_message_written(msg_id, content)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "on_message_written hook failed for msg_id=%s: %s",
                    msg_id,
                    exc,
                )

        return msg_id

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
                "tool_call_id, tool_calls, reasoning_content "
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
                "m.reasoning_content, "
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
                "m.reasoning_content, "
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

    # ---- P4-S17 MemoryStore Protocol compatibility --------------------

    async def get_recent(
        self, session_id: str, limit: int = 10
    ) -> list["ConversationTurn"]:
        """Implement ``MemoryStore.get_recent`` via ``get_messages``."""
        from memory.base import ConversationTurn

        rows = await self.get_messages(session_id, limit=limit)
        return [
            ConversationTurn(
                role=str(row.get("role", "")),
                content=str(row.get("content", "")),
                created_at=float(row.get("created_at") or 0.0),
            )
            for row in rows
        ]

    async def append(self, session_id: str, role: str, content: str) -> None:
        """Implement ``MemoryStore.append`` via ``append_message``."""
        await self.append_message(session_id=session_id, role=role, content=content)

    async def clear(self, session_id: str) -> None:
        """Implement ``MemoryStore.clear`` for one session."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "DELETE FROM messages WHERE session_id = ?",
                        (session_id,),
                    )
                    await db.commit()

        await self._with_retry(_do)

    # ---- S14 admin surface --------------------------------------------

    async def list_turns(
        self,
        session_id: str | None = None,
        limit: int | None = None,
    ) -> list["StoredTurn"]:
        """List messages for one session, or all sessions, as StoredTurn."""
        from memory.base import StoredTurn

        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            if session_id is None:
                sql = (
                    "SELECT id, session_id, role, content, created_at "
                    "FROM messages ORDER BY created_at ASC, id ASC"
                )
                params: tuple = ()
            else:
                sql = (
                    "SELECT id, session_id, role, content, created_at "
                    "FROM messages WHERE session_id = ? "
                    "ORDER BY created_at ASC, id ASC"
                )
                params = (session_id,)
            if limit is not None and limit > 0:
                sql += " LIMIT ?"
                params = (*params, int(limit))
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            StoredTurn(
                id=int(row[0]),
                session_id=str(row[1]),
                role=str(row[2]),
                content=str(row[3]),
                created_at=float(row[4] or 0.0),
            )
            for row in rows
        ]

    async def delete_turn(self, turn_id: int) -> bool:
        """Delete one message row and return whether a row was removed."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM messages WHERE id = ?",
                        (int(turn_id),),
                    )
                    await db.commit()
                    removed = cursor.rowcount or 0
                    await cursor.close()
                    try:
                        await db.execute(
                            "DELETE FROM messages_vec WHERE message_id = ?",
                            (int(turn_id),),
                        )
                        await db.commit()
                    except Exception:
                        pass
                    return removed

        return (await self._with_retry(_do)) > 0

    async def list_sessions(self) -> list["SessionSummary"]:
        """List all sessions that have messages, newest first."""
        from memory.base import SessionSummary

        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT session_id, COUNT(*), MAX(created_at) "
                "FROM messages GROUP BY session_id ORDER BY MAX(created_at) DESC"
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            SessionSummary(
                session_id=str(row[0]),
                turn_count=int(row[1] or 0),
                last_message_at=float(row[2] or 0.0),
            )
            for row in rows
        ]

    # ---- P5-S2 code_session_provider binding -------------------------

    async def get_code_session_provider_binding(
        self, base_session_id: str
    ) -> dict[str, Any]:
        """读取 code 会话的 provider/model override 绑定.

        返回 ``{"provider_id": str|None, "preferred_model": str|None}``。
        没有 binding 行的 sid 返回 ``{"provider_id": None, "preferred_model": None}``
        ——上层据此知道"走全局 chain"。

        Spec: code-session-provider-binding → Requirement "Resolution algorithm"
        步骤 1（读 SessionDB）.
        """
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT provider_id, preferred_model "
                "FROM code_session_provider WHERE base_session_id = ?",
                (base_session_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()

        if row is None:
            return {"provider_id": None, "preferred_model": None}
        return {"provider_id": row[0], "preferred_model": row[1]}

    async def set_code_session_provider_binding(
        self,
        base_session_id: str,
        provider_id: str | None,
        preferred_model: str | None,
    ) -> None:
        """写入/更新/清除 code 会话的 provider/model override 绑定.

        语义：
          * 任一字段非 None → upsert 一行
          * 两字段都 None → 删除该 sid 的 binding 行（如果有）

        Spec: code-session-provider-binding → Scenarios
          "Set provider override creates row"
          "Set preferred_model without provider keeps chain"
          "Clear override (set provider_id to null) restores global chain"
        """
        if not self._initialized:
            await self.initialize()

        clearing = provider_id is None and preferred_model is None

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    if clearing:
                        await db.execute(
                            "DELETE FROM code_session_provider "
                            "WHERE base_session_id = ?",
                            (base_session_id,),
                        )
                    else:
                        # SQLite UPSERT —— ON CONFLICT(PK) DO UPDATE
                        await db.execute(
                            "INSERT INTO code_session_provider "
                            "(base_session_id, provider_id, preferred_model, updated_at) "
                            "VALUES (?, ?, ?, julianday('now')) "
                            "ON CONFLICT(base_session_id) DO UPDATE SET "
                            "  provider_id = excluded.provider_id, "
                            "  preferred_model = excluded.preferred_model, "
                            "  updated_at = excluded.updated_at",
                            (base_session_id, provider_id, preferred_model),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def clear_bindings_for_provider(self, provider_id: str) -> int:
        """删除所有指向某 provider_id 的 code_session_provider 绑定行.

        P5-S2 Phase 2 minor extension: 当 provider 被 IPC 删除时,孤儿绑定行
        (sid → 已删除的 provider) 需要清理,避免 resolution 拿到一个不存在的
        provider_id。返回删除的行数。

        Spec: frontend-ipc-surface Scenario "remove cleanup" + design.md
        "Resolution algorithm" 步骤 2.
        """
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_session_provider WHERE provider_id = ?",
                        (provider_id,),
                    )
                    await db.commit()
                    removed = cursor.rowcount or 0
                    await cursor.close()
                    return removed

        return await self._with_retry(_do)

    async def clear_all(self) -> int:
        """Delete all messages and return the number of removed rows."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute("DELETE FROM messages")
                    await db.commit()
                    removed = cursor.rowcount or 0
                    await cursor.close()
                    try:
                        await db.execute("DELETE FROM messages_vec")
                        await db.commit()
                    except Exception:
                        pass
                    return removed

        return await self._with_retry(_do)

    # ------------------------------------------------------------------
    # P4-S22 Code Mode — todo list per session
    # ------------------------------------------------------------------

    async def replace_code_todos(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Replace the entire todo list for ``session_id`` atomically.

        ``items`` is a list of dicts shaped like::

            {
                "content": "Implement A",
                "activeForm": "Implementing A",
                "status": "pending" | "in_progress" | "completed",
            }

        Mirrors Claude Code's TodoWrite semantics: the tool is
        idempotent — every call replaces the full list, the LLM doesn't
        track diffs. Sort order is preserved by index in ``items``.
        """
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        "DELETE FROM code_todos WHERE session_id = ?",
                        (session_id,),
                    )
                    for idx, item in enumerate(items):
                        status = item.get("status", "pending")
                        if status not in {"pending", "in_progress", "completed"}:
                            status = "pending"
                        await db.execute(
                            """
                            INSERT INTO code_todos
                              (session_id, content, active_form, status, sort_order)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                session_id,
                                str(item.get("content", ""))[:2000],
                                str(item.get("activeForm", ""))[:2000],
                                status,
                                idx,
                            ),
                        )
                    await db.commit()

        await self._with_retry(_do)

    async def get_code_todos(self, session_id: str) -> list[dict[str, Any]]:
        """Return the current todo list for ``session_id`` in render order."""
        if not self._initialized:
            await self.initialize()

        async def _do():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                cursor = await db.execute(
                    """
                    SELECT content, active_form, status, sort_order
                    FROM code_todos
                    WHERE session_id = ?
                    ORDER BY sort_order
                    """,
                    (session_id,),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "content": row[0],
                        "activeForm": row[1],
                        "status": row[2],
                        "sort_order": row[3],
                    }
                    for row in rows
                ]

        return await self._with_retry(_do)

    async def upsert_code_session(
        self,
        *,
        base_session_id: str,
        code_session_id: str,
        project_root: str,
        project_name: str,
    ) -> None:
        """P4-S25 B4: persist the project enrollment so it survives restart.

        Called from CodeModeManager.enter(). last_active_at refreshes on
        every call so newest projects rise to the top of dashboards.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute(
                        """
                        INSERT INTO code_sessions(
                            base_session_id, code_session_id,
                            project_root, project_name,
                            created_at, last_active_at
                        ) VALUES (?, ?, ?, ?, julianday('now'), julianday('now'))
                        ON CONFLICT(base_session_id) DO UPDATE SET
                            code_session_id = excluded.code_session_id,
                            project_root    = excluded.project_root,
                            project_name    = excluded.project_name,
                            last_active_at  = julianday('now')
                        """,
                        (base_session_id, code_session_id, project_root, project_name),
                    )
                    await db.commit()

        await self._with_retry(_do)

    async def list_code_sessions(self) -> list[dict[str, Any]]:
        """P4-S25 B4: read the persisted project list, newest first.

        Used by CodeModeManager.load_persisted() at startup to repopulate
        the in-memory state map. Order doesn't strictly matter (both
        sidebar and dashboard sort their own way) but newest-first is
        a sensible default if anyone iterates raw.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT base_session_id, code_session_id,
                           project_root, project_name,
                           created_at, last_active_at
                    FROM code_sessions
                    ORDER BY last_active_at DESC
                    """
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "base_session_id": r[0],
                        "code_session_id": r[1],
                        "project_root": r[2],
                        "project_name": r[3],
                        "created_at": r[4],
                        "last_active_at": r[5],
                    }
                    for r in rows
                ]

        return await self._with_retry(_do)

    async def delete_code_session(self, base_session_id: str) -> int:
        """P4-S25 B4: remove a persisted project enrollment.

        Called from the `code_session_delete` IPC handler alongside
        delete_code_todos. We do NOT cascade-delete `messages` rows —
        chat history for that code_session_id stays in the DB so re-
        adding the same project root later resumes the same thread.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_sessions WHERE base_session_id = ?",
                        (base_session_id,),
                    )
                    deleted = cursor.rowcount or 0
                    await cursor.close()
                    await db.commit()
                    return int(deleted)

        return await self._with_retry(_do)

    # ─────────────────────────────────────────────────────────────────
    # P5-S1: supervisor_hints — audit log for the watchdog's interventions.
    # Every nudge / ask_user / cancel_coerced action emitted by the
    # supervisor LLM gets a row here, plus a follow-up ``user_choice``
    # row when the user clicks a bubble button. Used for: debugging
    # ("why did agent suddenly try a different approach?"), Settings UI
    # count badges, and a future cost-estimate feature.
    # ─────────────────────────────────────────────────────────────────

    async def append_supervisor_hint(
        self,
        *,
        session_id: str,
        alert_id: str,
        hint_text: str,
        action: str,
        severity: str,
        diagnosis: str = "",
        user_button: str | None = None,
        ts: int | None = None,
    ) -> int:
        """Insert one supervisor_hints row. Returns the new row id."""
        if not self._initialized:
            await self.initialize()
        import time as _time

        ts_val = int(ts if ts is not None else _time.time())

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        """
                        INSERT INTO supervisor_hints(
                            session_id, alert_id, hint_text, action, severity,
                            diagnosis, user_button, ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            alert_id,
                            hint_text,
                            action,
                            severity,
                            diagnosis,
                            user_button,
                            ts_val,
                        ),
                    )
                    new_id = int(cursor.lastrowid or 0)
                    await cursor.close()
                    await db.commit()
                    return new_id

        return await self._with_retry(_do)

    async def list_supervisor_hints(
        self,
        *,
        session_id: str | None = None,
        since_ts: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read supervisor audit rows newest-first.

        Filter by ``session_id`` to scope to one session; ``since_ts``
        for "today's interventions" style queries; ``limit`` caps result
        set so a long-lived backend doesn't dump 10K rows over IPC.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> list[dict[str, Any]]:
            async with aiosqlite.connect(self._db_path) as db:
                conditions: list[str] = []
                params: list[Any] = []
                if session_id is not None:
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if since_ts is not None:
                    conditions.append("ts >= ?")
                    params.append(int(since_ts))
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                params.append(int(limit))
                cursor = await db.execute(
                    f"""
                    SELECT id, session_id, alert_id, hint_text, action,
                           severity, diagnosis, user_button, ts
                    FROM supervisor_hints
                    {where}
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                return [
                    {
                        "id": r[0],
                        "session_id": r[1],
                        "alert_id": r[2],
                        "hint_text": r[3],
                        "action": r[4],
                        "severity": r[5],
                        "diagnosis": r[6],
                        "user_button": r[7],
                        "ts": r[8],
                    }
                    for r in rows
                ]

        return await self._with_retry(_do)

    async def count_supervisor_hints(
        self,
        *,
        session_id: str | None = None,
        since_ts: int | None = None,
    ) -> int:
        """Count audit rows matching filter (used by Settings UI badge)."""
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with aiosqlite.connect(self._db_path) as db:
                conditions: list[str] = []
                params: list[Any] = []
                if session_id is not None:
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if since_ts is not None:
                    conditions.append("ts >= ?")
                    params.append(int(since_ts))
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM supervisor_hints {where}",
                    tuple(params),
                )
                row = await cursor.fetchone()
                await cursor.close()
                return int(row[0]) if row else 0

        return await self._with_retry(_do)

    async def delete_code_todos(self, session_id: str) -> int:
        """P4-S24 followup: wipe all todos for a code session.

        Used by ``code_session_delete`` IPC when the user removes a
        project from the code panel. Returns the number of rows deleted
        (0 if the session had none).

        We deliberately do NOT touch ``messages`` rows — chat history
        for the project survives so the user can resume the same
        ``code-<sha>`` session later if they re-add the same project
        root. Same philosophy as ``CodeModeManager.exit``.
        """
        if not self._initialized:
            await self.initialize()

        async def _do() -> int:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "DELETE FROM code_todos WHERE session_id = ?",
                        (session_id,),
                    )
                    deleted = cursor.rowcount or 0
                    await cursor.close()
                    await db.commit()
                    return int(deleted)

        return await self._with_retry(_do)


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
    # P4-S24: chain-of-thought from thinking-mode LLMs (DeepSeek V4 Pro,
    # Qwen3 thinking, etc.). NULL for non-thinking models. Round-tripped
    # back into LLM payloads via MemoryComponent so the API doesn't 400.
    "reasoning_content",
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
