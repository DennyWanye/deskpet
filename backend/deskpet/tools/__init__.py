"""P4 tool framework + built-in tools (P4-S5, tasks 7.x / 8.x / 9.x).

Module layout
-------------

* ``registry.py``       — :class:`ToolRegistry` singleton with
  auto-discovery. Importing this package walks every sibling module in
  ``tools/*.py`` and loads it; each module's top-level
  ``registry.register(...)`` calls finish tool discovery.

* ``error_classifier.py`` — :func:`classify` decides whether a handler
  exception is retriable or permanent. Used by ``dispatch``.

* ``_config.py``        — TOML-only loader for ``[tools.web]``, kept
  local so tool modules don't drag in the full ``backend.config``
  dependency graph.

* ``tool_search.py``    — meta-tool ``tool_search`` for lazy schema
  lookup (CCB pattern). Registered at import-time.

* ``file_tools.py``     — 4 workspace-sandboxed tools: ``file_read /
  file_write / file_glob / file_grep``. All paths forced under
  ``%APPDATA%\\deskpet\\workspace\\`` with ``..`` traversal blocked.

* ``todo_tools.py``     — 2 todo tools (``todo_write / todo_complete``)
  backed by a JSON file (``todo.json``). SQLite version lands later.

* ``web_tools.py``      — zero-cost web toolkit (``web_fetch``,
  ``web_crawl``, ``web_extract_article``, ``web_read_sitemap``) built
  on ``httpx + trafilatura + selectolax``, shared politeness layer
  (robots.txt + per-host rate limit + 429/403 block cache).

* ``stubs.py``          — schemas-only stubs for features owned by
  future slices (memory_* / delegate / skill_invoke / mcp_call). The
  real implementations replace these registrations when their owning
  slice ships.

Brave / Tavily / Bing / exa.ai are intentionally absent (D9 decision,
reinforced by CI grep guard in task 9.9).
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

# Re-export registry so callers can ``from deskpet.tools import registry``.
from .registry import ToolRegistry, ToolSpec, registry  # noqa: F401

logger = logging.getLogger(__name__)

# Modules that the registry lives in but that must NOT be imported as
# tool providers (they either define the registry itself or are helpers).
_SKIP_SUBMODULES = {"registry", "error_classifier", "_config", "__init__"}


def _discover_and_load() -> None:
    """Walk this package's direct submodules and import each one so its
    top-level ``registry.register(...)`` calls execute.

    We iterate this package's ``__path__`` with ``pkgutil.iter_modules``
    — that's the standard "what's inside this package?" primitive and
    works under both source checkout and zipimport. Errors during a
    single submodule's import are logged but do NOT abort discovery;
    a broken ``weather_tool.py`` shouldn't take down the whole toolset.
    """
    for info in pkgutil.iter_modules(__path__, prefix=__name__ + "."):
        short = info.name.rsplit(".", 1)[-1]
        if short in _SKIP_SUBMODULES:
            continue
        if short.startswith("_") and short != "__init__":
            # Private helpers like ``_config`` already in skip list; keep
            # this as a safety net for future additions.
            continue
        if info.ispkg:
            # No nested tool packages expected; if one appears later we
            # can recurse, but for now the flat layout is intentional.
            continue
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "tool auto-discovery: failed to import %s (%s); skipping",
                info.name,
                type(exc).__name__,
            )


_discover_and_load()

__all__ = ["registry", "ToolRegistry", "ToolSpec"]
