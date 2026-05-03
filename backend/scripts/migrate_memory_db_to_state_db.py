from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import paths
from deskpet.memory.session_db import SessionDB

Row = tuple[str, str, str, float]


def _default_data_path(name: str) -> Path:
    return paths.user_data_dir() / "data" / name


def _read_legacy_rows(memory_db: Path) -> list[Row]:
    if not memory_db.exists():
        raise FileNotFoundError(f"legacy memory db not found: {memory_db}")
    with sqlite3.connect(memory_db) as conn:
        rows = conn.execute(
            "SELECT session_id, role, content, created_at "
            "FROM conversation ORDER BY created_at ASC, id ASC"
        ).fetchall()
    return [
        (str(session_id), str(role), str(content), float(created_at or 0.0))
        for session_id, role, content, created_at in rows
    ]


def _read_existing_keys(state_db: Path) -> set[Row]:
    if not state_db.exists():
        return set()
    with sqlite3.connect(state_db) as conn:
        try:
            rows = conn.execute(
                "SELECT session_id, role, content, created_at FROM messages"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return set()
            raise
    return {
        (str(session_id), str(role), str(content), float(created_at or 0.0))
        for session_id, role, content, created_at in rows
    }


async def _append_with_created_at(db: SessionDB, row: Row) -> None:
    session_id, role, content, created_at = row
    message_id = await db.append_message(
        session_id=session_id,
        role=role,
        content=content,
    )
    import aiosqlite

    async with aiosqlite.connect(db._db_path) as conn:
        await conn.execute(
            "UPDATE messages SET created_at = ? WHERE id = ?",
            (created_at, message_id),
        )
        await conn.commit()


async def _run_migration_async(
    memory_db: Path,
    state_db: Path,
    *,
    dry_run: bool,
) -> tuple[int, int]:
    legacy_rows = _read_legacy_rows(memory_db)
    existing = _read_existing_keys(state_db)

    unique_rows: list[Row] = []
    seen = set(existing)
    skipped = 0
    for row in legacy_rows:
        if row in seen:
            skipped += 1
            continue
        seen.add(row)
        unique_rows.append(row)

    if dry_run:
        return len(unique_rows), skipped

    db = SessionDB(state_db)
    await db.initialize()
    try:
        for row in unique_rows:
            await _append_with_created_at(db, row)
    finally:
        await db.close()
    return len(unique_rows), skipped


def run_migration(
    memory_db: str | Path,
    state_db: str | Path,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    return asyncio.run(
        _run_migration_async(
            Path(memory_db),
            Path(state_db),
            dry_run=dry_run,
        )
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate legacy memory.db conversation rows into state.db messages."
    )
    parser.add_argument(
        "--from",
        dest="from_path",
        type=Path,
        default=_default_data_path("memory.db"),
        help="Path to legacy memory.db.",
    )
    parser.add_argument(
        "--to",
        dest="to_path",
        type=Path,
        default=_default_data_path("state.db"),
        help="Path to canonical state.db.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows without writing state.db.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    migrated, skipped = run_migration(
        args.from_path,
        args.to_path,
        dry_run=args.dry_run,
    )
    print(f"migrated {migrated} rows, dedup skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
