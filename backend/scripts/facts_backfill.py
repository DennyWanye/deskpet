# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""记忆系统升级 WI-M1.2 — facts 存量 backfill。

facts 抽取只在新消息写入时触发（``_on_message_written`` fanout），历史
对话不会被抽 → 上线初期 ``facts`` 表长期接近空。本脚本对历史 ``messages``
表批量跑 ``FactExtractor.process_message``，把存量事实补出来。

可重入：已被抽过的消息（``facts.source_msg_id`` 命中）默认跳过。

要求：backend 不在跑（共享 state.db 写锁会冲突）；config 里配好可用的
LLM provider（抽取依赖 LLM）。

用法::

    cd backend
    python -m scripts.facts_backfill                 # 全量
    python -m scripts.facts_backfill --limit 200     # 只跑最近 200 条
    python -m scripts.facts_backfill --db <path>     # 指定 state.db
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


def _make_llm_call():
    """构造 (prompt:str)->str LLMCall。无 provider → 返回 None。"""
    try:
        # config 是 backend 根目录顶层模块，不在 deskpet 包下。
        from config import load_config
        from providers.openai_compatible import OpenAICompatibleProvider
    except ImportError:
        return None
    cfg = load_config()
    providers = getattr(cfg.llm, "providers", None) or []
    if not providers:
        return None
    p = providers[0]
    provider = OpenAICompatibleProvider(
        base_url=p.base_url,
        api_key=getattr(p, "api_key", "") or "",
        model=p.model,
    )

    async def _call(prompt: str) -> str:
        result = await provider.chat_with_tools(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.2,
        )
        return (result or {}).get("content") or ""

    return _call


async def _already_extracted_ids(db_path: Path) -> set[int]:
    """facts.source_msg_id 已命中的 message id 集合 —— 跳过它们。"""
    import aiosqlite
    from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

    await ensure_memory_v2_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT DISTINCT source_msg_id FROM facts "
            "WHERE source_msg_id IS NOT NULL"
        )
        rows = await cur.fetchall()
        await cur.close()
    return {int(r[0]) for r in rows}


async def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.facts_backfill")
    parser.add_argument("--db", default=None, help="state.db 路径")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="最多处理多少条历史消息（0 = 全量）",
    )
    parser.add_argument(
        "--min-chars", type=int, default=8,
        help="字数采样门（与 [memory.v2.facts].min_user_chars 对齐）",
    )
    args = parser.parse_args()

    db_path = await _resolve_state_db(args.db)
    if not db_path.exists():
        print(f"state.db not found at {db_path}", file=sys.stderr)
        return 2

    llm = _make_llm_call()
    if llm is None:
        print(
            "没有可用的 LLM provider —— facts 抽取依赖 LLM，无法 backfill。",
            file=sys.stderr,
        )
        return 3

    import aiosqlite
    from deskpet.memory.facts import FactsStore, FactExtractor
    from deskpet.memory.embedder import Embedder

    # embedder 注入 → backfill 出的 facts 也带向量（WI-M1.4）。无 BGE-M3
    # 权重时 Embedder 自动 mock，_embed_fact 返回 None，召回降级 LIKE。
    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    await embedder.warmup()
    store = FactsStore(db_path, embedder=embedder)
    extractor = FactExtractor(store, extract_llm=llm, min_chars=args.min_chars)

    done_ids = await _already_extracted_ids(db_path)
    print(f"[facts_backfill] {len(done_ids)} 条消息已抽过，跳过。")

    async with aiosqlite.connect(db_path) as db:
        sql = (
            "SELECT id, role, content FROM messages "
            "WHERE role IN ('user', 'assistant') "
            "ORDER BY id ASC"
        )
        cur = await db.execute(sql)
        rows = await cur.fetchall()
        await cur.close()

    processed = 0
    persisted_total = 0
    for msg_id, role, content in rows:
        if int(msg_id) in done_ids:
            continue
        if args.limit and processed >= args.limit:
            break
        try:
            facts = await extractor.process_message(
                message_id=int(msg_id), content=str(content or ""), role=str(role),
            )
            persisted_total += len(facts)
        except Exception as exc:  # noqa: BLE001
            print(f"  msg {msg_id} 抽取失败: {exc}", file=sys.stderr)
        processed += 1
        if processed % 25 == 0:
            print(f"  …已处理 {processed} 条，落 facts {persisted_total} 条")

    print(
        f"[facts_backfill] 完成：处理 {processed} 条消息，"
        f"落 facts {persisted_total} 条 → {db_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
