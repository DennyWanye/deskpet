# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S20-D: 自动总结 + 归档老对话。

设计思路：
  1. 扫 ``messages`` 表，按 ``session_id`` 分组，找出
     满足"全部消息 ``created_at < cutoff_ts``"且条数 >=
     ``min_messages`` 的 session（**已归档的 session 不再处理**）。
  2. 对每个候选 session：
     a. 把所有原文按时间顺序拼成一段 prompt
     b. 调 LLM (chat_with_tools 接口) 让它总结成 1-3 句话
     c. **事务**：
         - INSERT 一条 summary message (role="system",
           is_summary=1, summary_of=[原 id 列表 JSON])
         - 把所有原文 INSERT 进 messages_archive（带 archived_at +
           archived_into_id=summary_id）
         - 从 messages 表 DELETE 原文
     d. （可选）入向量库 — 上层 vector_worker.enqueue 即可

Safety：
  * **事务**：原文搬走 + summary 入库要么全成要么全失败 — 不会出现
    "原文删了 summary 没建"。
  * **可恢复**：messages_archive 永久保留原文，IPC 可查。
  * **幂等**：is_summary=1 的 message 永远不会被再次总结（其 created_at
    用归档时刻而非原对话时刻，自然在 cutoff 之外）。
  * **失败兜底**：LLM 调用挂了 → 跳过这个 session，整体不挂。

不做的事：
  * 自动调度 (cron) — 启动时跑一次 + IPC 手动触发即可
  * Summary 编辑 — 用户可通过 memory_delete IPC 删了再生成
  * archive 恢复 UI — 后续 iteration
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite

log = logging.getLogger(__name__)


# 默认参数 — 调用方可覆盖
DEFAULT_AGE_DAYS = 30        # 多久之前的对话才考虑总结
DEFAULT_MIN_MESSAGES = 20    # 一个 session 至少有这么多条才值得总结
DEFAULT_MAX_PER_RUN = 10     # 一次最多处理几个 session（防止单次卡太久）
DEFAULT_MAX_INPUT_CHARS = 8000  # LLM 输入的字符上限（截断）
DEFAULT_SUMMARY_MAX_TOKENS = 512  # gemma4:e4b 等小模型常需要 reasoning tokens

# 总结提示词 — 让 LLM 把 N 条对话压缩成 1-3 句关键事实
# 注：用 English instructions + 在指令里要求 Chinese output。这样 prompt
# 对小模型 (如 gemma4:e4b) 也能稳定触发；输出语种由 conversation 自身
# 内容决定（中文聊天 → 中文摘要）。
_SUMMARY_PROMPT = (
    "Summarize the following conversation history into 1-3 short sentences, "
    "preserving: user preferences/facts, important decisions, "
    "any key info that affects future conversations. "
    "Ignore: small talk, exclamations, emojis, info already overridden later. "
    "Output ONLY the summary text in the same language as the conversation. "
    "No prefix like 'Summary:'. "
    "If the conversation is in Chinese, output Chinese.\n\n"
    "Conversation:\n{conversation}\n\n"
    "Summary:"
)


@dataclass
class SummaryResult:
    """一次 summarize_old_sessions 的结果。"""

    sessions_scanned: int = 0
    sessions_summarized: int = 0
    messages_archived: int = 0
    summaries_created: list[int] = None  # type: ignore[assignment]
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.summaries_created is None:
            self.summaries_created = []
        if self.errors is None:
            self.errors = []


# Provider protocol — 接受任何带 chat_with_tools(messages, ...) 的对象。
LLMCall = Callable[[list[dict[str, str]]], Awaitable[dict[str, Any]]]


