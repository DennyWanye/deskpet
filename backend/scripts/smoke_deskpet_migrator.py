"""P4-S1 task 2.5 — 手动 smoke for deskpet.memory.migrator + schema.

Run:
    python backend/scripts/smoke_deskpet_migrator.py

What it exercises:
  1. initialize_state_db on an empty tmp dir → 预期 user_version=9，tables 齐全
  2. 再跑一次 → 预期幂等（applied = []），backup 存一份 .bak
  3. 放入一个"坏" migration 临时目录 → 预期 InitializeError 且 .bak 恢复了原库

Exit code：全部成功 0；任何步骤失败 >= 1。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# 保证 smoke 脚本能直接 `python backend/scripts/smoke_deskpet_migrator.py` 跑起来
# 不需要先 pip install -e backend
REPO_BACKEND = Path(__file__).resolve().parent.parent
if str(REPO_BACKEND) not in sys.path:
    sys.path.insert(0, str(REPO_BACKEND))

from deskpet.memory.migrator import (  # noqa: E402
    MigrationError,
    ensure_v9,
    run_migrations,
)
from deskpet.memory.schema import InitializeError, initialize_state_db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("smoke")


def _print_schema(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','trigger') ORDER BY type, name"
            ).fetchall()
        ]
    finally:
        conn.close()
    print(f"  user_version: {v}")
    print(f"  schema objects: {tables}")


async def _step_fresh(workdir: Path) -> None:
    print("[1/3] fresh init")
    db = workdir / "state.db"
    await initialize_state_db(db)
    _print_schema(db)
    if not db.exists():
        raise RuntimeError("db missing after initialize")


async def _step_idempotent(workdir: Path) -> None:
    print("[2/3] idempotent re-run")
    db = workdir / "state.db"
    applied = await ensure_v9(db)
    if applied:
        raise RuntimeError(
            f"expected no applied migrations on re-run, got {applied}"
        )
    print("  applied = [] -> idempotent OK")
    # 进一步写一条数据验证库仍可用
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO sessions(id, created_at, metadata) VALUES (?,?,?)",
            ("smoke-sess", 1, "{}"),
        )
        conn.commit()
        rows = conn.execute("SELECT id FROM sessions").fetchall()
    finally:
        conn.close()
    if rows != [("smoke-sess",)]:
        raise RuntimeError(f"unexpected sessions content: {rows}")
    print(f"  insert+select OK: {rows}")


async def _step_failure_rollback(workdir: Path) -> None:
    """构造"坏 migration" 场景，断言 .bak 恢复成功。

    做法：写一个 002_bad.sql 到临时 dir，然后让 run_migrations 指向它
    触发错误。注意 run_migrations 只吃 migrations_dir 参数，所以我们
    直接调 run_migrations 而非 initialize_state_db 来制造失败；
    同时自己做 .bak 以验证回滚模式可行（不经 schema.initialize_state_db，
    因为它的 rollback 只在 ensure_v9 走默认 dir 时才有 .bak 可恢复）。
    """
    print("[3/3] failure rollback drill")
    db = workdir / "state.db"
    # 先备份当前 state.db
    bak = db.with_suffix(".db.bak.smoke")
    shutil.copy2(db, bak)
    pre_bytes = db.read_bytes()

    # 构造坏迁移
    bad_dir = workdir / "bad_migrations"
    bad_dir.mkdir()
    (bad_dir / "999_bad.sql").write_text(
        "-- intentionally invalid SQL for smoke test\n"
        "CREATE TABLES broken_syntax_here;\n",
        encoding="utf-8",
    )

    raised = False
    try:
        await run_migrations(db, migrations_dir=bad_dir)
    except MigrationError as exc:
        raised = True
        print(f"  got MigrationError as expected: {exc}")

    if not raised:
        raise RuntimeError("expected MigrationError from bad SQL, nothing raised")

    # 模拟 schema.initialize_state_db 的回滚动作：拿 .bak 覆盖
    db.unlink()
    shutil.copy2(bak, db)
    if db.read_bytes() != pre_bytes:
        raise RuntimeError("post-rollback db bytes differ from pre")
    print("  manual .bak restore OK, db back to pre-migration state")

    # 进一步：试试 initialize_state_db 的内置回滚（通过 monkeypatch ensure_v9）
    import deskpet.memory.schema as schema_mod

    async def _boom(*_a, **_kw):
        raise MigrationError("injected failure")

    original = schema_mod.ensure_v9
    schema_mod.ensure_v9 = _boom  # type: ignore[assignment]
    try:
        try:
            await initialize_state_db(db)
        except InitializeError as exc:
            print(f"  initialize_state_db correctly raised InitializeError: {exc}")
        else:
            raise RuntimeError("initialize_state_db should have raised")
    finally:
        schema_mod.ensure_v9 = original  # type: ignore[assignment]

    # 回滚后 db 内容应保持
    if db.read_bytes() != pre_bytes:
        raise RuntimeError("initialize_state_db rollback did not preserve db")
    print("  initialize_state_db rollback preserved db bytes")


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="deskpet_smoke_migrator_") as td:
        workdir = Path(td)
        try:
            await _step_fresh(workdir)
            await _step_idempotent(workdir)
            await _step_failure_rollback(workdir)
        except Exception as exc:  # noqa: BLE001
            log.error("smoke failed: %s", exc, exc_info=True)
            return 1
    print("all smoke steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
