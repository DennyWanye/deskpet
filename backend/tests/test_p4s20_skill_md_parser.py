"""P4-S20 Wave 3a: Claude Code SKILL.md v1 parser tests."""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest

from deskpet.skills.parser.parse_skill_md import (
    ClaudeSkillMeta,
    SkillParseError,
    parse_skill_md,
    render_body,
)


# ---------------------------------------------------------------------
# parse_skill_md
# ---------------------------------------------------------------------


def _write_skill(tmp_path: Path, name: str, content: str) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    target = skill_dir / "SKILL.md"
    target.write_text(content, encoding="utf-8")
    return target


def test_minimal_skill(tmp_path: Path) -> None:
    p = _write_skill(
        tmp_path,
        "foo",
        "---\ndescription: bar\n---\nthis is the body",
    )
    meta = parse_skill_md(p)
    assert meta.name == "foo"  # defaults to dir name
    assert meta.description == "bar"
    assert meta.body_markdown == "this is the body"
    assert meta.source == "claude-code-v1"
    assert meta.user_invocable is True
    assert meta.disable_model_invocation is False


def test_missing_frontmatter_raises(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, "foo", "no frontmatter here")
    with pytest.raises(SkillParseError, match="missing YAML frontmatter"):
        parse_skill_md(p)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    # Properly delimited frontmatter, but the YAML inside is broken
    # (a value that starts a flow-mapping `{` but never closes).
    p = _write_skill(
        tmp_path,
        "foo",
        "---\ndescription: { unclosed mapping\n---\nbody",
    )
    with pytest.raises(SkillParseError, match="invalid YAML"):
        parse_skill_md(p)


def test_all_v1_fields_parsed(tmp_path: Path) -> None:
    p = _write_skill(
        tmp_path,
        "git-helper",
        "---\n"
        "name: git-commit-msg\n"
        "description: Generate commit messages\n"
        "when_to_use: Before running git commit\n"
        "argument-hint: <staged file path>\n"
        "disable-model-invocation: true\n"
        "user-invocable: false\n"
        "allowed-tools: [Read, Write, Bash]\n"
        "paths: ['**/*.py']\n"
        "version: 0.2.1\n"
        "---\n"
        "Body content",
    )
    meta = parse_skill_md(p)
    assert meta.name == "git-commit-msg"
    assert meta.description == "Generate commit messages"
    assert meta.when_to_use == "Before running git commit"
    assert meta.argument_hint == "<staged file path>"
    assert meta.disable_model_invocation is True
    assert meta.user_invocable is False
    assert meta.allowed_tools == ["Read", "Write", "Bash"]
    assert meta.paths == ["**/*.py"]
    assert meta.version == "0.2.1"


def test_allowed_tools_string_format(tmp_path: Path) -> None:
    p = _write_skill(
        tmp_path,
        "shellguy",
        "---\ndescription: t\nallowed-tools: \"Read Write Bash(git *)\"\n---\nb",
    )
    meta = parse_skill_md(p)
    assert meta.allowed_tools == ["Read", "Write", "Bash(git *)"]


def test_unknown_fields_preserved(tmp_path: Path) -> None:
    p = _write_skill(
        tmp_path,
        "future",
        "---\ndescription: t\nfuture-field: xyz\nanother: 42\n---\nbody",
    )
    meta = parse_skill_md(p)
    assert meta.raw_frontmatter.get("future-field") == "xyz"
    assert meta.raw_frontmatter.get("another") == 42


# ---------------------------------------------------------------------
# render_body — variable substitution
# ---------------------------------------------------------------------


def test_render_substitutes_skill_dir(tmp_path: Path) -> None:
    body = "Read ${CLAUDE_SKILL_DIR}/template.md please"
    out = render_body(
        body, skill_dir=tmp_path, session_id="s1", args=[]
    )
    assert str(tmp_path) in out
    assert "${CLAUDE_SKILL_DIR}" not in out


def test_render_substitutes_session_id(tmp_path: Path) -> None:
    body = "session=${CLAUDE_SESSION_ID}"
    out = render_body(
        body, skill_dir=tmp_path, session_id="ABC", args=[]
    )
    assert out == "session=ABC"


def test_render_substitutes_arguments(tmp_path: Path) -> None:
    body = "First arg: $0, all: $ARGUMENTS, second: $1"
    out = render_body(
        body, skill_dir=tmp_path, session_id="s1",
        args=["hello world", "second"],
    )
    assert "hello world" in out
    assert "second" in out
    # $ARGUMENTS is the joined string
    assert "hello world second" in out or "hello world\nsecond" in out


def test_render_quoted_argument_preserved(tmp_path: Path) -> None:
    body = "$0 then $1"
    out = render_body(
        body, skill_dir=tmp_path, session_id="s1",
        args=["hello world", "second"],
    )
    assert out == "hello world then second"


def test_render_arguments_indexed(tmp_path: Path) -> None:
    body = "[$ARGUMENTS[0]] - [$ARGUMENTS[1]]"
    out = render_body(
        body, skill_dir=tmp_path, session_id="s1",
        args=["alpha", "beta"],
    )
    assert "alpha" in out
    assert "beta" in out


# ---------------------------------------------------------------------
# Inline shell injection !`...`
# ---------------------------------------------------------------------


def test_render_inline_shell_basic(tmp_path: Path) -> None:
    if platform.system() == "Windows":
        body = "Today: !`cmd /c echo hi`"
    else:
        body = "Today: !`echo hi`"
    out = render_body(
        body, skill_dir=tmp_path, session_id="s1", args=[]
    )
    assert "hi" in out
    assert "!`" not in out


def test_render_inline_shell_failure_inlined(tmp_path: Path) -> None:
    body = "x: !`this_command_does_not_exist_xyz`"
    out = render_body(
        body, skill_dir=tmp_path, session_id="s1", args=[]
    )
    assert "[command failed:" in out