async def summarize_old_sessions(
    db_path: str | Path,
    llm_call: LLMCall,
    *,
    age_days: float = DEFAULT_AGE_DAYS,
    min_messages: int = DEFAULT_MIN_MESSAGES,
    max_per_run: int = DEFAULT_MAX_PER_RUN,
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
    now: float | None = None,
    vector_worker: Any = None,
    # Stage 2 / WI-S2.4 — episodic→semantic 固化通路
    fact_extractor: Any = None,
    episodic_to_semantic: bool = False,
    background_tasks: set[asyncio.Task] | None = None,
) -> SummaryResult:
    """主入口。返回 SummaryResult 报告本次成果。

    Parameters
    ----------
    vector_worker:
        Optional VectorWorker instance. When provided, each newly
        created summary message is enqueued for embedding so it
        participates in vector recall (otherwise summaries are in
        ``messages`` but not in ``messages_vec`` — recall miss).
    db_path:
        state.db 绝对路径。
    llm_call:
        async (messages: list[{role,content}]) -> {"content": str, ...}
        包装一下 OpenAICompatibleProvider.chat_with_tools 即可。
    age_days:
        N 天之前的对话才考虑总结。
    min_messages:
        一个 session 至少有这么多条原文才值得总结。
    max_per_run:
        一次调用最多处理 K 个 session — 防止首次运行（4881 条历史）
        把 LLM 打爆。
    max_input_chars:
        塞进 LLM 的 conversation 文本截断阈值。超长时取头部 + 尾部。
    """
    db_path = Path(db_path)
    now_ts = now if now is not None else time.time()
    cutoff_ts = now_ts - age_days * 86400.0

    result = SummaryResult()

    candidate_sessions = await _find_candidate_sessions(
        db_path=db_path,
        cutoff_ts=cutoff_ts,
        min_messages=min_messages,
        max_count=max_per_run,
    )
    result.sessions_scanned = len(candidate_sessions)
    if not candidate_sessions:
        return result

    log.info(
        "summarizer: %d candidate session(s) (cutoff=%.0fd ago, min=%d)",
        len(candidate_sessions), age_days, min_messages,
    )

    for session_id in candidate_sessions:
        try:
            n_archived, summary_id = await _summarize_one_session(
                db_path=db_path,
                session_id=session_id,
                llm_call=llm_call,
                cutoff_ts=cutoff_ts,
                max_input_chars=max_input_chars,
                now_ts=now_ts,
            )
            if summary_id is not None:
                result.sessions_summarized += 1
                result.messages_archived += n_archived
                result.summaries_created.append(summary_id)
                log.info(
                    "summarizer: session=%s -> summary id=%d, archived %d msgs",
                    session_id, summary_id, n_archived,
                )
                # Vectorize the new summary so it participates in recall.
                # Without this, summaries are invisible to BGE-M3 retrieval —
                # users would lose long-term memory after archive.
                if vector_worker is not None:
                    try:
                        # We need the summary's content to enqueue. Cheapest
                        # path: re-read it (we just inserted it).
                        summary_text = await _read_summary_content(db_path, summary_id)
                        if summary_text:
                            await vector_worker.enqueue(summary_id, summary_text)
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "summarizer: vec enqueue failed for summary %d: %s",
                            summary_id, exc,
                        )

                # Stage 2 / WI-S2.4 (D12 v2) — episodic 固化：异步抽 facts。
                # 必须用 background_tasks set 收集 + add_done_callback discard，
                # 不能裸 asyncio.create_task（Python 3.11+ 静默 GC 吞 task，
                # D-RISK-4）。
                if (
                    episodic_to_semantic
                    and fact_extractor is not None
                ):
                    try:
                        summary_text = await _read_summary_content(
                            db_path, summary_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "summarizer: episodic read content failed "
                            "for summary %d: %s", summary_id, exc,
                        )
                        summary_text = None
                    if summary_text:
                        task = asyncio.create_task(
                            _safe_episodic_extract(
                                fact_extractor, summary_id, summary_text,
                            ),
                        )
                        if background_tasks is not None:
                            background_tasks.add(task)
                            task.add_done_callback(background_tasks.discard)
        except Exception as exc:  # noqa: BLE001 — single-session failure not fatal
            log.warning(
                "summarizer: session=%s failed: %s",
                session_id, exc,
            )
            result.errors.append(f"{session_id}: {exc}")

    return result


# ---------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------


