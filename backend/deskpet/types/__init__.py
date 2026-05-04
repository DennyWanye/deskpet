"""Shared cross-package types for the skill-platform v1 (P4-S20).

Kept in a leaf package so any module can import without circular deps.
"""

from .skill_platform import (
    PermissionCategory,
    PermissionDecision,
    PermissionRequest,
    PermissionResponse,
    SkillSourceTier,
    ToolUseEvent,
)

__all__ = [
    "PermissionCategory",
    "PermissionDecision",
    "PermissionRequest",
    "PermissionResponse",
    "SkillSourceTier",
    "ToolUseEvent",
]
