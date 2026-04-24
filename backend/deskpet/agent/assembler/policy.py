"""Policy loader + user overrides merge (P4-S7 tasks 12.7, 12.8).

Policies live as declarative YAML. The packaged default sits at
``deskpet/agent/assembler/policies/default.yaml`` and covers the
8 canonical ``task_type``s. User overrides live at
``%APPDATA%/deskpet/policies/overrides.yaml`` (or the XDG equivalent)
and are merged on top per D9:

- ``prefer``, ``tools``, ``memory`` → replaced by user value (shallow).
- ``must`` → USER MAY ADD but MUST NOT REMOVE core ``memory`` component.
- New ``task_type`` entries from user → simply add to the resulting map.

The loader returns a dict mapping ``task_type -> AssemblyPolicy`` and
logs (not raises) on any structural issues. A missing file is not an
error — the packaged default suffices on a clean install.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import structlog
import yaml

from deskpet.agent.assembler.bundle import (
    TASK_TYPES,
    AssemblyPolicy,
    MemoryPolicy,
)

logger = structlog.get_logger(__name__)


_CORE_MUST = "memory"
_DEFAULT_YAML = Path(__file__).parent / "policies" / "default.yaml"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_policies(
    *,
    default_path: Optional[Path] = None,
    overrides_path: Optional[Path] = None,
) -> dict[str, AssemblyPolicy]:
    """Load default + overrides, merge, return ``{task_type: AssemblyPolicy}``.

    Missing defaults → a fallback built-in set (see :func:`_builtin_defaults`).
    Missing overrides → default-only.
    Malformed YAML → logged warning + ignored.
    """
    default_raw = _load_yaml(default_path or _DEFAULT_YAML)
    if not default_raw:
        default_raw = _builtin_defaults_raw()

    merged = dict(default_raw)
    if overrides_path is not None:
        user_raw = _load_yaml(overrides_path)
        if user_raw:
            merged = _merge(merged, user_raw)

    # Materialise to dataclasses. Unknown fields in YAML are ignored.
    policies: dict[str, AssemblyPolicy] = {}
    for task_type, body in merged.items():
        if not isinstance(body, dict):
            logger.warning(
                "assembler.policy_entry_not_mapping",
                task_type=task_type,
                got=type(body).__name__,
            )
            continue
        policies[task_type] = _to_policy(task_type, body)

    # Ensure every canonical task_type has a policy — fall back to chat's
    # policy cloned with the right task_type tag.
    if "chat" not in policies:
        policies["chat"] = _builtin_chat_policy()
    for tt in TASK_TYPES:
        if tt not in policies:
            base = policies["chat"]
            policies[tt] = AssemblyPolicy(
                task_type=tt,
                must=list(base.must),
                prefer=list(base.prefer),
                tools=list(base.tools),
                memory=MemoryPolicy(
                    l1=base.memory.l1,
                    l2_top_k=base.memory.l2_top_k,
                    l3_top_k=base.memory.l3_top_k,
                ),
                budget_ratio=base.budget_ratio,
            )

    return policies


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------
def _load_yaml(path: Path) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "assembler.policy_io_error", path=str(path), error=str(exc)
        )
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.warning(
            "assembler.policy_yaml_error", path=str(path), error=str(exc)
        )
        return {}
    if not isinstance(data, dict):
        return {}
    # Support nesting under a top-level "policies:" key for clarity.
    if (
        "policies" in data
        and isinstance(data["policies"], dict)
        and len(data) == 1
    ):
        data = data["policies"]
    return data


def _merge(
    default: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Shallow merge at the task_type level with ``must`` safety check."""
    if (
        "policies" in overrides
        and isinstance(overrides["policies"], dict)
        and len(overrides) == 1
    ):
        overrides = overrides["policies"]

    out: dict[str, Any] = {
        k: _clone(v) for k, v in default.items() if isinstance(v, dict)
    }
    for tt, body in overrides.items():
        if not isinstance(body, dict):
            logger.warning(
                "assembler.override_entry_not_mapping",
                task_type=tt,
                got=type(body).__name__,
            )
            continue
        base = out.get(tt, {})
        merged = dict(base)
        for field, value in body.items():
            if field == "must":
                merged["must"] = _safe_merge_must(
                    tt, base.get("must", []), value
                )
            elif field == "memory" and isinstance(value, dict):
                # Deep-merge memory params so users can override only l3_top_k
                # without having to restate l1/l2_top_k.
                merged["memory"] = {
                    **(base.get("memory") or {}),
                    **value,
                }
            else:
                merged[field] = value
        out[tt] = merged
    return out


