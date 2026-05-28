# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 — project root resolution + path safety."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from deskpet.code_mode import resolve_project_root, sanitize_project_name
from deskpet.code_mode.project_root import assert_within_project_root


# ---------------------------------------------------------------------------
# sanitize_project_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hello-world", "hello-world"),
        ("Hello World", "Hello-World"),
        ("hello/world", "helloworld"),
        ("..\\..\\evil", "evil"),
        ("中文项目", "中文项目"),
        ("", ""),
        ("   ", ""),
        ("!!!@@@###", ""),
        ("a" * 100, "a" * 60),
    ],
)
def test_sanitize_project_name(raw, expected):
    assert sanitize_project_name(raw) == expected


# ---------------------------------------------------------------------------
# resolve_project_root — explicit user path
# ---------------------------------------------------------------------------


def test_explicit_user_path_creates_dir(tmp_path):
    target = tmp_path / "my-project"
    assert not target.exists()
    out = resolve_project_root(str(target), "ignored")
    assert out == target.resolve()
    assert out.is_dir()


def test_explicit_user_path_keeps_existing_files(tmp_path):
    """User picks a folder that already has stuff — we don't touch it."""
    target = tmp_path / "existing"
    target.mkdir()
    (target / "myfile.py").write_text("x = 1", encoding="utf-8")
    out = resolve_project_root(str(target), "ignored")
    assert (out / "myfile.py").read_text(encoding="utf-8") == "x = 1"


# ---------------------------------------------------------------------------
# resolve_project_root — auto-create fallback
# ---------------------------------------------------------------------------


def test_auto_create_with_seeded_readme(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    out = resolve_project_root(None, "MyApp")
    expected = tmp_path / "projects" / "MyApp"
    assert out == expected.resolve()
    assert (out / "README.md").exists()
    text = (out / "README.md").read_text(encoding="utf-8")
    assert "MyApp" in text


def test_auto_create_falls_back_to_untitled_for_empty_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    out = resolve_project_root(None, "")
    assert out.name == "untitled"


def test_auto_create_falls_back_for_unsafe_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    out = resolve_project_root(None, "!!!@@@###")
    # All chars stripped → empty → "untitled"
    assert out.name == "untitled"


def test_auto_create_idempotent_on_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    a = resolve_project_root(None, "twice")
    # Modify the readme to detect clobber
    (a / "README.md").write_text("user edited!", encoding="utf-8")
    b = resolve_project_root(None, "twice")
    assert a == b
    assert (b / "README.md").read_text(encoding="utf-8") == "user edited!"


# ---------------------------------------------------------------------------
# assert_within_project_root
# ---------------------------------------------------------------------------


def test_within_root_passes(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "sub" / "file.py"
    out = assert_within_project_root(target, proj)
    # Returns resolved path
    assert out == target.resolve()


def test_outside_root_rejected(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    sibling = tmp_path / "elsewhere" / "secret.txt"
    with pytest.raises(ValueError, match="escapes project root"):
        assert_within_project_root(sibling, proj)


def test_dotdot_traversal_rejected(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    escape = proj / ".." / "secret"
    with pytest.raises(ValueError, match="escapes project root"):
        assert_within_project_root(escape, proj)


def test_root_itself_passes(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    assert assert_within_project_root(proj, proj) == proj.resolve()
