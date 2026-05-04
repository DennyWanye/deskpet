"""P4-S20 Stage C — skill installer (git clone → stage → confirm → finalize).

Three URL forms:
  - ``github:owner/repo[/tree/branch/path]``
  - ``https://github.com/owner/repo[/tree/branch/path]``
  - ``git@github.com:owner/repo``

Flow:
  1. ``stage(url)`` → parse URL, clone --depth 1 into staging,
     read manifest.json, validate via safety.validate_manifest,
     return StagedSkill (the UI shows the manifest for user confirm).
  2. UI sends back ``approve=True``.
  3. ``finalize(staged)`` → move staging dir into ``skills_dir/<name>``.

Failure cleanup:
  - Network/git clone failure → staging dir removed, error returned.
  - Safety check failure → staging dir removed, SafetyError raised.

The clone is done via ``asyncio.subprocess`` so it doesn't block the
event loop. Tests inject a ``clone_fn`` to skip the network.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import structlog

from .safety import SafetyError, validate_manifest

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------


@dataclass
class GithubSpec:
    owner: str
    repo: str
    branch: Optional[str] = None
    subpath: Optional[str] = None
    git_url: str = ""

    def __post_init__(self) -> None:
        if not self.git_url:
            self.git_url = f"https://github.com/{self.owner}/{self.repo}.git"


_SHORTHAND_RE = re.compile(
    r"^github:(?P<owner>[\w\-.]+)/(?P<repo>[\w\-.]+)"
    r"(?:/tree/(?P<branch>[\w\-./]+?)/(?P<subpath>.+))?"
    r"(?:/(?P<bare_subpath>[\w\-./]+))?$"
)
_HTTPS_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w\-.]+)/(?P<repo>[\w\-.]+)"
    r"(?:\.git)?(?:/tree/(?P<branch>[\w\-./]+?)/(?P<subpath>.+))?/?$"
)
_SSH_RE = re.compile(
    r"^git@github\.com:(?P<owner>[\w\-.]+)/(?P<repo>[\w\-.]+?)(?:\.git)?$"
)


def parse_github_url(url: str) -> GithubSpec:
    """Parse one of the three GitHub URL forms; raise ValueError otherwise."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")
    url = url.strip()

    m = _SHORTHAND_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo").removesuffix(".git")
        branch = m.group("branch")
        subpath = m.group("subpath") or m.group("bare_subpath")
        return GithubSpec(owner=owner, repo=repo, branch=branch, subpath=subpath)

    m = _HTTPS_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo").removesuffix(".git")
        return GithubSpec(
            owner=owner,
            repo=repo,
            branch=m.group("branch"),
            subpath=m.group("subpath"),
        )

    m = _SSH_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo").removesuffix(".git")
        return GithubSpec(
            owner=owner,
            repo=repo,
            git_url=f"git@github.com:{owner}/{repo}.git",
        )

    raise ValueError(f"unsupported url form: {url!r}")


# ---------------------------------------------------------------------
# Default git clone
# ---------------------------------------------------------------------


async def _default_clone(spec: GithubSpec, dest: Path) -> None:
    """Run ``git clone --depth 1`` into ``dest``."""
    args = ["git", "clone", "--depth", "1"]
    if spec.branch:
        args += ["--branch", spec.branch]
    args += [spec.git_url, str(dest)]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {proc.returncode}): "
            f"{stderr.decode('utf-8', errors='replace')[:500]}"
        )


# ---------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------


@dataclass
class StagedSkill:
    staging_id: str
    staging_path: Path
    manifest: dict[str, Any]
    name: str
    spec: GithubSpec
    permission_categories: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------


class SkillInstaller:
    def __init__(
        self,
        *,
        skills_dir: Path,
        staging_dir: Path,
        known_tools: set[str],
        clone_fn: Callable[[GithubSpec, Path], Awaitable[None]] | None = None,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.staging_dir = Path(staging_dir)
        self.known_tools = set(known_tools)
        self.clone_fn = clone_fn or _default_clone
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    async def stage(self, url: str) -> StagedSkill:
        spec = parse_github_url(url)
        staging_id = uuid.uuid4().hex[:12]
        full_staging = self.staging_dir / spec.repo
        # Wipe any leftover from a prior abandoned stage
        if full_staging.exists():
            shutil.rmtree(full_staging, ignore_errors=True)
        try:
            await self.clone_fn(spec, full_staging)
            # If subpath specified, the actual skill lives under that dir
            skill_root = (
                full_staging / spec.subpath if spec.subpath else full_staging
            )
            if not skill_root.exists():
                raise FileNotFoundError(
                    f"subpath {spec.subpath!r} not present in cloned repo"
                )
            manifest = self._read_manifest(skill_root)
            validate_manifest(manifest, known_tools=self.known_tools)
        except SafetyError:
            shutil.rmtree(full_staging, ignore_errors=True)
            raise
        except Exception:
            shutil.rmtree(full_staging, ignore_errors=True)
            raise

        return StagedSkill(
            staging_id=staging_id,
            staging_path=skill_root,
            manifest=manifest,
            name=manifest["name"],
            spec=spec,
            permission_categories=list(
                manifest.get("permission_categories") or []
            ),
        )

    def finalize(self, staged: StagedSkill) -> Path:
        target = self.skills_dir / staged.name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        # Move staging → final
        staging_root = staged.staging_path
        if staging_root.parent != self.staging_dir:
            # subpath case: copy only the subpath dir to final
            shutil.copytree(staging_root, target)
            shutil.rmtree(
                self._top_staging_dir(staged), ignore_errors=True
            )
        else:
            shutil.move(str(staging_root), str(target))
        return target

    def cancel(self, staged: StagedSkill) -> None:
        shutil.rmtree(self._top_staging_dir(staged), ignore_errors=True)

    def uninstall(self, name: str) -> None:
        # Reject path traversal — name must be a simple skill dir name
        if "/" in name or "\\" in name or name.startswith(".."):
            raise ValueError(f"invalid skill path: {name!r}")
        target = self.skills_dir / name
        if not target.exists():
            return
        # Defense in depth: assert the resolved path is still under skills_dir
        try:
            target.resolve().relative_to(self.skills_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"path escape attempt: {name!r}") from exc
        shutil.rmtree(target, ignore_errors=True)

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------
    def _read_manifest(self, root: Path) -> dict[str, Any]:
        mf = root / "manifest.json"
        if mf.exists():
            try:
                return json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SafetyError(f"invalid manifest.json: {exc}") from exc
        # Auto-derive a minimal manifest from SKILL.md frontmatter when
        # the repo doesn't ship one.
        skill = root / "SKILL.md"
        if skill.exists():
            try:
                from deskpet.skills.parser import parse_skill_md

                meta = parse_skill_md(skill)
                return {
                    "name": meta.name,
                    "description": meta.description,
                    "tools": list(meta.allowed_tools),
                    "permission_categories": [],
                }
            except Exception as exc:  # noqa: BLE001
                raise SafetyError(
                    f"cannot derive manifest from SKILL.md: {exc}"
                ) from exc
        raise SafetyError("repository has neither manifest.json nor SKILL.md")

    def _top_staging_dir(self, staged: StagedSkill) -> Path:
        return self.staging_dir / staged.spec.repo
