"""Run SQL migrations against a SQLite database.

Behaviour:
- Creates ``schema_migrations(version TEXT PRIMARY KEY, applied_at REAL)``
  if missing.
- Walks ``memory/migrations/*.sql`` in lexicographic order.
- Each unseen file is executed inside a transaction; on success the
  version is recorded. A failure leaves the table untouched (raise).

This is async to match the rest of the memory layer, but the actual work
is fast — we just open one connection per run.
"""
from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at REAL NOT NULL
)
"""

# Default location — ``memory/migrations/`` sibling to this file.
DEFAULT_DIR = Path(__file__).parent / "migrations"


def _discover(migrations_dir: Path) -> list[Path]:
    """Return *.sql files sorted by filename (the `NNN_` prefix)."""
    if not migrations_dir.exists():
        return []
    return sorted(p for p in migrations_dir.iterdir() if p.suffix == ".sql")


async def run_migrations(
    db_path: str | Path,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply all pending migrations; return list of versions applied this run."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migrations_dir = migrations_dir or DEFAULT_DIR
    files = _discover(migrations_dir)

    applied_now: list[str] = []
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_MIGRATIONS_TABLE)
        await db.commit()
        cursor = await db.execute("SELECT version FROM schema_migrations")
        already = {row[0] for row in await cursor.fetchall()}
        await cursor.close()

        for path in files:
            version = path.name  # e.g. "001_initial.sql"
            if version in already:
                continue
            sql = path.read_text(encoding="utf-8")
            # executescript does its own transaction; we record the version
            # in a separate commit so a scripting failure halts cleanly.
            await db.executescript(sql)
            await db.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, time.time()),
            )
            await db.commit()
            applied_now.append(version)

    return applied_now
