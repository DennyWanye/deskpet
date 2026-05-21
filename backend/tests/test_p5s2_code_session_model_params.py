"""code-session-model-params — SessionDB model_params round-trip.

Spec: openspec/changes/code-session-model-params/specs/
code-session-model-params/spec.md
  Requirement "Per-code-session model+params binding is persisted":
    - Set model + params round-trips
    - Legacy row without params stays valid
    - Clear binding restores global chain
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.session_db import SessionDB


@pytest.fixture()
def sdb(tmp_path: Path) -> SessionDB:
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    asyncio.run(db.initialize())
    return db


def _run(coro):
    return asyncio.run(coro)


def test_migration_008_added_model_params_column(sdb: SessionDB) -> None:
    con = sqlite3.connect(sdb._db_path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(code_session_provider)")}
    uv = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    assert "model_params" in cols
    assert uv >= 16


def test_set_get_model_params_round_trip(sdb: SessionDB) -> None:
    params = {
        "thinking": True,
        "fast": False,
        "context": "1m",
        "effort": "high",
    }
    _run(
        sdb.set_code_session_provider_binding(
            "code:proj-a", None, "gpt-5.5", params
        )
    )
    got = _run(sdb.get_code_session_provider_binding("code:proj-a"))
    assert got["preferred_model"] == "gpt-5.5"
    assert got["provider_id"] is None
    assert got["model_params"] == params


def test_legacy_row_without_params_is_valid(sdb: SessionDB) -> None:
    # Simulate a pre-008 / no-params row written via the model-only path.
    _run(
        sdb.set_code_session_provider_binding(
            "code:legacy", None, "deepseek-v4-pro"
        )
    )
    got = _run(sdb.get_code_session_provider_binding("code:legacy"))
    assert got["preferred_model"] == "deepseek-v4-pro"
    assert got["model_params"] is None  # provider defaults, no error


def test_corrupt_params_json_degrades_to_none(sdb: SessionDB) -> None:
    _run(
        sdb.set_code_session_provider_binding(
            "code:bad", None, "gpt-5.5", {"effort": "high"}
        )
    )
    con = sqlite3.connect(sdb._db_path)
    con.execute(
        "UPDATE code_session_provider SET model_params=? "
        "WHERE base_session_id=?",
        ("{not json", "code:bad"),
    )
    con.commit()
    con.close()
    got = _run(sdb.get_code_session_provider_binding("code:bad"))
    assert got["model_params"] is None  # never raises


def test_clear_binding_removes_row(sdb: SessionDB) -> None:
    _run(
        sdb.set_code_session_provider_binding(
            "code:proj-c", None, "gpt-5.5", {"effort": "max"}
        )
    )
    _run(sdb.set_code_session_provider_binding("code:proj-c", None, None, None))
    got = _run(sdb.get_code_session_provider_binding("code:proj-c"))
    assert got == {
        "provider_id": None,
        "preferred_model": None,
        "model_params": None,
    }
    con = sqlite3.connect(sdb._db_path)
    n = con.execute(
        "SELECT COUNT(*) FROM code_session_provider "
        "WHERE base_session_id='code:proj-c'"
    ).fetchone()[0]
    con.close()
    assert n == 0