def _safe_merge_must(
    task_type: str, default_must: Any, user_must: Any
) -> list[str]:
    """Ensure ``memory`` stays in ``must`` regardless of what the user did.

    User MAY add new components; user MAY reorder; user MUST NOT delete
    ``memory``. We log a warning if the user tried.
    """
    default_list = list(default_must or [])
    user_list = list(user_must or [])

    # Sanity: drop non-string entries silently.
    user_list = [c for c in user_list if isinstance(c, str) and c]

    if _CORE_MUST in default_list and _CORE_MUST not in user_list:
        logger.warning(
            "assembler.user_removed_core_must",
            task_type=task_type,
            core=_CORE_MUST,
        )
        user_list = [_CORE_MUST] + user_list

    return user_list


def _to_policy(task_type: str, body: dict[str, Any]) -> AssemblyPolicy:
    memory_raw = body.get("memory") or {}
    mem = MemoryPolicy(
        l1=str(memory_raw.get("l1", "snapshot")),
        l2_top_k=int(memory_raw.get("l2_top_k", 5)),
        l3_top_k=int(memory_raw.get("l3_top_k", 5)),
    )
    return AssemblyPolicy(
        task_type=task_type,
        must=[str(c) for c in (body.get("must") or []) if isinstance(c, str) and c] or [_CORE_MUST],
        prefer=[
            str(c) for c in (body.get("prefer") or []) if isinstance(c, str) and c
        ],
        tools=[
            str(c) for c in (body.get("tools") or []) if isinstance(c, str) and c
        ] or ["*"],
        memory=mem,
        budget_ratio=(
            float(body["budget_ratio"]) if "budget_ratio" in body else None
        ),
    )


def _clone(d: dict[str, Any]) -> dict[str, Any]:
    """Shallow dict clone, with mutable sub-lists defensively copied."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = list(v)
        elif isinstance(v, dict):
            out[k] = dict(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Built-in fallback defaults (used when the packaged YAML is missing)
# ---------------------------------------------------------------------------
def _builtin_chat_policy() -> AssemblyPolicy:
    return AssemblyPolicy(
        task_type="chat",
        must=["memory", "persona"],
        prefer=["tool", "time", "workspace"],
        tools=["*"],
        memory=MemoryPolicy(l1="snapshot", l2_top_k=5, l3_top_k=5),
    )


def _builtin_defaults_raw() -> dict[str, Any]:
    """Fallback raw dict used only if ``default.yaml`` is missing.

    Keeps the classifier + assembler functional on a misconfigured install.
    Every policy with a non-empty ``tools`` list includes ``tool`` in
    ``prefer`` so the ToolComponent runs and emits its schemas.
    """
    return {
        "chat": {
            "must": ["memory", "persona"],
            "prefer": ["tool", "time", "workspace"],
            "tools": ["*"],
            "memory": {"l1": "snapshot", "l2_top_k": 5, "l3_top_k": 5},
        },
        "recall": {
            "must": ["memory"],
            "prefer": ["persona", "tool", "time"],
            "tools": ["memory_read", "memory_search"],
            "memory": {"l1": "snapshot", "l2_top_k": 10, "l3_top_k": 10},
        },
        "task": {
            "must": ["memory", "persona"],
            "prefer": ["tool", "skill", "workspace", "time"],
            "tools": ["*"],
            "memory": {"l1": "snapshot", "l2_top_k": 5, "l3_top_k": 3},
        },
        "code": {
            "must": ["memory"],
            "prefer": ["persona", "tool", "workspace"],
            "tools": ["file_read", "file_write", "file_search", "todo_add", "todo_list"],
            "memory": {"l1": "snapshot", "l2_top_k": 3, "l3_top_k": 5},
        },
        "web_search": {
            "must": ["memory"],
            "prefer": ["persona", "tool", "time"],
            "tools": ["web_search", "web_fetch", "web_crawl"],
            "memory": {"l1": "snapshot", "l2_top_k": 2, "l3_top_k": 3},
        },
        "plan": {
            "must": ["memory", "persona"],
            "prefer": ["tool", "time", "workspace"],
            "tools": ["todo_add", "todo_list", "todo_update"],
            "memory": {"l1": "snapshot", "l2_top_k": 3, "l3_top_k": 5},
        },
        "emotion": {
            "must": ["memory", "persona"],
            "prefer": ["time"],
            "tools": [],
            "memory": {"l1": "snapshot", "l2_top_k": 5, "l3_top_k": 5},
        },
        "command": {
            "must": ["memory"],
            "prefer": ["persona", "tool"],
            "tools": ["*"],
            "memory": {"l1": "snapshot", "l2_top_k": 2, "l3_top_k": 0},
        },
    }
