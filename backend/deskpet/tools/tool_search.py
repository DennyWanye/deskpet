"""P4-S5: ``tool_search`` meta-tool (lazy schema loading, CCB pattern).

Spec: "Tool Search for Lazy Schema Loading" (tool-framework/spec.md).

Agent loop starts with a small, curated set of toolsets exposed via
``ContextAssembler``. When the user asks for something the curated
subset can't handle, the LLM calls ``tool_search(query="...")`` and the
registry searches the FULL inventory (including env-hidden tools — we
want to surface "web_search_brave requires BRAVE_API_KEY" so the agent
can ask the user to set it) for name/description matches. Matching
tools come back as a schema list the agent can cite / request activation
for on subsequent turns.

Matching is intentionally simple substring (lowercased, whitespace-split
tokens): if every query token appears in ``name + " " + description``,
the tool is a hit. We sort hits by number of tokens matched
(descending), then by name (ascending) for stable output.
"""
from __future__ import annotations

import json
from typing import Any

from .registry import registry

_SCHEMA: dict[str, Any] = {
    "name": "tool_search",
    "description": (
        "Search the complete DeskPet tool registry by keyword. Use this when "
        "you need a capability that isn't in your current toolset (e.g. file "
        "ops, web fetch, todo management). Returns matching tools' schemas; "
        "invoke them directly on the next turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Space-separated keywords. Matches against tool name + "
                    "description, case-insensitive. Every token must appear."
                ),
            },
            "toolset": {
                "type": "string",
                "description": (
                    "Optional toolset filter: 'file', 'web', 'memory', "
                    "'todo', 'control'. Omit to search all toolsets."
                ),
            },
        },
        "required": ["query"],
    },
}


def _handle_tool_search(args: dict[str, Any], task_id: str) -> str:
    query = str(args.get("query", "") or "").strip().lower()
    if not query:
        return json.dumps(
            {"error": "query must be non-empty", "retriable": False}
        )
    toolset_filter = args.get("toolset")
    tokens = [t for t in query.split() if t]

    hits: list[tuple[int, str, dict[str, Any]]] = []
    for spec in registry.all_specs():
        if toolset_filter and spec.toolset != toolset_filter:
            continue
        if spec.name == "tool_search":
            # Don't surface the search tool itself — the agent is
            # already calling it, no point advertising it again.
            continue
        haystack = (
            spec.name + " " + str(spec.schema.get("description", ""))
        ).lower()
        matched = sum(1 for tok in tokens if tok in haystack)
        if matched == len(tokens):
            hits.append(
                (
                    matched,
                    spec.name,
                    {"type": "function", "function": dict(spec.schema)},
                )
            )

    # Sort: more tokens matched first, then alphabetical for determinism.
    hits.sort(key=lambda t: (-t[0], t[1]))
    return json.dumps(
        {
            "matches": [h[2] for h in hits],
            "count": len(hits),
            "query": query,
            "toolset_filter": toolset_filter,
        },
        ensure_ascii=False,
    )


registry.register(
    name="tool_search",
    toolset="control",
    schema=_SCHEMA,
    handler=_handle_tool_search,
)
