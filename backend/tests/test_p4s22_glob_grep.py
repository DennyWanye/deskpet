# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 — glob + grep tools (filesystem-only, no LLM/network needed)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.tools.code_tools.glob_tool import glob_tool
from deskpet.tools.code_tools.grep_tool import grep_tool


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small fixture project tree for both glob and grep tests."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def hello():\n    return 'world'\n", encoding="utf-8"
    )
    (tmp_path / "src" / "utils.py").write_text(
        "import os\nprint('hi')\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_main.py").write_text(
        "def test_hello():\n    assert hello() == 'world'\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# proj\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------


def test_glob_finds_py_files_under_root(tree):
    out = json.loads(glob_tool({"pattern": "**/*.py", "_project_root": str(tree)}))
    files = out["files"]
    assert any(f.endswith("main.py") for f in files)
    assert any(f.endswith("utils.py") for f in files)
    assert any(f.endswith("test_main.py") for f in files)
    # README.md NOT in py glob
    assert not any(f.endswith("README.md") for f in files)


def test_glob_respects_explicit_path_override(tree, tmp_path):
    elsewhere = tmp_path / "outside"
    elsewhere.mkdir()
    (elsewhere / "x.py").write_text("", encoding="utf-8")
    # _project_root injected as `tree`, but caller passes `path` explicitly
    out = json.loads(
        glob_tool({"pattern": "*.py", "path": str(elsewhere), "_project_root": str(tree)})
    )
    files = out["files"]
    assert len(files) == 1
    assert files[0].endswith("x.py")


def test_glob_zero_matches_returns_empty(tree):
    out = json.loads(glob_tool({"pattern": "**/*.zzz", "_project_root": str(tree)}))
    assert out["count"] == 0
    assert out["files"] == []


def test_glob_requires_pattern(tree):
    out = json.loads(glob_tool({"_project_root": str(tree)}))
    assert "error" in out


def test_glob_handles_missing_root():
    out = json.loads(glob_tool({"pattern": "*.py"}))
    assert "error" in out


def test_glob_mtime_descending(tree):
    """Newest-first ordering — touch tests/test_main.py with a clearly
    later mtime than all the rest, then assert it's first."""
    import os, time
    target = tree / "tests" / "test_main.py"
    # Filesystems on Windows can have ~1s mtime resolution; sleep a
    # touch over that to guarantee monotonic ordering.
    time.sleep(1.1)
    target.touch()
    out = json.loads(glob_tool({"pattern": "**/*.py", "_project_root": str(tree)}))
    assert out["files"][0].endswith("test_main.py")


# ---------------------------------------------------------------------------
# grep — files_with_matches mode
# ---------------------------------------------------------------------------


def test_grep_default_mode_finds_files(tree):
    out = json.loads(
        grep_tool({"pattern": "hello", "_project_root": str(tree)})
    )
    assert out["files_with_matches"] >= 2
    files = out["files"]
    assert any("main.py" in f for f in files)
    assert any("test_main.py" in f for f in files)


def test_grep_glob_filter(tree):
    """Restrict to *.py — README would never match anyway, but Markdown
    headers in nested folders shouldn't match."""
    (tree / "doc.md").write_text("hello world", encoding="utf-8")
    out = json.loads(
        grep_tool(
            {"pattern": "hello", "glob": "*.py", "_project_root": str(tree)}
        )
    )
    assert all(".py" in f for f in out["files"])


# ---------------------------------------------------------------------------
# grep — content mode
# ---------------------------------------------------------------------------


def test_grep_content_mode_returns_lines(tree):
    out = json.loads(
        grep_tool(
            {
                "pattern": "hello",
                "_project_root": str(tree),
                "output_mode": "content",
            }
        )
    )
    assert "matches" in out
    matches = out["matches"]
    # Each file's match list has line numbers + text
    for _f, lst in matches.items():
        for entry in lst:
            assert "line" in entry
            assert "text" in entry


def test_grep_content_with_context(tree):
    out = json.loads(
        grep_tool(
            {
                "pattern": "hello",
                "_project_root": str(tree),
                "output_mode": "content",
                "context": 1,
            }
        )
    )
    # Context expands lines around the match → expect at least one
    # entry that's NOT itself a match.
    any_context_line = False
    for _f, lst in out["matches"].items():
        match_lines = [
            e["text"] for e in lst if "hello" in e["text"]
        ]
        non_match_lines = [
            e["text"] for e in lst if "hello" not in e["text"]
        ]
        if non_match_lines:
            any_context_line = True
            break
    assert any_context_line


# ---------------------------------------------------------------------------
# grep — count mode
# ---------------------------------------------------------------------------


def test_grep_count_mode(tree):
    out = json.loads(
        grep_tool(
            {
                "pattern": "hello",
                "_project_root": str(tree),
                "output_mode": "count",
            }
        )
    )
    assert "counts" in out
    # main.py contains "hello" twice (def hello + return 'world' has no
    # 'hello' so just once... but def hello() says hello).
    # Just verify that it found > 0 in some file.
    assert any(v >= 1 for v in out["counts"].values())


# ---------------------------------------------------------------------------
# grep — flags
# ---------------------------------------------------------------------------


def test_grep_case_insensitive(tree):
    (tree / "case_test.py").write_text("HELLO\nhello\n", encoding="utf-8")
    out = json.loads(
        grep_tool(
            {
                "pattern": "hello",
                "_project_root": str(tree),
                "case_insensitive": True,
                "output_mode": "count",
            }
        )
    )
    found_path = None
    for f, c in out["counts"].items():
        if "case_test.py" in f and c == 2:
            found_path = f
            break
    assert found_path is not None


def test_grep_invalid_regex_returns_error(tree):
    out = json.loads(
        grep_tool({"pattern": "[invalid", "_project_root": str(tree)})
    )
    assert "error" in out
