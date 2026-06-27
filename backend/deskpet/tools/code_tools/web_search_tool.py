# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""web_search — DuckDuckGo HTML scrape.

We picked DDG because it doesn't require an API key (Bing/Google do)
and the HTML is unauthenticated + relatively stable. The endpoint we
hit is the user-facing ``/html/`` lite version which returns clean
``<a class="result__a">`` links and ``<a class="result__snippet">``
descriptions.

Failure mode: when DDG changes its HTML class names or rate-limits us,
we return ``{"results": [], "error": "..."}``. The LLM should retry or
fall back to whatever knowledge it already has — this is a best-effort
tool.

We ALWAYS truncate to ``max_results`` (default 5, hard cap 10) so the
LLM doesn't burn tokens on a wall of search noise.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def web_search(args: dict[str, Any], task_id: str = "") -> str:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return json.dumps({"error": "query (string) is required"})

    # Delegate to the unified provider (region-aware DDG; same engine,
    # one parser, Chinese queries now hit 中文区). Output shape unchanged
    # so Code-mode callers / tests keep working.
    from deskpet.tools import search_provider

    result = search_provider.search(query, max_results=args.get("max_results", 5))
    return json.dumps(
        {
            "query": result.get("query", query),
            "count": result.get("count", 0),
            "results": result.get("results", []),
            **({"error": result["error"]} if result.get("error") else {}),
        },
        ensure_ascii=False,
    )
