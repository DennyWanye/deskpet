"""P5-S2 Phase 0 — sensor + remediation hint contract tests.

Spec: openspec/changes/p5-s2-self-healing-harness/specs/tool-registry/sensor-feedback.md

Every os_tool error response must extend its legacy ``{"error": "..."}``
shape with structured remediation fields:

* ``hint`` — non-empty Chinese str telling the LLM how to recover
* ``examples`` — list of sample valid args (may be empty list, but field
  must exist) — the contract test only requires ``isinstance(out["examples"], list)``

Backward compatibility: legacy ``out["error"]`` still works (additive
fields, no rename of existing error codes).
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Callable

import pytest

from deskpet.tools import os_tools
from deskpet.tools.os_tools import (
    desktop_create_file,
    edit_file,
    list_directory,
    read_file,
    run_shell,
    web_fetch,
    write_file,
)


def _decode(raw: Any) -> dict[str, Any]:
    """Tools always return JSON strings — decode for assertion."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw  # already dict (defensive)


def _assert_hint_envelope(out: dict[str, Any], *, expect_hint_substr: str | None = None) -> None:
    """Common contract assertions for any error envelope."""
    assert "error" in out, f"missing legacy error field: {out!r}"
    assert out.get("ok") is False, f"expected ok=false, got {out!r}"
    assert "hint" in out, f"missing hint field: {out!r}"
    assert isinstance(out["hint"], str) and out["hint"].strip(), (
        f"hint must be non-empty str, got {out.get('hint')!r}"
    )
    assert "examples" in out, f"missing examples field: {out!r}"
    assert isinstance(out["examples"], list), (
        f"examples must be list, got {type(out.get('examples')).__name__}"
    )
    if expect_hint_substr is not None:
        assert expect_hint_substr in out["hint"], (
            f"hint should contain {expect_hint_substr!r}, got {out['hint']!r}"
        )


# ---------------------------------------------------------------------
# 0.1 run_shell
# ---------------------------------------------------------------------


def test_run_shell_missing_command_returns_hint() -> None:
    out = _decode(run_shell({}, ""))
    _assert_hint_envelope(out, expect_hint_substr="command")
    # Per spec scenario, the hint should make clear that command is required.
    assert "command 字段必填" in out["hint"] or "command" in out["hint"]
    assert len(out["examples"]) >= 1


def test_run_shell_invalid_cwd_returns_hint(tmp_path: Path) -> None:
    """cwd points to a path that doesn't exist — OSError path."""
    bad_cwd = str(tmp_path / "definitely_does_not_exist_xyz")
    out = _decode(run_shell({"command": "echo hi", "cwd": bad_cwd}, ""))
    _assert_hint_envelope(out)


def test_run_shell_timeout_returns_hint() -> None:
    # `sleep 5` works in bash, busybox sh, AND PowerShell (alias for
    # Start-Sleep). cmd.exe is unlikely to be picked by the P5-S2
    # tier-based shell selector on a normal dev box; if it ever is,
    # this test would need a `ping` fallback.
    cmd = "sleep 5"
    out = _decode(run_shell({"command": cmd, "timeout": 1}, ""))
    _assert_hint_envelope(out)
    assert out["error"] == "timeout"
    # Spec requires elapsed_seconds for timeout
    assert "elapsed_seconds" in out
    # stdout_partial may be empty string, but field should exist
    assert "stdout_partial" in out


# ---------------------------------------------------------------------
# 0.3 write_file
# ---------------------------------------------------------------------


def test_write_file_missing_path_returns_hint() -> None:
    out = _decode(write_file({"content": "hi"}, ""))
    _assert_hint_envelope(out, expect_hint_substr="path")
    assert len(out["examples"]) >= 1


def test_write_file_missing_content_returns_hint() -> None:
    out = _decode(write_file({"path": "x.txt"}, ""))
    _assert_hint_envelope(out)


def test_write_file_overwrite_blocked_returns_hint(tmp_path: Path) -> None:
    p = tmp_path / "exists.txt"
    p.write_text("old", encoding="utf-8")
    out = _decode(write_file({"path": str(p), "content": "new"}, ""))
    _assert_hint_envelope(out)
    # Spec hint should mention overwrite or edit_file alternative
    assert "overwrite" in out["hint"] or "edit_file" in out["hint"]


def test_write_file_oserror_returns_hint(tmp_path: Path) -> None:
    """Try writing to a directory path — OS will refuse with IsADirectory."""
    d = tmp_path / "imadir"
    d.mkdir()
    out = _decode(write_file({"path": str(d), "content": "x", "overwrite": True}, ""))
    _assert_hint_envelope(out)


# ---------------------------------------------------------------------
# 0.4 edit_file
# ---------------------------------------------------------------------


def test_edit_file_missing_path_returns_hint() -> None:
    out = _decode(edit_file({"old_string": "a", "new_string": "b"}, ""))
    _assert_hint_envelope(out, expect_hint_substr="path")


def test_edit_file_not_unique_returns_hint(tmp_path: Path) -> None:
    p = tmp_path / "doc.txt"
    p.write_text("x x x", encoding="utf-8")
    out = _decode(
        edit_file({"path": str(p), "old_string": "x", "new_string": "y"}, "")
    )
    _assert_hint_envelope(out)
    # Spec hints should suggest replace_all
    assert "replace_all" in out["hint"] or "唯一" in out["hint"]


