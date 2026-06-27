# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""记忆系统升级 WI-M1.5 — messages_chunks 存量 backfill。

chunking 只在新消息写入时触发（``_on_message_written`` fanout），历史长
消息不会被切块 → 召回不一致。本脚本对历史 ``messages`` 批量跑
``MessageChunker.chunk_message``（短消息只产一行，长消息按句切块），并对
每个 chunk embed 进 ``messages_chunks``。

可重入：已有 chunk 行的 message 默认跳过。

要求：backend 不在跑（共享 state.db 写锁会冲突）。

用法::

    cd backend
    python -m scripts.chunk_backfill                 # 全量
    python -m scripts.chunk_backfill --limit 500     # 限量
    python -m scripts.chunk_backfill --db <path>     # 指定 state.db
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass


async def _resolve_state_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    # config / paths 是 backend 根目录顶层模块，不在 deskpet 包下。
    try:
        from config import load_config
        cfg = load_config()
        if cfg.memory.db_path:
            return Path(cfg.memory.db_path).resolve().parent / "state.db"
    except Exception:
        pass
    try:
        import paths  # 认 DESKPET_USER_DATA_DIR
        return paths.user_data_dir() / "data" / "state.db"
    except Exception:
        pass
    import platformdirs
    return Path(
        platformdirs.user_data_dir("deskpet", appauthor=False, roaming=False)
    ) / "data" / "state.db"


async def _already_chunked_ids(db_path: Path) -> set[int]:
    import aiosqlite
    from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

    await ensure_memory_v2_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT DISTINCT message_id FROM messages_chunks"
        )
        rows = await cur.fetchall()
        await cur.close()
    return {int(r[0]) for r in rows}


async def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.chunk_backfill")
    parser.add_argument("--db", default=None, help="state.db 路径")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="最多处理多少条历史消息（0 = 全量）",
    )
    args = parser.parse_args()

    db_path = await _resolve_state_db(args.db)
    if not db_path.exists():
        print(f"state.db not found at {db_path}", file=sys.stderr)
        return 2

    import aiosqlite
    from deskpet.memory.chunker import MessageChunker
    from deskpet.memory.embedder import Embedder

    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    await embedder.warmup()
    chunker = MessageChunker(db_path, embedder=embedder)

    done_ids = await _already_chunked_ids(db_path)
    print(f"[chunk_backfill] {len(done_ids)} 条消息已切块，跳过。")

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT id, content FROM messages "
            "WHERE role IN ('user', 'assistant') ORDER BY id ASC"
        )
        rows = await cur.fetchall()
        await cur.close()

    processed = 0
    chunks_total = 0
    for msg_id, content in rows:
        if int(msg_id) in done_ids:
            continue
        if args.limit and processed >= args.limit:
            break
        try:
            ids = await chunker.chunk_message(
                message_id=int(msg_id), content=str(content or ""),
            )
            chunks_total += len(ids)
        except Exception as exc:  # noqa: BLE001
            print(f"  msg {msg_id} 切块失败: {exc}", file=sys.stderr)
        processed += 1
        if processed % 50 == 0:
            print(f"  …已处理 {processed} 条，落 chunk {chunks_total} 块")

    print(
        f"[chunk_backfill] 完成：处理 {processed} 条消息，"
        f"落 chunk {chunks_total} 块 → {db_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
