"""P4-S5: stub tools for features owned by later slices.

We register the schemas now so the LLM's view of DeskPet's namespace is
complete from day one, but the handlers just return ``not implemented``
errors with the slice that will deliver them. Each stub is expected to
be overridden by a proper registration (same name) when the owning
slice merges — ``registry.register`` replaces on duplicate name.

Stubs grouped by owning slice:

* memory_{write,read,search}      — P4-S4 (MemoryManager hookup)
* delegate                         — future subagent spawn
* skill_invoke                     — P4-S10 (skill-system)
* mcp_call                         — P4-S9 (mcp-integration)
"""
from __future__ import annotations

import json
from typing import Any

from .registry import registry


def _stub_handler(slice_name: str):
    """Return a handler closure that reports the owning slice."""

    def handler(args: dict[str, Any], task_id: str) -> str:
        return json.dumps(
            {
                "error": f"not implemented (pending {slice_name})",
                "retriable": False,
            },
            ensure_ascii=False,
        )

    return handler


# ---------------------------------------------------------------------
# memory_* — P4-S4
# ---------------------------------------------------------------------
_MEMORY_WRITE_SCHEMA: dict[str, Any] = {
    "name": "memory_write",
    "description": (
        "Persist a fact / observation to DeskPet long-term memory. "
        "(Pending P4-S4 MemoryManager.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Content to remember."},
            "tier": {
                "type": "string",
                "enum": ["l1", "l2", "l3", "auto"],
                "default": "auto",
            },
            "salience": {
                "type": "number",
                "description": "0.0-1.0 importance. Default 0.5.",
                "default": 0.5,
            },
        },
        "required": ["text"],
    },
}
_MEMORY_READ_SCHEMA: dict[str, Any] = {
    "name": "memory_read",
    "description": (
        "Read a specific memory record by id. "
        "(Pending P4-S4 MemoryManager.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string"},
        },
        "required": ["memory_id"],
    },
}
_MEMORY_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "memory_search",
    "description": (
        "Hybrid recall across L1+L2+L3 (vec + FTS5 + recency + salience) "
        "with RRF fusion. (Pending P4-S4 MemoryManager.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}

registry.register(
    "memory_write", "memory", _MEMORY_WRITE_SCHEMA, _stub_handler("S4")
)
registry.register(
    "memory_read", "memory", _MEMORY_READ_SCHEMA, _stub_handler("S4")
)
registry.register(
    "memory_search", "memory", _MEMORY_SEARCH_SCHEMA, _stub_handler("S4")
)


# ---------------------------------------------------------------------
# control namespace: delegate / skill_invoke / mcp_call
# ---------------------------------------------------------------------
_DELEGATE_SCHEMA: dict[str, Any] = {
    "name": "delegate",
    "description": (
        "Spawn a focused sub-agent for a bounded sub-task. "
        "(Pending: future subagent slice.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The sub-agent's single goal.",
            },
            "context": {
                "type": "string",
                "description": "Relevant context snippets.",
            },
        },
        "required": ["goal"],
    },
}
_SKILL_INVOKE_SCHEMA: dict[str, Any] = {
    "name": "skill_invoke",
    "description": (
        "Invoke a DeskPet skill by name with arguments. Skills are "
        "composable multi-step procedures. (Pending P4-S10.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["skill_name"],
    },
}
_MCP_CALL_SCHEMA: dict[str, Any] = {
    "name": "mcp_call",
    "description": (
        "Call a tool exposed by an MCP server (namespace "
        "mcp_{server}_{tool}). (Pending P4-S9.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["server", "tool"],
    },
}

registry.register(
    "delegate", "control", _DELEGATE_SCHEMA, _stub_handler("subagent slice")
)
registry.register(
    "skill_invoke", "control", _SKILL_INVOKE_SCHEMA, _stub_handler("S10")
)
registry.register("mcp_call", "control", _MCP_CALL_SCHEMA, _stub_handler("S9"))
