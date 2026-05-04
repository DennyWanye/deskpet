"""P4-S20 shared dataclasses for the skill-platform IPC + permission contracts.

These types are the **wire contract** between backend ↔ frontend ↔ tools ↔
plugins. Every change here ripples to TypeScript types and IPC handlers, so
keep the surface minimal and well-documented.

References:
  - openspec/changes/deskpet-skill-platform/specs/permission-gate/spec.md
  - openspec/changes/deskpet-skill-platform/specs/tool-use/spec.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# 7 categories from spec permission-gate §"Permission categories table".
PermissionCategory = Literal[
    "read_file",
    "read_file_sensitive",
    "write_file",
    "desktop_write",
    "shell",
    "network",
    "mcp_call",
    "skill_install",
]


# Tier of the skill source — determines override priority (plugin highest).
SkillSourceTier = Literal["bundled", "user", "project", "plugin"]


@dataclass(frozen=True)
class PermissionDecision:
    """Outcome of `PermissionGate.check(...)`.

    `source` records *why* this decision was reached — useful for audit
    logs and for the UI to show "you allowed this earlier" hints.
    """

    allow: bool
    source: Literal[
        "default-allow",
        "default-deny",
        "config-deny",
        "user-allowed",
        "user-denied",
        "user-allowed-session",
        "cache-hit",
        "timeout",
    ]
    pattern: str | None = None  # populated when source=="config-deny"
    request_id: str | None = None


@dataclass
class PermissionRequest:
    """Payload sent from backend → frontend on the control WS.

    `summary` is a one-line human-readable string (rendered as popup title).
    `params` is the full operation params (rendered as collapsible details).
    `default_action` lets the UI pre-highlight a button.
    """

    request_id: str
    category: PermissionCategory
    summary: str
    params: dict[str, Any]
    default_action: Literal["allow", "prompt", "deny"] = "prompt"
    dangerous: bool = False
    session_id: str = "default"


@dataclass
class PermissionResponse:
    """Payload sent from frontend → backend in reply to `permission_request`.

    `decision`:
      - "allow"          → one-time allow
      - "allow_session"  → cache for this session, same category+params shape
      - "deny"           → one-time deny
    """

    request_id: str
    decision: Literal["allow", "allow_session", "deny"]


@dataclass
class ToolUseEvent:
    """Streaming event emitted by the agent loop during a tool_use turn.

    Frontend uses `kind` to decide rendering: `request` shows a step,
    `result` shows the outcome, `cancelled` shows a red X.
    """

    kind: Literal["request", "result", "cancelled"]
    tool_name: str
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    turn: int = 0
