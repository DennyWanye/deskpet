from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path


def _load_script():
    script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_memory_db_to_state_db.py"
    spec = importlib.util.spec_from_file_location("migrate_memory_db_to_state_db", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrate_memory_db_to_state_db_dry_run(tmp_path: Path):
    memory_db = tmp_path / "memory.db"
    state_db = tmp_path / "state.db"
    with sqlite3.connect(memory_db) as conn:
        conn.execute(
            "CREATE TABLE conversation ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT NOT NULL, role TEXT NOT NULL, "
            "content TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO conversation(session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            [
                ("s1", "user", "hello", 1.0),
                ("s1", "assistant", "hi", 2.0),
            ],
        )

    script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_memory_db_to_state_db.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--from",
            str(memory_db),
            "--to",
            str(state_db),
            "--dry-run",
        ],
        cwd=script.parents[1],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "migrated 2 rows, dedup skipped 0" in result.stdout
    assert not state_db.exists()


def test_migrate_memory_db_to_state_db_skips_duplicates(tmp_path: Path):
    module = _load_script()

    memory_db = tmp_path / "memory.db"
    state_db = tmp_path / "state.db"
    with sqlite3.connect(memory_db) as conn:
        conn.execute(
            "CREATE TABLE conversation ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT NOT NULL, role TEXT NOT NULL, "
            "content TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO conversation(session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            [
                ("s1", "user", "same", 1.0),
                ("s1", "user", "same", 1.0),
                ("s1", "assistant", "new", 2.0),
            ],
        )

    migrated, skipped = module.run_migration(memory_db, state_db, dry_run=False)

    assert (migrated, skipped) == (2, 1)
    with sqlite3.connect(state_db) as conn:
        rows = conn.execute(
            "SELECT session_id, role, content, created_at "
            "FROM messages ORDER BY created_at ASC, id ASC"
        ).fetchall()
    assert rows == [
        ("s1", "user", "same", 1.0),
        ("s1", "assistant", "new", 2.0),
    ]
