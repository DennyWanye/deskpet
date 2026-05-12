"""P5-S2 G1 — fetch_tool_result tool.

Companion to B1 (tool_result_truncator). When the agent loop truncates a
long tool_result body it leaves a marker like
``...[truncated 8421 chars, ref_id=abc123 — use fetch_tool_result to read more]...``

This tool lets the LLM fetch the full body (or a slice) on demand,
keyed by the ``ref_id`` it sees in the marker.

Without this tool, the marker advertises a capability that doesn't
exist — and the LLM has been observed calling ``fetch_tool_result(...)``
which then hits "unknown tool" → ``HallucinationError``. Registering
this handler closes that gap.
"""
from __future__ import annotations

import json
from typing import Any

from agent.tool_result_truncator import get_global_ref_store


# Reasonable per-call slice cap so a single LLM call can't blow context
# back up. The whole point of truncation was to keep history compact —
# the LLM should grab targeted slices, not the whole megabyte.
MAX_SLICE_CHARS = 8000


def fetch_tool_result_handler(args: dict[str, Any], session_id: str) -> str:
    """Resolve a ref_id to its stored body (full or slice).

    Args:
      ref_id   (str, required): the token from a "[truncated …]" marker.
      start    (int, optional): inclusive start offset; defaults to 0.
      end      (int, optional): exclusive end offset; defaults to start +
                                ``MAX_SLICE_CHARS``.

    Returns OpenAI tool-result envelope as a JSON string:
      {"ok": true,  "content": "<slice>",
       "ref_id": "<ref>", "start": int, "end": int, "total_len": int}
      or
      {"ok": false, "error": "<reason>"}
    """
    ref_id = (args or {}).get("ref_id")
    if not isinstance(ref_id, str) or not ref_id.strip():
        return json.dumps(
            {"ok": False, "error": "missing or empty `ref_id`"},
            ensure_ascii=False,
        )
    ref_id = ref_id.strip()

    store = get_global_ref_store()
    full = store.get(ref_id)
    if full is None:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    f"ref_id {ref_id!r} not found (the result may have "
                    "been evicted from the LRU cache; re-run the original "
                    "tool call to get a fresh ref)."
                ),
            },
            ensure_ascii=False,
        )

    total_len = len(full)
    raw_start = args.get("start") if isinstance(args, dict) else None
    raw_end = args.get("end") if isinstance(args, dict) else None

    try:
        start = int(raw_start) if raw_start is not None else 0
    except (TypeError, ValueError):
        start = 0
    try:
        end = int(raw_end) if raw_end is not None else (start + MAX_SLICE_CHARS)
    except (TypeError, ValueError):
        end = start + MAX_SLICE_CHARS

    # Clamp + enforce slice cap
    start = max(0, min(total_len, start))
    end = max(start, min(total_len, end))
    if end - start > MAX_SLICE_CHARS:
        end = start + MAX_SLICE_CHARS

    slice_text = store.get(ref_id, start=start, end=end) or ""
    return json.dumps(
        {
            "ok": True,
            "content": slice_text,
            "ref_id": ref_id,
            "start": start,
            "end": end,
            "total_len": total_len,
            "truncated": end < total_len,
        },
        ensure_ascii=False,
    )


FETCH_TOOL_RESULT_SCHEMA = {
    "name": "fetch_tool_result",
    "description": (
        "Retrieve the full body (or a slice) of a previously-truncated "
        "tool_result by its ref_id. The ref_id appears in the truncation "
        "marker like '[truncated 8421 chars, ref_id=abc123 …]'. Use this "
        "when the head/tail snippet wasn't enough and you need to see "
        "more of the original output. Slices are capped at "
        f"{MAX_SLICE_CHARS} chars per call — pass start/end to page through "
        "longer bodies."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref_id": {
                "type": "string",
                "description": "The ref_id from a truncation marker.",
            },
            "start": {
                "type": "integer",
                "description": "Inclusive start offset in chars (default 0).",
            },
            "end": {
                "type": "integer",
                "description": (
                    f"Exclusive end offset (default start + {MAX_SLICE_CHARS}). "
                    "Slice will be clamped to the body length."
                ),
            },
        },
        "required": ["ref_id"],
    },
}
