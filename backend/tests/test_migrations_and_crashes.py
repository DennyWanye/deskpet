"""Tests for S7: migrator + crash reporter."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from memory.conversation import SqliteConversationMemory
from memory.migrator import DEFAULT_DIR, run_migrations
from observability.crash_reports import install_crash_reporter


# --- run_migrations ---


@pytest.mark.asyncio
async def test_migrator_applies_initial_and_records_version(tmp_path: Path):
    db = tmp_path / "test.db"
    applied = await run_migrations(db)
    assert "001_initial.sql" in applied

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {r[0] for r in await cur.fetchall()}
    assert "conversation" in tables
    assert "schema_migrations" in tables


@pytest.mark.asyncio
async def test_migrator_is_idempotent(tmp_path: Path):
    db = tmp_path / "idem.db"
    first = await run_migrations(db)
    second = await run_migrations(db)
    assert first  # at least one applied first time
    assert second == []  # nothing new to apply


@pytest.mark.asyncio
async def test_migrator_applies_custom_files_in_order(tmp_path: Path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_first.sql").write_text(
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    (migrations / "002_second.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )

    db = tmp_path / "custom.db"
    applied = await run_migrations(db, migrations_dir=migrations)
    assert applied == ["001_first.sql", "002_second.sql"]

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        rows = [r[0] for r in await cur.fetchall()]
    assert rows == ["001_first.sql", "002_second.sql"]


@pytest.mark.asyncio
async def test_migrator_default_dir_points_to_package(tmp_path: Path):
    """Sanity check: DEFAULT_DIR contains the 001_initial.sql we ship."""
    assert (DEFAULT_DIR / "001_initial.sql").exists()


# --- SqliteConversationMemory uses migrator ---


@pytest.mark.asyncio
async def test_conversation_memory_runs_migrations_on_first_use(tmp_path: Path):
    db = tmp_path / "conv.db"
    mem = SqliteConversationMemory(db)
    await mem.append("s1", "user", "hi")

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute("SELECT version FROM schema_migrations")
        versions = {r[0] for r in await cur.fetchall()}
    assert "001_initial.sql" in versions


# --- install_crash_reporter ---


def test_crash_reporter_writes_file_on_uncaught(tmp_path: Path, monkeypatch):
    """Trigger the excepthook manually and assert a report lands in tmp_path."""
    install_crash_reporter(directory=tmp_path)

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        exc_type, exc_value, tb = sys.exc_info()
        # Call the hook directly (pytest doesn't let a real uncaught escape)
        sys.excepthook(exc_type, exc_value, tb)

    reports = list(tmp_path.glob("python-*.log"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")
    assert "RuntimeError" in body
    assert "boom" in body


def test_crash_reporter_chains_previous_hook(tmp_path: Path):
    """Previous hook must still run (tracebacks still reach stderr)."""
    calls = []
    prior = sys.excepthook

    def spy(exc_type, exc_value, tb):
        calls.append((exc_type, str(exc_value)))

    sys.excepthook = spy
    install_crash_reporter(directory=tmp_path)
    try:
        try:
            raise ValueError("inner")
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = prior

    assert calls and calls[0][0] is ValueError


def test_crash_reporter_skips_keyboard_interrupt(tmp_path: Path):
    """Ctrl+C must NOT produce a crash report — it's expected shutdown."""
    install_crash_reporter(directory=tmp_path)
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())

    assert list(tmp_path.glob("python-*.log")) == []
