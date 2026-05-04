"""P4-S5: the ToolRegistry singleton (tool-framework spec).

Design notes
------------

* **Auto-discovery**: ``deskpet/tools/__init__.py`` walks the package's
  submodules with ``pkgutil.iter_modules`` and imports each one so their
  top-level ``registry.register(...)`` calls land in the singleton. Tool
  authors never touch ``__init__.py`` — drop a new ``foo_tool.py`` and
  call ``register`` at module scope.

* **OpenAI function-calling format**: ``schemas()`` emits the exact
  ``{type: "function", function: {name, description, parameters}}``
  shape that anthropic/openai/gemini adapters all normalize against
  (see spec "OpenAI-Format Tool Schemas").

* **Toolset gating**: every tool belongs to a ``toolset`` string
  (e.g. ``"file"``, ``"web"``, ``"memory"``, ``"control"``). The
  ContextAssembler passes ``enabled_toolsets=[...]`` at turn start so
  only the task-relevant slice shows up in the LLM prompt.

* **Env + check gating**: ``requires_env=["BRAVE_API_KEY"]`` hides the
  tool entirely when any var is missing. ``check_fn`` runs just before
  dispatch and, on False, short-circuits with a retriable error JSON —
  used by ``memory_search`` while the BGE-M3 embedder is still warming.

* **Error contract**: every dispatch path — whether the handler succeeds,
  returns an error dict, raises, or is gated by check_fn — MUST return a
  JSON **string**. Callers never need to unwrap Python exceptions; they
  feed the string straight back to the LLM tool-result turn.

* **Thread safety**: registration may happen during import on any thread
  (e.g. a background prefetch that imports ``deskpet.tools``), and
  ``dispatch`` is called from the agent loop. A ``threading.Lock``
  serializes registration and the registry-read portion of dispatch.
  Handler execution runs **outside** the lock so slow tools never block
  other dispatches.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .error_classifier import classify as _classify_retriable

logger = logging.getLogger(__name__)


# A tool handler receives the JSON-decoded args dict + a correlation
# ``task_id`` (used for tracing / observability; "" when the caller
# doesn't have one handy) and MUST return a JSON-encodable string.
ToolHandler = Callable[[dict[str, Any], str], str]
CheckFn = Callable[[], bool]


@dataclass(frozen=True)
class ToolSpec:
    """Immutable bundle of everything needed to expose + run a tool.

    Kept ``frozen=True`` so a stray ``spec.handler = ...`` typo at call
    site fails loudly instead of silently replacing a registered tool.

    P4-S20 v2 additions (all optional, backward-compatible defaults):

    * ``permission_category`` — one of the 7 categories from the
      permission-gate spec. Defaults to ``"read_file"`` (the safest
      default-allow category) so legacy tools registered without the
      kwarg keep dispatching unchanged.
    * ``source`` — provenance string used by audit + uninstall. Format:
      ``"builtin"`` | ``"plugin:<name>"`` | ``"mcp:<server>"``.
    * ``dangerous`` — UI hint to render the popup in red.
    """

    name: str
    toolset: str
    schema: dict[str, Any]
    handler: ToolHandler
    check_fn: Optional[CheckFn] = None
    requires_env: list[str] = field(default_factory=list)
    permission_category: str = "read_file"
    source: str = "builtin"
    dangerous: bool = False

    def env_satisfied(self) -> bool:
        """True iff every ``requires_env`` var is present AND non-empty."""
        return all(os.environ.get(e) for e in self.requires_env)

    @property
    def description_for_llm(self) -> str:
        """Convenience accessor — pulls ``description`` out of the OpenAI
        function schema. v2 callers can read this without poking into the
        schema dict."""
        return str(self.schema.get("description", ""))

    @property
    def input_schema_json(self) -> dict[str, Any]:
        """Convenience accessor — pulls ``parameters`` out of the schema."""
        return dict(self.schema.get("parameters", {}))


class ToolRegistry:
    """Process-wide singleton for tool registration + dispatch.

    Don't instantiate directly in application code — import the module
    level ``registry`` instance instead. Tests do instantiate fresh
    ``ToolRegistry()`` objects to avoid polluting the global one.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._lock = threading.Lock()
        # P4-S20: optional permission gate. When set, ``execute_tool``
        # awaits ``gate.check(...)`` before running the handler. Tests
        # and legacy ``dispatch()`` paths leave it unset (no gating).
        self._gate = None  # type: Optional[Any]  # PermissionGate

    def set_permission_gate(self, gate) -> None:  # type: ignore[no-untyped-def]
        """Wire a PermissionGate. Called once at backend startup."""
        self._gate = gate

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: ToolHandler,
        *,
        check_fn: Optional[CheckFn] = None,
        requires_env: Optional[list[str]] = None,
        permission_category: str = "read_file",
        source: str = "builtin",
        dangerous: bool = False,
    ) -> None:
        """Register a single tool. Idempotent replace on duplicate name
        (with a log warning — usually a symptom of module reload during
        tests or hot-reload in dev; never expected in production).

        The ``schema`` argument is the raw OpenAI ``function`` object
        (``{name, description, parameters}``). ``schemas()`` wraps each
        with the outer ``{type: "function", function: ...}`` envelope,
        so callers don't need to repeat it here.
        """
        if not name or not isinstance(name, str):
            raise ValueError(f"tool name must be non-empty str, got {name!r}")
        if not toolset or not isinstance(toolset, str):
            raise ValueError(f"toolset must be non-empty str, got {toolset!r}")
        if not isinstance(schema, dict):
            raise TypeError(f"schema must be dict, got {type(schema).__name__}")
        if not callable(handler):
            raise TypeError("handler must be callable")

        spec = ToolSpec(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            requires_env=list(requires_env or []),
            permission_category=permission_category,
            source=source,
            dangerous=dangerous,
        )
        with self._lock:
            if name in self._tools:
                logger.warning(
                    "tool %r re-registered (toolset=%s); previous definition "
                    "replaced",
                    name,
                    toolset,
                )
            self._tools[name] = spec

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed, False if
        the name was absent. Used by MCPManager to drop a server's
        tools on disconnect (P4-S9 task 14.5 + 14.6).
        """
        with self._lock:
            return self._tools.pop(name, None) is not None

    # ------------------------------------------------------------------
    # Schema export
    # ------------------------------------------------------------------
    def schemas(
        self, enabled_toolsets: Optional[list[str]] = None
    ) -> list[dict[str, Any]]:
        """Return OpenAI-format schema list.

        Filtering rules (applied in order):
          1. ``requires_env`` — any missing/empty env var hides the tool
             so the LLM never sees a feature it can't invoke.
          2. ``enabled_toolsets`` — if provided, only tools whose
             ``toolset`` is in the whitelist survive. ``None`` (the
             default) returns everything.
        """
        allowed: Optional[set[str]] = (
            set(enabled_toolsets) if enabled_toolsets is not None else None
        )
        with self._lock:
            specs = list(self._tools.values())

        out: list[dict[str, Any]] = []
        for spec in specs:
            if not spec.env_satisfied():
                continue
            if allowed is not None and spec.toolset not in allowed:
                continue
            out.append({"type": "function", "function": dict(spec.schema)})
        return out

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def dispatch(
        self, name: str, args: dict[str, Any], task_id: str = ""
    ) -> str:
        """Invoke a tool by name. Always returns a JSON string.

        * Unknown tool → ``{"error":"unknown tool: <name>","retriable":false}``
        * ``check_fn`` returns False → retriable ``tool not ready`` error
        * Handler raises → ``{"error":"<ExcClass>: <msg>","retriable":<classified>}``
        * Handler returns non-string → stringified via ``json.dumps``;
          already-string return passed through verbatim (handlers are
          expected to produce valid JSON, but we don't re-parse it —
          re-serializing a valid JSON string would wrap it in quotes).

        Handler execution runs outside the internal lock so a slow tool
        (e.g. ``web_fetch``) never blocks another dispatch on a different
        thread.
        """
        with self._lock:
            spec = self._tools.get(name)

        if spec is None:
            return json.dumps(
                {"error": f"unknown tool: {name}", "retriable": False}
            )

        if spec.check_fn is not None:
            try:
                ready = bool(spec.check_fn())
            except Exception as exc:  # noqa: BLE001 — check_fn must never break dispatch
                logger.warning(
                    "tool %r check_fn raised %s; treating as not-ready",
                    name,
                    type(exc).__name__,
                )
                ready = False
            if not ready:
                return json.dumps(
                    {
                        "error": f"tool not ready: {name}",
                        "retriable": True,
                    }
                )

        try:
            result = spec.handler(dict(args or {}), task_id)
        except Exception as exc:  # noqa: BLE001 — everything caught by design
            retriable = _classify_retriable(exc)
            err = f"{type(exc).__name__}: {exc}"
            logger.info(
                "tool %r raised (retriable=%s): %s", name, retriable, err
            )
            return json.dumps({"error": err, "retriable": retriable})

        if isinstance(result, str):
            return result
        # Handlers are expected to return strings; accept dict/list as
        # a convenience and serialize. Anything not JSON-encodable
        # surfaces as a non-retriable error (it's a programmer bug in
        # the handler).
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return json.dumps(
                {
                    "error": f"handler returned non-JSON value: {exc}",
                    "retriable": False,
                }
            )

    # ------------------------------------------------------------------
    # P4-S20 v2: tool_use protocol schema generation + gated execution
    # ------------------------------------------------------------------
    def to_openai_schema(
        self,
        names: Optional[list[str]] = None,
        filter_categories: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """OpenAI function-calling schema list.

        Equivalent to ``schemas()`` but with v2 filters:
        * ``names`` — explicit allowlist by tool name
        * ``filter_categories`` — restrict to tools whose
          ``permission_category`` is in this list (used by safe-mode)
        """
        with self._lock:
            specs = list(self._tools.values())
        out: list[dict[str, Any]] = []
        name_set = set(names) if names is not None else None
        cat_set = set(filter_categories) if filter_categories is not None else None
        for spec in specs:
            if not spec.env_satisfied():
                continue
            if name_set is not None and spec.name not in name_set:
                continue
            if cat_set is not None and spec.permission_category not in cat_set:
                continue
            out.append({"type": "function", "function": dict(spec.schema)})
        return out

    def to_anthropic_schema(
        self,
        names: Optional[list[str]] = None,
        filter_categories: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Anthropic Messages API tool schema list.

        Anthropic uses ``input_schema`` (not OpenAI's ``parameters``).
        """
        with self._lock:
            specs = list(self._tools.values())
        out: list[dict[str, Any]] = []
        name_set = set(names) if names is not None else None
        cat_set = set(filter_categories) if filter_categories is not None else None
        for spec in specs:
            if not spec.env_satisfied():
                continue
            if name_set is not None and spec.name not in name_set:
                continue
            if cat_set is not None and spec.permission_category not in cat_set:
                continue
            out.append(
                {
                    "name": spec.name,
                    "description": spec.description_for_llm,
                    "input_schema": spec.input_schema_json,
                }
            )
        return out

    # Ollama uses the OpenAI-compatible shape; alias for clarity.
    to_ollama_schema = to_openai_schema

    async def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str = "",
    ) -> dict[str, Any]:
        """Permission-gated async tool execution.

        Wraps the legacy ``dispatch()`` with three guarantees:
          1. Looks up the spec; unknown tool → ``{ok: False, error: ...}``
          2. Awaits ``PermissionGate.check`` (if a gate is wired). Deny
             → handler is NOT called.
          3. Runs the handler under a try/except so handler exceptions
             surface as ``{ok: False, error: "..."}`` rather than
             propagating.

        Return shape: ``{"ok": bool, "result": str | None, "error": str | None}``.
        ``result`` is whatever the handler returned (typically a JSON string).
        """
        with self._lock:
            spec = self._tools.get(name)
        if spec is None:
            return {"ok": False, "result": None, "error": f"unknown tool: {name}"}

        if self._gate is not None:
            decision = await self._gate.check(
                category=spec.permission_category,
                params=params,
                session_id=session_id,
            )
            if not decision.allow:
                return {
                    "ok": False,
                    "result": None,
                    "error": f"permission denied (source={decision.source})",
                }

        try:
            result = spec.handler(dict(params or {}), task_id)
        except Exception as exc:  # noqa: BLE001 — uniform error envelope
            err = f"{type(exc).__name__}: {exc}"
            logger.info("execute_tool %r raised: %s", name, err)
            return {"ok": False, "result": None, "error": err}

        if not isinstance(result, str):
            try:
                result = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                return {
                    "ok": False,
                    "result": None,
                    "error": f"handler returned non-JSON value: {exc}",
                }
        return {"ok": True, "result": result, "error": None}

    # ------------------------------------------------------------------
    # Introspection helpers (tests + tool_search)
    # ------------------------------------------------------------------
    def list_tools(self, source: Optional[str] = None) -> list[str]:
        """All registered tool names (env-hidden tools INCLUDED).

        Distinct from ``schemas()`` which filters — this is the raw
        inventory, used by tests and by the observability dashboard.

        P4-S20: pass ``source="plugin:notion"`` to filter by provenance.
        """
        with self._lock:
            if source is None:
                return sorted(self._tools.keys())
            return sorted(
                n for n, s in self._tools.items() if s.source == source
            )

    def get(self, name: str) -> Optional[ToolSpec]:
        """Return the full spec for one tool, or None if absent.

        ``tool_search`` uses this to grab ``description`` for matching
        without going through the dispatch path.
        """
        with self._lock:
            return self._tools.get(name)

    def all_specs(self) -> list[ToolSpec]:
        """Return every ToolSpec, regardless of env gating. Used by
        ``tool_search`` so a missing ``BRAVE_API_KEY`` still surfaces
        the tool name in search results (agent can then prompt the user
        to set it)."""
        with self._lock:
            return list(self._tools.values())


# Module-level singleton. Import this in tool modules:
#
#     from deskpet.tools.registry import registry
#     registry.register("my_tool", ...)
registry = ToolRegistry()