async def _safe_episodic_extract(
    fact_extractor: Any, summary_id: int, summary_text: str,
) -> None:
    """Stage 2 / WI-S2.4：在 background task 里安全调 fact_extractor。

    任何异常都吞掉 + warn。这里不能 raise —— 它跑在 background_tasks set
    里，未捕获异常会污染 asyncio 默认日志而不影响主路径，但仍要显式收。
    """
    try:
        persisted = await fact_extractor.process_message(
            message_id=summary_id,
            content=summary_text,
            role="system",
            source="summarizer",
        )
        log.info(
            "episodic_to_semantic: summary=%d → %d facts persisted",
            summary_id, len(persisted),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "episodic_to_semantic: extract failed for summary=%d: %s",
            summary_id, exc,
        )


async def _read_summary_content(db_path: Path, summary_id: int) -> str | None:
    """Read back a summary message's content (for vector enqueue)."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT content FROM messages WHERE id = ?", (summary_id,),
        )
        row = await cur.fetchone()
        await cur.close()
    return row[0] if row else None


async def _find_candidate_sessions(
    db_path: Path,
    cutoff_ts: float,
    min_messages: int,
    max_count: int,
) -> list[str]:
    """找出满足条件的 session_id 列表：
    - 至少 min_messages 条**非 summary** 的原文
    - 全部原文 created_at < cutoff_ts （即整个 session 都 cold 了）
    - 排除已经存在 summary 的 session（避免重复总结）

    返回按"最老 session 优先"排序的 ID 列表，最多 max_count 个。
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT session_id,
                   COUNT(*) AS n_msgs,
                   MAX(created_at) AS latest_ts,
                   SUM(CASE WHEN is_summary = 1 THEN 1 ELSE 0 END) AS n_summaries
            FROM messages
            GROUP BY session_id
            HAVING n_msgs >= ?
              AND latest_ts < ?
              AND n_summaries = 0
            ORDER BY latest_ts ASC
            LIMIT ?
            """,
            (min_messages, cutoff_ts, max_count),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return [str(r[0]) for r in rows]


async def _summarize_one_session(
    db_path: Path,
    session_id: str,
    llm_call: LLMCall,
    cutoff_ts: float,
    max_input_chars: int,
    now_ts: float,
) -> tuple[int, int | None]:
    """对单个 session 做：拉原文 → 调 LLM → 写 archive + summary（事务）。

    Returns:
        (n_archived, summary_message_id)
        summary_message_id 为 None 表示 LLM 返回空摘要，未生成 summary。
    """
    # 1. 拉所有原文
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE session_id = ?
              AND COALESCE(is_summary, 0) = 0
              AND created_at < ?
            ORDER BY created_at ASC, id ASC
            """,
            (session_id, cutoff_ts),
        )
        rows = await cursor.fetchall()
        await cursor.close()

    if not rows:
        return 0, None

    msg_ids: list[int] = [int(r[0]) for r in rows]

    # 2. 拼 conversation 文本（截断超长）
    convo_parts = []
    for _id, role, content, _ts in rows:
        # 防止巨大 tool result 或代码 block 把 prompt 撑爆
        snippet = content if len(content) <= 800 else content[:400] + "...[truncated]..." + content[-400:]
        convo_parts.append(f"[{role}] {snippet}")
    convo = "\n".join(convo_parts)
    if len(convo) > max_input_chars:
        # 头 60% + 尾 40%
        head_len = int(max_input_chars * 0.6)
        tail_len = max_input_chars - head_len
        convo = (
            convo[:head_len]
            + f"\n\n...[省略中段 {len(convo) - max_input_chars} 字]...\n\n"
            + convo[-tail_len:]
        )

    # 3. 调 LLM
    prompt = _SUMMARY_PROMPT.format(conversation=convo)
    llm_resp = await llm_call(
        [
            {"role": "user", "content": prompt},
        ]
    )
    summary_text = (llm_resp.get("content") or "").strip()
    if not summary_text:
        log.warning("summarizer: empty summary from LLM for session=%s", session_id)
        return 0, None

    # 4. 写事务：archive 原文 + insert summary + delete 原文 from messages_vec
    summary_id = await _commit_summary_txn(
        db_path=db_path,
        session_id=session_id,
        msg_ids=msg_ids,
        summary_text=summary_text,
        now_ts=now_ts,
    )
    return len(msg_ids), summary_id


