"""P4-S20 Wave 1a: OS-tools TDD tests.

Covers spec `os-tools` for all 7 tools. Tests instantiate handlers
directly (sync calls) — permission gating is tested separately in
test_p4s20_tool_registry_v2.py via execute_tool().
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from deskpet.tools.os_tools import (
    desktop_create_file,
    edit_file,
    list_directory,
    read_file,
    run_shell,
    web_fetch,
    write_file,
)


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------


def test_read_file_text(tmp_dir: Path) -> None:
    p = tmp_dir / "note.txt"
    p.write_text("milk\neggs", encoding="utf-8")
    out = json.loads(read_file({"path": str(p)}, ""))
    assert out["content"] == "milk\neggs"
    assert out["lines"] == 2
    assert out["truncated"] is False


def test_read_file_missing(tmp_dir: Path) -> None:
    out = json.loads(read_file({"path": str(tmp_dir / "no.txt")}, ""))
    assert out["error"] == "FileNotFoundError"


def test_read_file_offset_limit(tmp_dir: Path) -> None:
    p = tmp_dir / "big.txt"
    p.write_text("\n".join(f"line{i}" for i in range(1000)), encoding="utf-8")
    out = json.loads(
        read_file({"path": str(p), "offset": 100, "limit": 50}, "")
    )
    assert out["truncated"] is True
    assert out["content"].startswith("line100")
    assert out["lines"] == 50


# ---------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------


def test_write_file_create(tmp_dir: Path) -> None:
    target = tmp_dir / "sub" / "note.txt"
    out = json.loads(
        write_file({"path": str(target), "content": "hello"}, "")
    )
    assert out["bytes_written"] == 5
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_file_refuses_overwrite(tmp_dir: Path) -> None:
    p = tmp_dir / "exists.txt"
    p.write_text("old", encoding="utf-8")
    out = json.loads(
        write_file({"path": str(p), "content": "new"}, "")
    )
    assert out["error"] == "FileExistsError"
    assert p.read_text(encoding="utf-8") == "old"  # unchanged


def test_write_file_overwrite_flag(tmp_dir: Path) -> None:
    p = tmp_dir / "exists.txt"
    p.write_text("old", encoding="utf-8")
    out = json.loads(
        write_file(
            {"path": str(p), "content": "new", "overwrite": True}, ""
        )
    )
    assert "bytes_written" in out
    assert p.read_text(encoding="utf-8") == "new"


# ---------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------


def test_edit_file_single(tmp_dir: Path) -> None:
    p = tmp_dir / "doc.txt"
    p.write_text("foo bar baz", encoding="utf-8")
    out = json.loads(
        edit_file(
            {"path": str(p), "old_string": "bar", "new_string": "BAR"},
            "",
        )
    )
    assert out["replacements"] == 1
    assert p.read_text(encoding="utf-8") == "foo BAR baz"


def test_edit_file_not_unique_fails(tmp_dir: Path) -> None:
    p = tmp_dir / "doc.txt"
    p.write_text("x x x", encoding="utf-8")
    out = json.loads(
        edit_file(
            {"path": str(p), "old_string": "x", "new_string": "y"}, ""
        )
    )
    assert "not unique" in out["error"]
    assert p.read_text(encoding="utf-8") == "x x x"  # unchanged


def test_edit_file_replace_all(tmp_dir: Path) -> None:
    p = tmp_dir / "doc.txt"
    p.write_text("x x x", encoding="utf-8")
    out = json.loads(
        edit_file(
            {
                "path": str(p),
                "old_string": "x",
                "new_string": "y",
                "replace_all": True,
            },
            "",
        )
    )
    assert out["replacements"] == 3
    assert p.read_text(encoding="utf-8") == "y y y"


# ---------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------


def test_list_directory_basic(tmp_dir: Path) -> None:
    (tmp_dir / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_dir / "sub").mkdir()
    out = json.loads(list_directory({"path": str(tmp_dir)}, ""))
    names = sorted(e["name"] for e in out["entries"])
    assert names == ["a.txt", "sub"]
    types = {e["name"]: e["type"] for e in out["entries"]}
    assert types["a.txt"] == "file"
    assert types["sub"] == "dir"


def test_list_directory_truncates(tmp_dir: Path) -> None:
    for i in range(150):
        (tmp_dir / f"f{i}.txt").write_text(".", encoding="utf-8")
    out = json.loads(
        list_directory({"path": str(tmp_dir), "max_entries": 100}, "")
    )
    assert out["truncated"] is True
    assert len(out["entries"]) == 100


# ---------------------------------------------------------------------
# run_shell
# ---------------------------------------------------------------------


def test_run_shell_success() -> None:
    if platform.system() == "Windows":
        cmd = "cmd /c echo hello"
    else:
        cmd = "echo hello"
    out = json.loads(run_shell({"command": cmd, "timeout": 5}, ""))
    assert out["exit_code"] == 0
    assert "hello" in out["stdout"]


def test_run_shell_timeout() -> None:
    if platform.system() == "Windows":
        cmd = "cmd /c ping 127.0.0.1 -n 5"
    else:
        cmd = "sleep 5"
    out = json.loads(run_shell({"command": cmd, "timeout": 1}, ""))
    assert out.get("error") == "timeout"


# ---------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------


def test_web_fetch_refuses_non_http() -> None:
    out = json.loads(web_fetch({"url": "file:///etc/passwd"}, ""))
    assert "scheme" in out["error"]


def test_web_fetch_refuses_ftp() -> None:
    out = json.loads(web_fetch({"url": "ftp://example.com"}, ""))
    assert "scheme" in out["error"]


# ---------------------------------------------------------------------
# desktop_create_file
# ---------------------------------------------------------------------


def test_desktop_create_file_resolves_to_desktop(tmp_dir: Path, monkeypatch) -> None:
    """Use a fake HOME so the test is hermetic."""
    fake_home = tmp_dir / "fakeuser"
    desktop = fake_home / "Desktop"
    desktop.mkdir(parents=True)
    if platform.system() == "Windows":
        monkeypatch.setenv("USERPROFILE", str(fake_home))
    else:
        monkeypatch.setenv("HOME", str(fake_home))

    out = json.loads(
        desktop_create_file(
            {"name": "todo.txt", "content": "milk"}, ""
        )
    )
    p = Path(out["path"])
    assert p.name == "todo.txt"
    assert p.parent == desktop or str(p.parent).endswith("Desktop")
    assert p.read_text(encoding="utf-8") == "milk"


def test_desktop_create_file_utf8(tmp_dir: Path, monkeypatch) -> None:
    fake_home = tmp_dir / "fakeuser2"
    (fake_home / "Desktop").mkdir(parents=True)
    if platform.system() == "Windows":
        monkeypatch.setenv("USERPROFILE", str(fake_home))
    else:
        monkeypatch.setenv("HOME", str(fake_home))

    out = json.loads(
        desktop_create_file(
            {"name": "购物.txt", "content": "吃饭买菜"}, ""
        )
    )
    p = Path(out["path"])
    assert p.read_text(encoding="utf-8") == "吃饭买菜"
    assert p.name == "购物.txt"
