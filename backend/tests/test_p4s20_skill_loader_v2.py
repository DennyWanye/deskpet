"""P4-S20 Wave 3b: dual-format SkillLoader tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.skills.loader import SkillLoader


def _write(dir_: Path, name: str, content: str) -> Path:
    sd = dir_ / name
    sd.mkdir()
    p = sd / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_legacy_format_still_loads(tmp_path: Path) -> None:
    """Legacy format with version+author keeps working unchanged."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    _write(
        builtin,
        "voice",
        "---\n"
        "name: voice\n"
        "description: voice helper\n"
        "version: 1.0\n"
        "author: deskpet\n"
        "---\n"
        "body",
    )
    loader = SkillLoader([builtin, user], enable_watch=False)
    loader.reload()
    metas = loader.list_metas()
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "voice"
    assert m.source_format == "deskpet-legacy"
    assert m.disable_model_invocation is False
    assert m.user_invocable is True


def test_v1_format_loads(tmp_path: Path) -> None:
    """Claude Code v1 format (no version/author) loads via parser."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    _write(
        user,
        "summarize",
        "---\n"
        "description: summarize text\n"
        "when_to_use: when user asks for a summary\n"
        "allowed-tools: [Read, Write]\n"
        "disable-model-invocation: true\n"
        "---\n"
        "Summarize $ARGUMENTS",
    )
    loader = SkillLoader([builtin, user], enable_watch=False)
    loader.reload()
    metas = loader.list_metas()
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "summarize"  # defaults to dir name
    assert m.description == "summarize text"
    assert m.source_format == "claude-code-v1"
    assert m.when_to_use == "when user asks for a summary"
    assert m.allowed_tools == ["Read", "Write"]
    assert m.disable_model_invocation is True


def test_mixed_dirs_both_load(tmp_path: Path) -> None:
    """Built-in legacy + user v1 both surface in list_metas."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    _write(
        builtin,
        "legacy-skill",
        "---\nname: legacy-skill\ndescription: legacy\n"
        "version: 1.0\nauthor: deskpet\n---\nbody",
    )
    _write(
        user,
        "v1-skill",
        "---\ndescription: a v1 skill\n---\nbody",
    )
    loader = SkillLoader([builtin, user], enable_watch=False)
    loader.reload()
    metas = sorted(loader.list_metas(), key=lambda m: m.name)
    assert [m.name for m in metas] == ["legacy-skill", "v1-skill"]
    formats = {m.name: m.source_format for m in metas}
    assert formats["legacy-skill"] == "deskpet-legacy"
    assert formats["v1-skill"] == "claude-code-v1"


def test_v1_invalid_yaml_skipped(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    _write(
        user,
        "broken",
        "---\ndescription: { unclosed\n---\nbody",
    )
    _write(
        user,
        "good",
        "---\ndescription: ok\n---\nbody",
    )
    loader = SkillLoader([builtin, user], enable_watch=False)
    loader.reload()
    names = sorted(m.name for m in loader.list_metas())
    assert names == ["good"]


def test_legacy_missing_name_still_skipped(tmp_path: Path) -> None:
    """Legacy strictness preserved — missing name + has version+author → skipped."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    _write(
        builtin,
        "bad",
        "---\ndescription: x\nversion: 1.0\nauthor: a\n---\nb",
    )
    _write(
        builtin,
        "ok",
        "---\nname: ok\ndescription: x\nversion: 1.0\nauthor: a\n---\nb",
    )
    loader = SkillLoader([builtin, user], enable_watch=False)
    loader.reload()
    names = sorted(m.name for m in loader.list_metas())
    assert names == ["ok"]