async def _commit_summary_txn(
    db_path: Path,
    session_id: str,
    msg_ids: list[int],
    summary_text: str,
    now_ts: float,
) -> int:
    """事务：拷原文进 archive + insert summary + 从 messages 删原文。

    使用同步 sqlite3 — sqlite_vec.load 需要原生连接 + sqlite-vec 的
    DELETE FROM messages_vec WHERE message_id IN (...) 也需要它。

    Returns:
        新 summary 的 message_id
    """

    def _sync_commit() -> int:
        import sqlite3
        try:
            import sqlite_vec  # type: ignore[import-untyped]
        except ImportError:
            sqlite_vec = None  # vec 不可用时跳过 vec 删除

        conn = sqlite3.connect(str(db_path))
        try:
            if sqlite_vec is not None:
                conn.enable_load_extension(True)
                try:
                    sqlite_vec.load(conn)
                finally:
                    conn.enable_load_extension(False)

            conn.execute("BEGIN")
            try:
                # a. 把原文复制到 messages_archive
                placeholders = ",".join("?" * len(msg_ids))
                conn.execute(
                    f"""
                    INSERT INTO messages_archive (
                        id, session_id, role, content, created_at,
                        embedding, salience, decay_last_touch,
                        user_emotion, audio_file_path,
                        tool_call_id, tool_calls,
                        archived_at, archived_into_id
                    )
                    SELECT
                        id, session_id, role, content, created_at,
                        embedding, salience, decay_last_touch,
                        user_emotion, audio_file_path,
                        tool_call_id, tool_calls,
                        ?, NULL
                    FROM messages
                    WHERE id IN ({placeholders})
                    """,
                    [now_ts] + msg_ids,
                )

                # b. INSERT summary message — 单独 SQL 拿 lastrowid
                cur = conn.execute(
                    """
                    INSERT INTO messages
                        (session_id, role, content, created_at,
                         salience, decay_last_touch,
                         is_summary, summary_of)
                    VALUES (?, 'system', ?, ?, 0.8, ?, 1, ?)
                    """,
                    (
                        session_id,
                        f"[summary of {len(msg_ids)} archived messages] {summary_text}",
                        now_ts,
                        now_ts,
                        json.dumps(msg_ids),
                    ),
                )
                summary_id = int(cur.lastrowid or 0)

                # c. 把原文从 messages 主表删 (FTS5 trigger 会自动同步)
                conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    msg_ids,
                )

                # d. 把原文从 messages_vec 删（向量召回不再命中老原文）
                if sqlite_vec is not None:
                    try:
                        conn.execute(
                            f"DELETE FROM messages_vec "
                            f"WHERE message_id IN ({placeholders})",
                            msg_ids,
                        )
                    except sqlite3.OperationalError:
                        # vec 不可用就算了 - 主表已经没原文了，召回不到
                        pass

                # e. 更新 archive 行的 archived_into_id
                conn.execute(
                    f"""
                    UPDATE messages_archive
                    SET archived_into_id = ?
                    WHERE id IN ({placeholders})
                    """,
                    [summary_id] + msg_ids,
                )

                conn.commit()
                return summary_id
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    return await asyncio.get_running_loop().run_in_executor(None, _sync_commit)


# ---------------------------------------------------------------------
# Helper: build llm_call from OpenAICompatibleProvider
# ---------------------------------------------------------------------


def make_llm_call(provider, *, max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS) -> LLMCall:
    """Adapt OpenAICompatibleProvider into the LLMCall protocol."""
    async def _call(messages: list[dict[str, str]]) -> dict[str, Any]:
        return await provider.chat_with_tools(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,  # 总结任务要稳定，不要发散
        )
    return _call
