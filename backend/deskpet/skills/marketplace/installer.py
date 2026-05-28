# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S20 Stage C — skill installer (git clone → stage → confirm → finalize).

Windows note: ``shutil.rmtree`` on a freshly-cloned ``.git`` directory
sometimes hits ``PermissionError`` because git left a packfile open or
a lockfile read-only. We use a ``_force_rmtree`` helper that retries
with ``stat.S_IWRITE`` permission fix and a brief delay before giving
up. Without this, repeat-install runs see "destination already exists"
errors from git clone.

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
import os
import re
import shutil
import stat
import time
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


def _force_rmtree(path: Path, attempts: int = 3) -> bool:
    """rmtree that handles Windows .git read-only files.

    Returns True on success, False if the dir still exists after all
    attempts. ``shutil.rmtree(ignore_errors=True)`` swallows real
    errors silently and leaves the dir; this version retries with the
    permission fix from python docs:
    https://docs.python.org/3/library/shutil.html#rmtree-example
    """
    def _onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:  # noqa: BLE001
            pass

    for i in range(attempts):
        if not path.exists():
            return True
        try:
            shutil.rmtree(path, onerror=_onerror)
        except Exception:  # noqa: BLE001
            pass
        if not path.exists():
            return True
        time.sleep(0.2 * (i + 1))
    return not path.exists()


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