def test_edit_file_not_found_returns_hint(tmp_path: Path) -> None:
    out = _decode(
        edit_file(
            {"path": str(tmp_path / "missing.txt"), "old_string": "a", "new_string": "b"},
            "",
        )
    )
    _assert_hint_envelope(out)


def test_edit_file_oserror_returns_hint(tmp_path: Path) -> None:
    # path is a dir — read_text will OSError
    d = tmp_path / "d"
    d.mkdir()
    out = _decode(
        edit_file({"path": str(d), "old_string": "a", "new_string": "b"}, "")
    )
    _assert_hint_envelope(out)


# ---------------------------------------------------------------------
# 0.5 read_file
# ---------------------------------------------------------------------


def test_read_file_missing_path_returns_hint() -> None:
    out = _decode(read_file({}, ""))
    _assert_hint_envelope(out, expect_hint_substr="path")


def test_read_file_file_not_found_returns_hint(tmp_path: Path) -> None:
    # Create a sibling so did_you_mean has a candidate
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    out = _decode(read_file({"path": str(tmp_path / "mian.go")}, ""))
    _assert_hint_envelope(out)
    # Spec asks for did_you_mean fuzzy candidates (at most 5)
    assert "did_you_mean" in out
    assert isinstance(out["did_you_mean"], list)
    assert len(out["did_you_mean"]) <= 5
    # main.go should be in the candidates
    assert any("main.go" in c for c in out["did_you_mean"])


def test_read_file_not_a_file_returns_hint(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    out = _decode(read_file({"path": str(d)}, ""))
    _assert_hint_envelope(out)


def test_read_file_binary_returns_hint(tmp_path: Path) -> None:
    """Binary file path — current read_file returns content with binary=True
    (success-like). The spec requires hint+examples on errors only, so
    binary may stay 'success'. We just assert no error contract violation."""
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\x00\x01\x02\xff\xfe")
    out = _decode(read_file({"path": str(p)}, ""))
    # Either it returns binary marker (no error), or it errors with hint.
    if "error" in out:
        _assert_hint_envelope(out)


# ---------------------------------------------------------------------
# 0.6 list_directory
# ---------------------------------------------------------------------


def test_list_directory_missing_path_returns_hint() -> None:
    out = _decode(list_directory({}, ""))
    _assert_hint_envelope(out, expect_hint_substr="path")


def test_list_directory_not_a_dir_returns_hint(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("hi", encoding="utf-8")
    out = _decode(list_directory({"path": str(p)}, ""))
    _assert_hint_envelope(out)


def test_list_directory_nonexistent_returns_hint(tmp_path: Path) -> None:
    out = _decode(list_directory({"path": str(tmp_path / "nope")}, ""))
    _assert_hint_envelope(out)


# ---------------------------------------------------------------------
# 0.7 desktop_create_file / web_fetch
# ---------------------------------------------------------------------


def test_desktop_create_file_missing_name_returns_hint() -> None:
    out = _decode(desktop_create_file({"content": "hi"}, ""))
    _assert_hint_envelope(out, expect_hint_substr="name")


def test_desktop_create_file_invalid_content_type_returns_hint() -> None:
    """content as non-string (int) should error with hint."""
    out = _decode(desktop_create_file({"name": "test.txt", "content": 42}, ""))
    _assert_hint_envelope(out)


def test_desktop_create_file_path_traversal_returns_hint() -> None:
    out = _decode(desktop_create_file({"name": "../evil.txt", "content": "x"}, ""))
    _assert_hint_envelope(out)


def test_web_fetch_missing_url_returns_hint() -> None:
    out = _decode(web_fetch({}, ""))
    _assert_hint_envelope(out, expect_hint_substr="url")


def test_web_fetch_bad_scheme_returns_hint() -> None:
    out = _decode(web_fetch({"url": "file:///etc/passwd"}, ""))
    _assert_hint_envelope(out)
    assert "scheme" in out["error"] or "scheme" in out["hint"] or "http" in out["hint"]


# ---------------------------------------------------------------------
# 0.8 Integration — every os_tool with a sane "force-error" trigger
# ---------------------------------------------------------------------


def _force_error_trigger(tool_name: str) -> dict[str, Any]:
    """Return args guaranteed to trigger an error for the given tool.

    Strategy: pass empty args — every os_tool requires at least one
    field, so {} hits the missing-required branch.
    """
    return {}


def test_all_os_tools_error_have_hint_field() -> None:
    """Iterate every tool exported by os_tools and confirm the hint
    contract is upheld for an obvious error trigger."""
    tools: list[tuple[str, Callable[[dict, str], str]]] = [
        ("run_shell", os_tools.run_shell),
        ("write_file", os_tools.write_file),
        ("edit_file", os_tools.edit_file),
        ("read_file", os_tools.read_file),
        ("list_directory", os_tools.list_directory),
        ("desktop_create_file", os_tools.desktop_create_file),
        ("web_fetch", os_tools.web_fetch),
    ]
    for name, fn in tools:
        raw = fn(_force_error_trigger(name), "")
        out = _decode(raw)
        assert "error" in out, f"{name}: missing error field; got {out!r}"
        assert out.get("ok") is False, f"{name}: ok must be False, got {out!r}"
        assert isinstance(out.get("hint"), str) and out["hint"].strip(), (
            f"{name}: hint must be non-empty str, got {out.get('hint')!r}"
        )
        assert isinstance(out.get("examples"), list), (
            f"{name}: examples must be list, got {type(out.get('examples')).__name__}"
        )
