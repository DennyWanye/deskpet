# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-CC-3: run-deskpet / verify-deskpet bundled engineering skills.

These are knowledge-snippet skills (``user-invocable: false``) meant for
dev/test agents, not end users. Tests assert:

  1. Both SKILL.md files exist in the real bundled builtin dir and parse
     with the expected frontmatter (name, user_invocable=False, triggers).
  2. BC: with the default loader (``knowledge_enabled=False``) the
     ``user-invocable: false`` skills stay OUT of the loader snapshot — so
     /help, skill_invoke, and description lists are byte-identical.
  3. With ``knowledge_enabled=True`` they DO appear (knowledge gating works).
  4. The skill bodies carry the engineering recipe content (smoke check).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.skills.loader import SkillLoader

# Real bundled builtin dir (the one main.py points dir[0] at).
import deskpet.skills.builtin as _builtin_pkg

_BUILTIN_DIR = Path(_builtin_pkg.__file__).parent


@pytest.mark.parametrize("name", ["run-deskpet", "verify-deskpet"])
def test_skill_md_exists(name: str) -> None:
    p = _BUILTIN_DIR / name / "SKILL.md"
    assert p.is_file(), f"missing bundled skill: {p}"


def test_frontmatter_parses_user_invocable_false(tmp_path: Path) -> None:
    """With knowledge gating ON, both skills load with user_invocable=False."""
    loader = SkillLoader(
        [_BUILTIN_DIR, tmp_path / "user"],
        enable_watch=False,
        knowledge_enabled=True,
    )
    loader.reload()
    by_name = {m.name: m for m in loader.list_metas()}

    for name in ("run-deskpet", "verify-deskpet"):
        assert name in by_name, f"{name} not loaded with knowledge_enabled=True"
        m = by_name[name]
        assert m.user_invocable is False
        assert m.scope == "built-in"
        assert m.description  # non-empty
        assert m.triggers, f"{name} should declare triggers"


def test_bc_hidden_when_knowledge_disabled(tmp_path: Path) -> None:
    """Default loader (knowledge_enabled=False) keeps them out of snapshot."""
    loader = SkillLoader(
        [_BUILTIN_DIR, tmp_path / "user"],
        enable_watch=False,
        knowledge_enabled=False,  # default
    )
    loader.reload()
    names = {m.name for m in loader.list_metas()}
    assert "run-deskpet" not in names
    assert "verify-deskpet" not in names


def test_bodies_carry_recipe_content(tmp_path: Path) -> None:
    """Smoke-check the skill bodies inline the engineering recipe."""
    loader = SkillLoader(
        [_BUILTIN_DIR, tmp_path / "user"],
        enable_watch=False,
        knowledge_enabled=True,
    )
    loader.reload()

    run_body = loader.read_body("run-deskpet")
    assert "DESKPET_BACKEND_DIR" in run_body
    assert "tauri dev" in run_body.lower()

    verify_body = loader.read_body("verify-deskpet")
    assert "SendInput" in verify_body
    # SOP must reference real-coordinate click + log assertion discipline.
    assert "截图" in verify_body or "Screenshot" in verify_body