@dataclass
class BatchStageResult:
    """Outcome of `stage_recursive` — multi-skill best-effort install.

    Single-skill mode (root has SKILL.md or manifest.json, OR subpath
    pinned in URL): ``staged`` has exactly 1 entry, ``errors`` empty.

    Multi-skill mode (root lacks both AND no subpath): ``staged`` has
    every successfully validated sub-skill; ``errors`` collects per-
    sub-skill failures (safety violation, invalid manifest, etc.) so
    the caller / UI can show partial-success outcomes.
    """
    staged: list[StagedSkill] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    multi: bool = False
    repo_dir: Optional[Path] = None


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
        full_staging = self.staging_dir / spec.repo
        # Wipe any leftover from a prior abandoned stage
        if full_staging.exists():
            _force_rmtree(full_staging)
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
            return self._stage_at_dir(skill_root, spec)
        except SafetyError:
            _force_rmtree(full_staging)
            raise
        except Exception:
            _force_rmtree(full_staging)
            raise

    async def stage_recursive(self, url: str) -> BatchStageResult:
        """Best-effort multi-skill stage.

        When the URL points at a repo root that contains NO SKILL.md
        and NO manifest.json (typical of plugin-marketplace repos like
        obra/superpowers), recursively find every nested SKILL.md and
        stage each subdir as its own pending skill.

        Failures of individual sub-skills (safety violation, invalid
        manifest, etc.) are collected into ``errors`` rather than
        aborting the whole batch — the user explicitly asked for
        "install whatever can be installed".

        Single-skill paths (root has SKILL.md/manifest.json, or URL
        has a subpath) still produce a list of one item — same shape,
        simpler call site.

        Args:
            url: One of the three supported GitHub URL forms.

        Returns:
            BatchStageResult with ``staged`` list, ``errors`` list,
            and ``multi`` flag indicating whether the multi-SKILL.md
            recursion path was taken.
        """
        spec = parse_github_url(url)
        full_staging = self.staging_dir / spec.repo
        if full_staging.exists():
            _force_rmtree(full_staging)

        try:
            await self.clone_fn(spec, full_staging)
        except Exception:
            _force_rmtree(full_staging)
            raise

        skill_root = (
            full_staging / spec.subpath if spec.subpath else full_staging
        )
        if not skill_root.exists():
            _force_rmtree(full_staging)
            raise FileNotFoundError(
                f"subpath {spec.subpath!r} not present in cloned repo"
            )

        # Single-skill mode: root has manifest.json or SKILL.md.
        has_manifest = (skill_root / "manifest.json").exists()
        has_skill_md = (skill_root / "SKILL.md").exists()
        if has_manifest or has_skill_md:
            try:
                staged = self._stage_at_dir(skill_root, spec)
                return BatchStageResult(
                    staged=[staged],
                    errors=[],
                    multi=False,
                    repo_dir=full_staging,
                )
            except SafetyError as exc:
                _force_rmtree(full_staging)
                # Re-raise so single-skill paths keep their old
                # surface-the-error semantics.
                raise

        # Multi-skill mode: recurse, stage each, collect failures.
        candidates = self._discover_skill_dirs(skill_root)
        result = BatchStageResult(multi=True, repo_dir=full_staging)
        if not candidates:
            _force_rmtree(full_staging)
            raise SafetyError(
                "repository has no SKILL.md anywhere (checked root + recursive)"
            )

        for sub_dir in candidates:
            try:
                # Synthesise a per-sub-skill GithubSpec so finalize knows
                # which staging tree to clean up after we're done.
                sub_spec = GithubSpec(
                    owner=spec.owner,
                    repo=spec.repo,
                    branch=spec.branch,
                    subpath=(
                        str(sub_dir.relative_to(full_staging)).replace(
                            os.sep, "/"
                        )
                    ),
                    git_url=spec.git_url,
                )
                staged = self._stage_at_dir(sub_dir, sub_spec)
                result.staged.append(staged)
            except SafetyError as exc:
                result.errors.append({
                    "path": str(sub_dir.relative_to(full_staging)).replace(
                        os.sep, "/"
                    ),
                    "error": f"SafetyError: {exc}",
                })
            except Exception as exc:  # noqa: BLE001
                result.errors.append({
                    "path": str(sub_dir.relative_to(full_staging)).replace(
                        os.sep, "/"
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                })

        if not result.staged:
            # Nothing usable — clean up the clone, surface a clear error.
            _force_rmtree(full_staging)
            raise SafetyError(
                f"no valid skills in repository ({len(result.errors)} "
                f"candidates failed validation)"
            )

        return result

    # -----------------------------------------------------------------
    # Recursive discovery helpers
    # -----------------------------------------------------------------
    def _discover_skill_dirs(self, root: Path) -> list[Path]:
        """Find every directory under ``root`` that contains a SKILL.md.

        Hidden dirs (``.git``, ``.github``, ``.claude-plugin``, etc.) and
        common non-skill subtrees (node_modules, __pycache__, etc.) are
        skipped to keep the scan cheap on big repos.
        """
        skip_names = {
            ".git", ".github", ".claude-plugin", ".cursor-plugin",
            ".codex-plugin", ".opencode", "node_modules", "__pycache__",
            ".pytest_cache", "dist", "build", "target", ".venv", "venv",
            ".idea", ".vscode",
        }
        results: list[Path] = []
        # Use os.walk so we can prune by mutating dirnames.
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune in place — saves descending into noise.
            dirnames[:] = [d for d in dirnames if d not in skip_names]
            if "SKILL.md" in filenames:
                results.append(Path(dirpath))
        # Stable order so tests + UI list deterministically.
        results.sort()
        return results

    def _stage_at_dir(
        self, skill_root: Path, spec: GithubSpec
    ) -> StagedSkill:
        """Read + validate manifest at ``skill_root``; return StagedSkill.

        Used by both single-skill ``stage()`` and per-sub-skill
        ``stage_recursive()``. Caller owns the clone lifecycle (cleanup
        on error happens at the outer scope).
        """
        manifest = self._read_manifest(skill_root)
        validate_manifest(manifest, known_tools=self.known_tools)
        staging_id = uuid.uuid4().hex[:12]
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

    def finalize(self, staged: StagedSkill, *, cleanup_repo: bool = True) -> Path:
        """Move the staged sub-tree into the final skills directory.

        Args:
            staged: The StagedSkill to install.
            cleanup_repo: When True (default), also wipe the top-level
                staging clone after copy. Set False in batch mode where
                multiple sub-skills share one clone — the caller wipes
                the clone once after the whole batch completes.

        Returns:
            The absolute path of the installed skill directory.
        """
        target = self.skills_dir / staged.name
        if target.exists():
            _force_rmtree(target)
        # Move staging → final
        staging_root = staged.staging_path
        if staging_root.parent != self.staging_dir:
            # subpath case: copy only the subpath dir to final
            shutil.copytree(staging_root, target)
            if cleanup_repo:
                _force_rmtree(self._top_staging_dir(staged))
        else:
            shutil.move(str(staging_root), str(target))
        return target

    def finalize_batch(self, result: "BatchStageResult") -> dict[str, Any]:
        """Finalize every staged sub-skill in a BatchStageResult.

        Iterates each StagedSkill, calls finalize(..., cleanup_repo=False),
        then wipes the shared clone once at the end. Per-skill failures
        are collected; one bad sub-skill does not abort the rest.

        Returns a dict with ``installed`` (list of {name, path}) and
        ``errors`` (list of {name, error}) — same shape as the ws
        ``skill_install_batch_completed`` payload.
        """
        installed: list[dict[str, str]] = []
        errors: list[dict[str, str]] = list(result.errors)  # carry over staging-time errors
        for staged in result.staged:
            try:
                final_path = self.finalize(staged, cleanup_repo=False)
                installed.append({"name": staged.name, "path": str(final_path)})
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "name": staged.name,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        # Single cleanup of the shared clone — only in multi mode where
        # we actually shared one. Single-skill batches don't have a
        # top-level clone separate from the moved staging_root.
        if result.multi and result.repo_dir is not None:
            _force_rmtree(result.repo_dir)
        return {"installed": installed, "errors": errors}

    def cancel(self, staged: StagedSkill) -> None:
        _force_rmtree(self._top_staging_dir(staged))

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
        _force_rmtree(target)

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
