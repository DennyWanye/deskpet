"""P4-S20 Stage C — manifest.json safety check.

Validates a skill's ``manifest.json`` against a known-tool allow-list
BEFORE writing anything to the user-skills directory. Spec:
``openspec/changes/deskpet-skill-platform/specs/skill-marketplace/spec.md``.
"""
from __future__ import annotations

import re
from typing import Any


class SafetyError(ValueError):
    """Raised when a manifest fails any safety check."""


# The 7 categories the gate knows about — anything else is a typo or
# a malicious attempt to bypass the gate.
_KNOWN_CATEGORIES = {
    "read_file",
    "read_file_sensitive",
    "write_file",
    "desktop_write",
    "shell",
    "network",
    "mcp_call",
    "skill_install",
}

_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def validate_manifest(
    manifest: dict[str, Any], *, known_tools: set[str]
) -> None:
    """Validate manifest in-place. Raises SafetyError on any violation."""
    if not isinstance(manifest, dict):
        raise SafetyError(f"manifest must be a JSON object, got {type(manifest).__name__}")

    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        raise SafetyError("manifest missing required 'name'")
    if not _NAME_RE.match(name):
        raise SafetyError(
            f"manifest 'name' has invalid characters: {name!r} "
            "(use a-z A-Z 0-9 _ - only, max 64 chars)"
        )

    description = manifest.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SafetyError("manifest missing required 'description'")

    tools = manifest.get("tools") or []
    if not isinstance(tools, list):
        raise SafetyError("manifest 'tools' must be a list")
    for t in tools:
        if not isinstance(t, str):
            raise SafetyError(f"manifest tool entry not a string: {t!r}")
        if t not in known_tools:
            raise SafetyError(f"unknown tool: {t!r}")

    cats = manifest.get("permission_categories") or []
    if not isinstance(cats, list):
        raise SafetyError("manifest 'permission_categories' must be a list")
    for c in cats:
        if c not in _KNOWN_CATEGORIES:
            raise SafetyError(f"unknown permission category: {c!r}")
