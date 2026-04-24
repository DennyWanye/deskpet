"""P4-S5: file_tools unit tests.

Focus areas:

* Happy-path read/write/glob/grep round-trip.
* Path-escape defence — absolute paths, ``..`` traversal, UNC paths,
  mixed-case ``C:/Windows/...``. All MUST come back as
  ``path outside workspace`` errors, with the handler NEVER touching
  real disk outside the workspace.

Each test points ``DESKPET_WORKSPACE_DIR`` at a fresh tmp directory so
the production ``%APPDATA%\\deskpet\\workspace\\`` stays untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.tools.registry import registry


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the workspace resolver at a disposable tmp dir."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(workspace))
    return workspace


# ---------------------------------------------------------------------
# file_write / file_read round-trip
# ---------------------------------------------------------------------
def test_write_then_read_roundtrip(sandbox: Path):
    wres = json.loads(
        registry.dispatch("file_write", {"path": "a.txt", "content": "hi\n"})
    )
    assert wres["bytes_written"] == 3
    rres = json.loads(registry.dispatch("file_read", {"path": "a.txt"}))
    assert rres["content"] == "hi\n"
    assert rres["lines_read"] == 1


def test_write_creates_parent_dirs(sandbox: Path):
    res = json.loads(
        registry.dispatch(
            "file_write", {"path": "nested/dir/b.txt", "content": "x"}
        )
    )
    assert res["bytes_written"] == 1
    assert (sandbox / "nested" / "dir" / "b.txt").is_file()


def test_append_mode_accumulates(sandbox: Path):
    registry.dispatch("file_write", {"path": "x.txt", "content": "1\n"})
    registry.dispatch(
        "file_write", {"path": "x.txt", "content": "2\n", "mode": "append"}
    )
    r = json.loads(registry.dispatch("file_read", {"path": "x.txt"}))
    assert r["content"] == "1\n2\n"


def test_read_offset_and_limit(sandbox: Path):
    payload = "".join(f"line{i}\n" for i in range(10))
    registry.dispatch("file_write", {"path": "lines.txt", "content": payload})
    r = json.loads(
        registry.dispatch(
            "file_read", {"path": "lines.txt", "offset": 3, "limit": 2}
        )
    )
    assert r["content"] == "line3\nline4\n"
    assert r["lines_read"] == 2


def test_read_missing_file(sandbox: Path):
    r = json.loads(registry.dispatch("file_read", {"path": "ghost.txt"}))
    assert "error" in r
    assert r["retriable"] is False


def test_write_rejects_non_string_content(sandbox: Path):
    r = json.loads(
        registry.dispatch("file_write", {"path": "a", "content": 123})
    )
    assert r["error"] == "content must be a string"


def test_write_rejects_invalid_mode(sandbox: Path):
    r = json.loads(
        registry.dispatch(
            "file_write", {"path": "a", "content": "x", "mode": "zap"}
        )
    )
    assert "invalid mode" in r["error"]


# ---------------------------------------------------------------------
# Path-escape defence (requirements: safe file workspace)
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "evil",
    [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system.ini",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "C:\\Windows\\system.ini",
        "\\\\server\\share\\file.txt",
        "//server/share/file.txt",
        "D:\\Users\\victim\\secret.txt",
    ],
)
def test_read_rejects_escaping_paths(sandbox: Path, evil: str):
    r = json.loads(registry.dispatch("file_read", {"path": evil}))
    assert r == {"error": "path outside workspace", "retriable": False}


@pytest.mark.parametrize(
    "evil",
    [
        "../../outside.txt",
        "..\\..\\outside.txt",
        "/tmp/x",
        "C:/Windows/host.ini",
    ],
)
def test_write_rejects_escaping_paths(sandbox: Path, evil: str):
    r = json.loads(
        registry.dispatch("file_write", {"path": evil, "content": "evil"})
    )
    assert r["error"] == "path outside workspace"
    # Defensive: verify nothing landed anywhere near sandbox parent.
    assert not list(sandbox.parent.glob("outside.txt"))


def test_glob_rejects_escaping_root(sandbox: Path):
    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "*", "root": "../.."})
    )
    assert r["error"] == "path outside workspace"


def test_grep_rejects_escaping_path(sandbox: Path):
    r = json.loads(
        registry.dispatch(
            "file_grep",
            {"pattern": "root", "path": "/etc/passwd"},
        )
    )
    assert r["error"] == "path outside workspace"


def test_relative_dot_slash_is_allowed(sandbox: Path):
    """``./foo.txt`` is benign — it's the workspace root."""
    registry.dispatch("file_write", {"path": "./foo.txt", "content": "ok"})
    r = json.loads(registry.dispatch("file_read", {"path": "./foo.txt"}))
    assert r["content"] == "ok"


# ---------------------------------------------------------------------
# file_glob
# ---------------------------------------------------------------------
def test_glob_finds_files(sandbox: Path):
    (sandbox / "a.md").write_text("x")
    (sandbox / "dir").mkdir()
    (sandbox / "dir" / "b.md").write_text("y")
    (sandbox / "c.txt").write_text("z")
    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "**/*.md"})
    )
    assert sorted(r["matches"]) == ["a.md", "dir/b.md"]
    assert r["count"] == 2


def test_glob_missing_root_returns_empty(sandbox: Path):
    r = json.loads(
        registry.dispatch(
            "file_glob", {"pattern": "*", "root": "no-such-dir"}
        )
    )
    assert r == {"matches": [], "count": 0}


# ---------------------------------------------------------------------
# file_grep
# ---------------------------------------------------------------------
def test_grep_returns_matching_lines(sandbox: Path):
    (sandbox / "log.txt").write_text(
        "info: starting\nerror: fatal\nwarn: mild\nerror: again\n"
    )
    r = json.loads(
        registry.dispatch(
            "file_grep", {"pattern": "error", "path": "log.txt"}
        )
    )
    assert r["count"] == 2
    assert r["matches"][0]["line"] == 2
    assert "fatal" in r["matches"][0]["text"]
    assert r["matches"][1]["line"] == 4


def test_grep_max_matches_caps_output(sandbox: Path):
    (sandbox / "log.txt").write_text("x\n" * 100)
    r = json.loads(
        registry.dispatch(
            "file_grep",
            {"pattern": "x", "path": "log.txt", "max_matches": 5},
        )
    )
    assert r["count"] == 5


def test_grep_invalid_regex(sandbox: Path):
    (sandbox / "f.txt").write_text("hi")
    r = json.loads(
        registry.dispatch(
            "file_grep", {"pattern": "[unterminated", "path": "f.txt"}
        )
    )
    assert r["error"].startswith("invalid regex")
