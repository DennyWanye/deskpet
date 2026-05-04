"""P4-S20 Wave 4a: Skill marketplace TDD tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from deskpet.skills.marketplace.installer import (
    SkillInstaller,
    StagedSkill,
    parse_github_url,
)
from deskpet.skills.marketplace.registry_client import RegistryClient
from deskpet.skills.marketplace.safety import (
    SafetyError,
    validate_manifest,
)


# ---------------------------------------------------------------------
# Registry client
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_client_fetches_listing() -> None:
    body = {
        "skills": [
            {
                "name": "git-helper",
                "description": "git commit msg helper",
                "source_url": "github:nan/git-helper",
                "permission_categories": ["read_file", "shell"],
            }
        ]
    }

    def _h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = RegistryClient(
        url="https://example.com/registry.json",
        cache_ttl_s=60,
        transport=httpx.MockTransport(_h),
    )
    out = await client.fetch()
    assert len(out["skills"]) == 1
    assert out["skills"][0]["name"] == "git-helper"


@pytest.mark.asyncio
async def test_registry_client_returns_error_on_unreachable() -> None:
    def _h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = RegistryClient(
        url="https://example.com/registry.json",
        cache_ttl_s=60,
        transport=httpx.MockTransport(_h),
    )
    out = await client.fetch()
    assert out["skills"] == []
    assert "error" in out


@pytest.mark.asyncio
async def test_registry_client_caches() -> None:
    calls = {"n": 0}

    def _h(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"skills": []})

    client = RegistryClient(
        url="https://example.com/registry.json",
        cache_ttl_s=60,
        transport=httpx.MockTransport(_h),
    )
    await client.fetch()
    await client.fetch()
    await client.fetch()
    assert calls["n"] == 1  # cache hit on subsequent calls


# ---------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------


def test_parse_github_shorthand() -> None:
    spec = parse_github_url("github:foo/bar")
    assert spec.owner == "foo"
    assert spec.repo == "bar"
    assert spec.subpath is None
    assert spec.git_url == "https://github.com/foo/bar.git"


def test_parse_github_https() -> None:
    spec = parse_github_url("https://github.com/foo/bar")
    assert spec.owner == "foo"
    assert spec.repo == "bar"


def test_parse_github_subpath_tree() -> None:
    spec = parse_github_url("github:foo/bar/tree/main/web-search")
    assert spec.owner == "foo"
    assert spec.repo == "bar"
    assert spec.subpath == "web-search"
    assert spec.branch == "main"


def test_parse_github_https_subpath() -> None:
    spec = parse_github_url(
        "https://github.com/anthropics/skills/tree/main/web-search"
    )
    assert spec.subpath == "web-search"
    assert spec.branch == "main"


def test_parse_github_ssh() -> None:
    spec = parse_github_url("git@github.com:foo/bar")
    assert spec.owner == "foo"
    assert spec.repo == "bar"
    assert spec.git_url == "git@github.com:foo/bar.git"


def test_parse_github_invalid() -> None:
    with pytest.raises(ValueError):
        parse_github_url("not-a-github-url")


# ---------------------------------------------------------------------
# Manifest safety
# ---------------------------------------------------------------------


def test_safety_unknown_tool_rejected() -> None:
    manifest = {
        "name": "evil",
        "description": "malicious",
        "tools": ["read_file", "exec_arbitrary_pyc"],
    }
    known = {"read_file", "write_file", "shell"}
    with pytest.raises(SafetyError, match="unknown tool"):
        validate_manifest(manifest, known_tools=known)


def test_safety_known_tools_accepted() -> None:
    manifest = {
        "name": "ok",
        "description": "fine",
        "tools": ["read_file"],
        "permission_categories": ["read_file"],
    }
    validate_manifest(manifest, known_tools={"read_file"})


def test_safety_missing_name_rejected() -> None:
    manifest = {"description": "no name"}
    with pytest.raises(SafetyError, match="name"):
        validate_manifest(manifest, known_tools=set())


# ---------------------------------------------------------------------
# Installer (uses MockTransport-style stub for git)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_installer_stage_writes_to_staging(tmp_path: Path) -> None:
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        # Simulate git clone — write SKILL.md + manifest.json into dest
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(
            "---\ndescription: test skill\n---\nbody",
            encoding="utf-8",
        )
        (dest / "manifest.json").write_text(
            json.dumps(
                {
                    "name": "test-skill",
                    "description": "test skill",
                    "tools": ["read_file"],
                    "permission_categories": ["read_file"],
                }
            ),
            encoding="utf-8",
        )

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    staged = await inst.stage("github:foo/test-skill")
    assert isinstance(staged, StagedSkill)
    assert staged.manifest["name"] == "test-skill"
    assert staged.staging_path.exists()


@pytest.mark.asyncio
async def test_installer_stage_unknown_tool_rejected(tmp_path: Path) -> None:
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(
            json.dumps(
                {
                    "name": "evil",
                    "description": ".",
                    "tools": ["evil_tool"],
                }
            ),
            encoding="utf-8",
        )

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    with pytest.raises(SafetyError):
        await inst.stage("github:foo/evil")
    # Staging dir must be cleaned up on rejection
    assert not (staging / "evil").exists()


@pytest.mark.asyncio
async def test_installer_finalize_moves_to_skills_dir(tmp_path: Path) -> None:
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(
            "---\ndescription: ok\n---\nbody", encoding="utf-8"
        )
        (dest / "manifest.json").write_text(
            json.dumps(
                {
                    "name": "ok-skill",
                    "description": "ok",
                    "tools": ["read_file"],
                }
            ),
            encoding="utf-8",
        )

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    staged = await inst.stage("github:foo/ok-skill")
    final_path = inst.finalize(staged)
    assert final_path == target / "ok-skill"
    assert (final_path / "SKILL.md").exists()
    assert not staged.staging_path.exists()  # cleaned


@pytest.mark.asyncio
async def test_installer_uninstall(tmp_path: Path) -> None:
    target = tmp_path / "user_skills"
    target.mkdir()
    skill_dir = target / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: x\n---\nb", encoding="utf-8")

    async def _no_clone(spec, dest): ...

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=tmp_path / "staging",
        known_tools=set(),
        clone_fn=_no_clone,
    )
    inst.uninstall("my-skill")
    assert not skill_dir.exists()


@pytest.mark.asyncio
async def test_installer_uninstall_rejects_path_traversal(tmp_path: Path) -> None:
    target = tmp_path / "user_skills"
    target.mkdir()

    async def _no_clone(spec, dest): ...

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=tmp_path / "staging",
        known_tools=set(),
        clone_fn=_no_clone,
    )
    with pytest.raises(ValueError, match="path"):
        inst.uninstall("../outside")
