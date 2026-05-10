"""P4-S22 — CodeModeManager state lifecycle + session-id derivation."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.code_mode import CodeModeManager
from deskpet.code_mode.state import _code_session_id


def test_default_disabled():
    mgr = CodeModeManager()
    assert mgr.is_enabled("default") is False
    assert mgr.get("default") is None
    assert mgr.project_root("default") is None
    assert mgr.code_session_id("default") is None


def test_enter_then_is_enabled(tmp_path):
    mgr = CodeModeManager()
    state = mgr.enter("default", tmp_path)
    assert state.enabled is True
    assert state.project_root == tmp_path.resolve()
    assert state.code_session_id and state.code_session_id.startswith("code-")
    assert mgr.is_enabled("default") is True


def test_exit_disables_and_drops(tmp_path):
    mgr = CodeModeManager()
    mgr.enter("default", tmp_path)
    mgr.exit("default")
    assert mgr.is_enabled("default") is False
    # exit twice is safe
    mgr.exit("default")
    assert mgr.is_enabled("default") is False


def test_session_id_is_stable_for_same_path(tmp_path):
    """Re-entering the same project recovers the same code_session_id —
    that's how we get cross-session memory continuity."""
    mgr1 = CodeModeManager()
    mgr2 = CodeModeManager()
    s1 = mgr1.enter("default", tmp_path)
    s2 = mgr2.enter("default", tmp_path)
    assert s1.code_session_id == s2.code_session_id


def test_session_id_differs_for_different_paths(tmp_path):
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()
    mgr = CodeModeManager()
    sa = mgr.enter("default", a)
    sb = mgr.enter("user2", b)
    assert sa.code_session_id != sb.code_session_id


def test_per_base_session_isolation(tmp_path):
    """Two base sessions can have different code-mode states simultaneously."""
    proj_a = tmp_path / "a"; proj_a.mkdir()
    proj_b = tmp_path / "b"; proj_b.mkdir()
    mgr = CodeModeManager()
    mgr.enter("alice", proj_a)
    mgr.enter("bob", proj_b)
    assert mgr.is_enabled("alice")
    assert mgr.is_enabled("bob")
    mgr.exit("alice")
    assert not mgr.is_enabled("alice")
    assert mgr.is_enabled("bob")  # Bob untouched


def test_code_session_id_helper_is_deterministic_8_hex(tmp_path):
    sid = _code_session_id(tmp_path)
    assert sid.startswith("code-")
    rest = sid[len("code-"):]
    assert len(rest) == 8
    assert all(c in "0123456789abcdef" for c in rest)


def test_all_sessions_returns_snapshot_copy(tmp_path):
    mgr = CodeModeManager()
    mgr.enter("default", tmp_path)
    snap = mgr.all_sessions()
    snap.clear()  # mutating snapshot must not affect manager
    assert mgr.is_enabled("default")
