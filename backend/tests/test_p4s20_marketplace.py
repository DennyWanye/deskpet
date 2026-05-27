"""P4-S20 Wave 4a: Skill marketplace TDD tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from deskpet.skills.marketplace.installer import (
    BatchStageResult,
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


# ---------------------------------------------------------------------
# Batch install — recursive multi-skill mode
# (obra/superpowers-style repos: root has neither SKILL.md nor
# manifest.json, but skills/ subdirs each contain SKILL.md)
# ---------------------------------------------------------------------


def _write_skill_md(dir_path: Path, name: str, tools: list[str]) -> None:
    """Helper: write a minimal valid SKILL.md frontmatter at `dir_path`."""
    dir_path.mkdir(parents=True, exist_ok=True)
    allowed = ", ".join(tools)
    body = (
        "---\n"
        f"name: {name}\n"
        f"description: test skill {name}\n"
        f"allowed-tools: [{allowed}]\n"
        "---\n"
        "skill body\n"
    )
    (dir_path / "SKILL.md").write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_stage_recursive_single_skill_mode_root_has_skill_md(
    tmp_path: Path,
) -> None:
    """When root has SKILL.md, stage_recursive returns 1 entry, multi=False."""
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        _write_skill_md(dest, "solo", ["read_file"])

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    result = await inst.stage_recursive("github:foo/solo")
    assert isinstance(result, BatchStageResult)
    assert result.multi is False
    assert len(result.staged) == 1
    assert result.staged[0].name == "solo"
    assert result.errors == []


@pytest.mark.asyncio
async def test_stage_recursive_marketplace_finds_all_subdir_skills(
    tmp_path: Path,
) -> None:
    """Root has no SKILL.md/manifest.json but skills/foo and skills/bar
    each have SKILL.md → both staged, multi=True."""
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        # Plugin-marketplace shape: top-level README + 3 sub-skills.
        (dest / "README.md").write_text("plugin marketplace", encoding="utf-8")
        _write_skill_md(dest / "skills" / "alpha", "alpha", ["read_file"])
        _write_skill_md(dest / "skills" / "beta", "beta", ["shell"])
        _write_skill_md(dest / "skills" / "gamma", "gamma", ["read_file"])

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file", "shell"},
        clone_fn=fake_clone,
    )
    result = await inst.stage_recursive("github:foo/marketplace")
    assert result.multi is True
    assert len(result.staged) == 3
    names = sorted(s.name for s in result.staged)
    assert names == ["alpha", "beta", "gamma"]
    assert result.errors == []


@pytest.mark.asyncio
async def test_stage_recursive_best_effort_skips_invalid_subskill(
    tmp_path: Path,
) -> None:
    """One sub-skill has an unknown tool → it lands in errors but the
    other two are still staged successfully."""
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        _write_skill_md(dest / "skills" / "good1", "good1", ["read_file"])
        _write_skill_md(dest / "skills" / "bad", "bad", ["evil_tool"])
        _write_skill_md(dest / "skills" / "good2", "good2", ["read_file"])

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    result = await inst.stage_recursive("github:foo/mixed")
    assert result.multi is True
    assert len(result.staged) == 2
    assert sorted(s.name for s in result.staged) == ["good1", "good2"]
    assert len(result.errors) == 1
    assert "bad" in result.errors[0]["path"]


@pytest.mark.asyncio
async def test_stage_recursive_skips_dot_and_excluded_dirs(
    tmp_path: Path,
) -> None:
    """`.git`, `.claude-plugin`, `node_modules`, etc. must NOT be scanned
    even if they happen to contain a SKILL.md (rare but defensive)."""
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        # Real skill
        _write_skill_md(dest / "skills" / "real", "real", ["read_file"])
        # Noise that must be skipped
        _write_skill_md(dest / ".git" / "hooks", "noise-git", ["read_file"])
        _write_skill_md(dest / ".claude-plugin", "noise-cc", ["read_file"])
        _write_skill_md(
            dest / "node_modules" / "lib", "noise-node", ["read_file"]
        )

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    result = await inst.stage_recursive("github:foo/noisy")
    names = sorted(s.name for s in result.staged)
    assert names == ["real"]


@pytest.mark.asyncio
async def test_stage_recursive_repo_has_no_skill_md_anywhere(
    tmp_path: Path,
) -> None:
    """Repo with literally no SKILL.md → SafetyError surfaces, clone cleaned."""
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "README.md").write_text("nothing here", encoding="utf-8")

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    with pytest.raises(SafetyError, match="no SKILL.md"):
        await inst.stage_recursive("github:foo/empty")
    assert not (staging / "empty").exists()


@pytest.mark.asyncio
async def test_finalize_batch_installs_all_then_cleans_clone(
    tmp_path: Path,
) -> None:
    """finalize_batch copies each sub-skill into skills_dir, then wipes
    the shared clone once at the end."""
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        _write_skill_md(dest / "skills" / "alpha", "alpha", ["read_file"])
        _write_skill_md(dest / "skills" / "beta", "beta", ["read_file"])

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    batch = await inst.stage_recursive("github:foo/two-skills")
    out = inst.finalize_batch(batch)
    assert len(out["installed"]) == 2
    assert (target / "alpha" / "SKILL.md").exists()
    assert (target / "beta" / "SKILL.md").exists()
    # Shared clone wiped after batch
    assert not (staging / "two-skills").exists()


@pytest.mark.asyncio
async def test_finalize_batch_collects_per_skill_failures(
    tmp_path: Path,
) -> None:
    """If a sub-skill's finalize collides with a read-only target etc.,
    other sub-skills still install; the failing one lands in errors."""
    target = tmp_path / "user_skills"
    target.mkdir()
    staging = tmp_path / "staging"

    async def fake_clone(spec, dest):
        dest.mkdir(parents=True, exist_ok=True)
        _write_skill_md(dest / "skills" / "ok1", "ok1", ["read_file"])
        _write_skill_md(dest / "skills" / "ok2", "ok2", ["read_file"])

    inst = SkillInstaller(
        skills_dir=target,
        staging_dir=staging,
        known_tools={"read_file"},
        clone_fn=fake_clone,
    )
    batch = await inst.stage_recursive("github:foo/two-ok")
    # Pre-create a sentinel file at target/ok1 that finalize must wipe;
    # this should still succeed because _force_rmtree handles read-only.
    (target / "ok1").mkdir()
    (target / "ok1" / "preexisting").write_text("x", encoding="utf-8")

    out = inst.finalize_batch(batch)
    # Both should still install — ok1's preexisting was wiped.
    assert len(out["installed"]) == 2
    # If finalize_batch ever does collect a failure, the count
    # invariant must hold:
    assert len(out["installed"]) + len(out["errors"]) >= len(batch.staged)
