"""向量数据库健康检查 — 五维度 end-to-end。

不依赖 backend 进程，独立运行。每个维度独立打印结果，单独 PASS/FAIL，
任何一个 fail 不阻断后续检查。

使用：
    cd backend && python -m scripts.check_vector_db [--db PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows console 默认 GBK 编码，print 中文会乱码 — force UTF-8 stdout.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

import numpy as np

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


def _print_section(title: str) -> None:
    print("\n" + "=" * 64)
    print(f" {title}")
    print("=" * 64)


def _pass(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"  [info] {msg}")


# ---------------------------------------------------------------------
# Check 1: SQLite-vec extension loads + schema is correct
# ---------------------------------------------------------------------
async def check_sqlite_vec(db_path: Path) -> bool:
    _print_section("1. SQLite + sqlite-vec 扩展")
    if not db_path.exists():
        _fail(f"state.db not found at {db_path}")
        return False
    _info(f"db path: {db_path}")

    from deskpet.memory.session_db import SessionDB

    sdb = SessionDB(db_path=db_path)
    try:
        await sdb.initialize()
    except Exception as exc:
        _fail(f"SessionDB.initialize raised: {exc}")
        return False
    _pass("SessionDB.initialize() OK")

    # Inspect tables — must load sqlite_vec extension on the inspection
    # connection too (SessionDB's autocommit transactions open new conns,
    # we don't share theirs).
    import sqlite3
    import sqlite_vec  # type: ignore[import-untyped]

    def _sync_inspect() -> tuple[list[str], int, int]:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "OR type='virtual table' ORDER BY name"
            )
            tables = [r[0] for r in cur.fetchall()]
            (n_msgs,) = conn.execute("SELECT count(*) FROM messages").fetchone()
            try:
                (n_vecs,) = conn.execute(
                    "SELECT count(*) FROM messages_vec"
                ).fetchone()
            except Exception:
                n_vecs = -1
            return tables, int(n_msgs), int(n_vecs)
        finally:
            conn.close()

    tables, n_msgs, n_vecs = await asyncio.get_running_loop().run_in_executor(
        None, _sync_inspect
    )

    has_msgs_table = "messages" in tables
    has_vec_table = "messages_vec" in tables
    if has_msgs_table:
        _pass(f"`messages` table exists ({n_msgs} rows)")
    else:
        _fail("`messages` table missing")
    if has_vec_table:
        _pass(f"`messages_vec` virtual table exists ({n_vecs} rows)")
    else:
        _fail("`messages_vec` virtual table missing — sqlite-vec broken")

    coverage = (n_vecs / n_msgs * 100) if n_msgs > 0 else 0.0
    if n_msgs > 0:
        _info(f"vector coverage: {n_vecs}/{n_msgs} = {coverage:.1f}%")
    if coverage < 50 and n_msgs > 10:
        _info(
            f"  → 大部分历史消息还没向量化。运行 backfill_missing() 补齐。"
        )

    await sdb.close()
    return has_msgs_table and has_vec_table


# ---------------------------------------------------------------------
# Check 2: Embedder produces real vectors (not mock)
# ---------------------------------------------------------------------
async def check_embedder(model_path: Path) -> tuple[bool, "Embedder | None"]:
    _print_section("2. BGE-M3 真实嵌入器")
    if not model_path.exists():
        _fail(f"model dir not found at {model_path}")
        return False, None
    _info(f"model: {model_path}")

    from deskpet.memory.embedder import Embedder

    emb = Embedder(
        model_path=model_path, mode="subprocess", use_mock_when_missing=False
    )
    t0 = perf_counter()
    try:
        await emb.warmup()
    except Exception as exc:
        _fail(f"warmup failed: {exc}")
        return False, None
    elapsed = (perf_counter() - t0) * 1000
    _pass(f"warmup OK in {elapsed:.0f}ms (mock={emb.is_mock()})")

    if emb.is_mock():
        _fail("Embedder fell back to MOCK — vectors will be random!")
        return False, emb

    # Embed something + check vector shape + L2-norm ≈ 1.0
    t0 = perf_counter()
    vecs = await emb.encode(["hello world"])
    elapsed = (perf_counter() - t0) * 1000
    if vecs.shape != (1, 1024):
        _fail(f"vector shape wrong: {vecs.shape}, expected (1, 1024)")
        return False, emb
    norm = np.linalg.norm(vecs[0])
    if not (0.95 <= norm <= 1.05):
        _fail(f"vector not normalized: ||v|| = {norm:.3f}")
        return False, emb
    _pass(
        f"encode 1 text in {elapsed:.0f}ms, shape=(1,1024), ||v||={norm:.3f}"
    )
    return True, emb


# ---------------------------------------------------------------------
# Check 3: Cross-lingual semantic similarity
# ---------------------------------------------------------------------
async def check_cross_lingual(emb) -> bool:  # type: ignore[no-untyped-def]
    _print_section("3. 跨语言语义相似度")
    if emb is None or emb.is_mock():
        _fail("skipping — embedder unavailable")
        return False

    # 注：BGE-M3 INT8 量化下，无关文本对相似度底噪 ~0.40-0.50，比 fp16
    # 高一点。所以跨域阈值放宽到 0.55 才算"明显高于背景"。
    pairs = [
        ("我喜欢吃苹果", "I like eating apples", 0.65, "high"),
        ("今天天气真好", "The weather is nice today", 0.65, "high"),
        ("我想吃苹果", "我讨厌苹果", 0.60, "high"),  # 同主题情感对立但语义近
        ("我喜欢吃苹果", "Python is a programming language", 0.55, "low"),
    ]
    all_pass = True
    for zh, en, threshold, kind in pairs:
        vecs = await emb.encode([zh, en])
        sim = float(np.dot(vecs[0], vecs[1]))
        if kind == "high":
            ok = sim >= threshold
            arrow = ">="
        else:  # 反向：要明显低于
            ok = sim < threshold
            arrow = "<"
        marker = "PASS" if ok else "WARN"
        print(
            f"  [{marker}] sim('{zh}','{en}') = {sim:.3f} (expect {arrow} {threshold})"
        )
        all_pass = all_pass and ok
    return all_pass


# ---------------------------------------------------------------------
# Check 4: Round-trip — write a message + retrieve it via vector search
# ---------------------------------------------------------------------
async def check_round_trip(db_path: Path, emb) -> bool:  # type: ignore[no-untyped-def]
    _print_section("4. 写入 + 向量召回 端到端")
    if emb is None or emb.is_mock():
        _fail("skipping — embedder unavailable")
        return False

    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.vector_worker import VectorWorker
    from deskpet.memory.retriever import Retriever

    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()

    test_session = "vec-health-check"
    test_messages = [
        "我最喜欢的编程语言是 Rust",
        "今晚我想吃日式拉面",
        "deskpet 是一个桌面宠物 + AI 助手",
        "天气预报说明天会下雨",
    ]
    msg_ids = []
    for content in test_messages:
        msg_id = await sdb.append_message(
            session_id=test_session, role="user", content=content,
        )
        msg_ids.append(msg_id)
    _info(f"wrote {len(test_messages)} test messages to session={test_session}")

    # Drive vector worker to flush these into messages_vec
    vw = VectorWorker(
        embedder=emb, session_db=sdb, batch_size=8, flush_interval_s=0.2,
    )
    await vw.start()
    for mid, text in zip(msg_ids, test_messages):
        await vw.enqueue(mid, text)
    await asyncio.sleep(1.0)  # let it flush
    await vw.stop()
    _pass(f"vector worker enqueued + flushed {len(msg_ids)} ids")

    # Verify rows landed in messages_vec
    import sqlite3
    import sqlite_vec  # type: ignore[import-untyped]

    def _sync_count_indexed() -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            (n,) = conn.execute(
                "SELECT count(*) FROM messages_vec WHERE message_id IN ("
                + ",".join("?" * len(msg_ids)) + ")",
                msg_ids,
            ).fetchone()
            return int(n)
        finally:
            conn.close()

    n_indexed = await asyncio.get_running_loop().run_in_executor(
        None, _sync_count_indexed
    )
    if n_indexed != len(msg_ids):
        _fail(
            f"only {n_indexed}/{len(msg_ids)} of test messages were indexed"
        )
    else:
        _pass(f"all {len(msg_ids)} messages indexed in messages_vec")

    # Now retrieve: ask "what programming language do I like?" should
    # return the Rust message (cross-lingual!)
    retr = Retriever(session_db=sdb, embedder=emb)
    queries = [
        ("What programming language do I like?", "Rust"),
        ("ramen 拉面 dinner", "拉面"),
        ("desktop pet AI", "桌面宠物"),
    ]
    all_correct = True
    for q, expected_keyword in queries:
        hits = await retr.recall(query=q, top_k=4)
        # filter to our test session's messages by id
        hits = [h for h in hits if h.message_id in msg_ids]
        if not hits:
            _fail(f"query '{q}' → 0 results from test session")
            all_correct = False
            continue
        top_text = hits[0].text
        ok = expected_keyword in top_text
        marker = "PASS" if ok else "WARN"
        print(
            f"  [{marker}] '{q}' → top1 (score={hits[0].score:.3f}, "
            f"source={hits[0].source}): '{top_text[:50]}' "
            f"(expected: contains '{expected_keyword}')"
        )
        all_correct = all_correct and ok

    # Cleanup
    def _sync_cleanup() -> None:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            placeholder = ",".join("?" * len(msg_ids))
            conn.execute(
                f"DELETE FROM messages_vec WHERE message_id IN ({placeholder})",
                msg_ids,
            )
            conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholder})",
                msg_ids,
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.get_running_loop().run_in_executor(None, _sync_cleanup)
    _info(f"cleaned up {len(msg_ids)} test rows")

    await emb.close()
    await sdb.close()
    return all_correct


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    args = parser.parse_args()

    db_path = Path(args.db)
    model_path = Path(args.model)

    results: dict[str, bool] = {}
    results["sqlite-vec"] = await check_sqlite_vec(db_path)
    ok, emb = await check_embedder(model_path)
    results["embedder"] = ok
    if ok:
        results["cross-lingual"] = await check_cross_lingual(emb)
        results["round-trip"] = await check_round_trip(db_path, emb)
    else:
        results["cross-lingual"] = False
        results["round-trip"] = False
        if emb is not None:
            try:
                await emb.close()
            except Exception:
                pass

    # Summary
    _print_section("总结")
    for name, ok in results.items():
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] {name}")

    all_ok = all(results.values())
    print()
    if all_ok:
        print("  RESULT: ALL CHECKS PASSED — 向量数据库工作正常")
        return 0
    else:
        print("  RESULT: SOME CHECKS FAILED — 见上方")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
