"""Claude Code SKILL.md v1 parser (P4-S20 Wave 3a).

Spec: https://code.claude.com/docs/en/skills + reference impl
https://github.com/NanmiCoder/cc-haha. Locally enforced contract:
``openspec/changes/deskpet-skill-platform/specs/skill-md-parser/spec.md``.

Two responsibilities:
1. **Parse-time**: read ``SKILL.md``, validate the YAML frontmatter,
   produce ``ClaudeSkillMeta``. Body is kept verbatim — substitutions
   are deferred until invocation so the same parsed skill can be
   reused across many sessions with different args.
2. **Render-time**: ``render_body(meta.body_markdown, ...)`` does the
   variable substitution + inline shell injection just before the
   string is fed to the LLM.

Variable substitution (per spec §"Variable substitution"):
  ${CLAUDE_SKILL_DIR}   → skill dir absolute path
  ${CLAUDE_SESSION_ID}  → current session id
  $ARGUMENTS            → all args joined by single space
  $ARGUMENTS[N], $N     → 0-based positional arg

Inline shell injection (per spec §"Inline shell injection"):
  !`cmd`                → execute cmd (cwd=skill_dir, 10s timeout),
                          replace with stdout. Errors inlined as
                          [command failed: <exit N>: <stderr>].
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class SkillParseError(ValueError):
    """Raised when SKILL.md cannot be parsed; loader logs + skips."""


@dataclass
class ClaudeSkillMeta:
    """Claude Code v1 skill metadata.

    Field names mirror the frontmatter keys (kebab-case → snake_case).
    Unknown frontmatter keys land in ``raw_frontmatter`` so we can
    surface them in audit / debug UI without losing data.
    """

    name: str
    description: str
    body_markdown: str
    path: str
    skill_dir: str
    when_to_use: str = ""
    argument_hint: str = ""
    disable_model_invocation: bool = False
    user_invocable: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    context: str = "inline"  # only "inline" supported in v1
    hooks: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    source: str = "claude-code-v1"
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body). Raises on missing/invalid."""
    stripped = text.lstrip("﻿")  # BOM tolerance
    if not stripped.startswith("---"):
        raise SkillParseError("missing YAML frontmatter")
    # Strip the leading "---\n" (3 chars + newline)
    rest = stripped[3:].lstrip("\n")
    end = rest.find("\n---")
    if end < 0:
        raise SkillParseError("missing closing '---' delimiter")
    fm_text = rest[:end]
    body = rest[end + len("\n---"):].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid YAML: {exc}") from exc
    if not isinstance(fm, dict):
        raise SkillParseError(
            f"frontmatter must be a mapping, got {type(fm).__name__}"
        )
    return fm, body


# ---------------------------------------------------------------------
# allowed-tools parsing
# ---------------------------------------------------------------------


def _parse_allowed_tools(value: Any) -> list[str]:
    """Accept either a list or a space-separated string with paren-aware splits.

    Split by whitespace EXCEPT when inside parentheses, so e.g.
    ``Read Write Bash(git *)`` → ``["Read", "Write", "Bash(git *)"]``.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str):
        return [str(value)]
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in value:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch.isspace() and depth == 0:
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


# ---------------------------------------------------------------------
# Public parse API
# ---------------------------------------------------------------------


def parse_skill_md(path: Path) -> ClaudeSkillMeta:
    """Parse a SKILL.md file. Raises SkillParseError on any failure."""
    p = Path(path)
    if not p.exists():
        raise SkillParseError(f"file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(f"cannot read: {exc}") from exc

    fm, body = _split_frontmatter(text)

    # required: description
    description = fm.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillParseError("frontmatter must include non-empty 'description'")

    skill_dir = p.parent
    name = str(fm.get("name") or skill_dir.name).strip()

    context = str(fm.get("context", "inline"))
    if context not in ("inline", "fork"):
        context = "inline"

    return ClaudeSkillMeta(
        name=name,
        description=description.strip(),
        body_markdown=body,
        path=str(p),
        skill_dir=str(skill_dir),
        when_to_use=str(fm.get("when_to_use", "") or ""),
        argument_hint=str(fm.get("argument-hint", "") or ""),
        disable_model_invocation=bool(fm.get("disable-model-invocation", False)),
        user_invocable=bool(fm.get("user-invocable", True)),
        allowed_tools=_parse_allowed_tools(fm.get("allowed-tools")),
        paths=_parse_allowed_tools(fm.get("paths")),
        context=context,
        hooks=dict(fm.get("hooks") or {}),
        version=str(fm.get("version", "") or ""),
        raw_frontmatter=dict(fm),
    )


# ---------------------------------------------------------------------
# Body rendering — variables + inline shell
# ---------------------------------------------------------------------


_BACKTICK_RE = re.compile(r"!`([^`]+)`")
_VAR_DOLLAR_RE = re.compile(r"\$ARGUMENTS\[(\d+)\]|\$(\d+)|\$ARGUMENTS")


def render_body(
    body: str,
    *,
    skill_dir: Path,
    session_id: str,
    args: list[str],
    shell_timeout_s: float = 10.0,
) -> str:
    """Apply variable substitution + inline shell injection to body.

    Order:
      1. ${CLAUDE_SKILL_DIR}, ${CLAUDE_SESSION_ID} (verbatim string sub)
      2. $ARGUMENTS, $ARGUMENTS[N], $N (positional)
      3. !`cmd` (shell injection, runs in skill_dir)

    Failed shell commands inline as ``[command failed: exit N: stderr]``
    so the LLM still sees something coherent and can decide to recover.
    """
    # 1. directory + session id
    body = body.replace("${CLAUDE_SKILL_DIR}", str(skill_dir))
    body = body.replace("${CLAUDE_SESSION_ID}", session_id)

    # 2. arguments (longer pattern first to avoid $ARGUMENTS gobbling $ARG)
    def _arg_replace(m: re.Match[str]) -> str:
        idx_a = m.group(1)  # $ARGUMENTS[N]
        idx_n = m.group(2)  # $N
        if idx_a is not None:
            i = int(idx_a)
            return args[i] if 0 <= i < len(args) else ""
        if idx_n is not None:
            i = int(idx_n)
            return args[i] if 0 <= i < len(args) else ""
        # bare $ARGUMENTS
        return " ".join(args)

    body = _VAR_DOLLAR_RE.sub(_arg_replace, body)

    # 3. shell injection
    def _shell_replace(m: re.Match[str]) -> str:
        cmd = m.group(1)
        try:
            proc = subprocess.run(  # noqa: S602 — explicit per-spec
                cmd,
                shell=True,
                cwd=str(skill_dir),
                capture_output=True,
                text=True,
                timeout=shell_timeout_s,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return f"[command failed: timeout after {shell_timeout_s}s]"
        except OSError as exc:
            return f"[command failed: {type(exc).__name__}: {exc}]"
        if proc.returncode != 0:
            return f"[command failed: exit {proc.returncode}: {proc.stderr.strip()}]"
        return proc.stdout.strip()

    body = _BACKTICK_RE.sub(_shell_replace, body)
    return body
