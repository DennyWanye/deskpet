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


# WI-T4.1 v3 D11: 显式 conflict 错误（registry spec gap 修复）。
# 历史 ``registry.register replaces on duplicate`` 是反模式 — late-loaded
# stubs 会无声覆盖真实现，错误极难调试（last-mile / stage2 都踩过）。
# v3 行为：name 冲突时若双方都未 opt-in `replace_allowed=True` → 抛此异常；
# 任一方 opt-in 则允许覆盖（warn）。
class ToolNameConflictError(RuntimeError):
    """Raised when registering a tool name that already exists and neither
    the existing spec nor the new registration opt-in `replace_allowed=True`.

    Typical fix: pass `replace_allowed=True` explicitly (for test fixtures,
    MCP hot-replace, or stubs.py guard-mode), or rename one of the tools.
    """


def _envelope_indicates_success(handler_result: str) -> bool:
    """P5-S2 Phase 3: was the handler call a "real" success for breaker
    accounting?

    Handlers return JSON strings. By Phase 0 convention, the structured
    payload uses ``{"ok": false, "error": "..."}`` for known failure
    modes (missing param / would_overwrite / not_found / etc) even when
    no Python exception was raised. We treat those as breaker failures
    — otherwise an LLM that keeps invoking ``write_file`` with no
    ``path`` parameter would never trip the breaker.

    Anything that doesn't parse as JSON / isn't a dict / has no ``ok``
    field defaults to True (success) — handlers that pre-date Phase 0
    just return raw strings and we don't want to false-trip on them.
    """
    if not isinstance(handler_result, str):
        return True
    try:
        payload = json.loads(handler_result)
    except (ValueError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    ok = payload.get("ok")
    if ok is False:
        return False
    return True


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
    # P5-S1: per-tool hard timeout in seconds. ``execute_tool`` enforces
    # via ``asyncio.wait_for``. Defaults to 60s; bash_run / long-running
    # MCP tools should override on registration (e.g. 300.0).
    timeout_seconds: float = 60.0
    # WI-T4.1 v3 D11: explicit consent for "this name may be replaced".
    # ToolNameConflictError 抛 iff 同名重复注册且两边都没 opt-in。
    # 历史 stubs.py "register replaces on duplicate" 行为现改为：
    #   - stubs.py 用 "if not registry.has(name): register"（守卫模式）
    #   - 真实现注册（默认 replace_allowed=False）→ 不会被 stub 覆盖
    #   - mcp_call / 测试热替换等显式 replace_allowed=True 时合法覆盖
    replace_allowed: bool = False

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


def _run_coro_sync(coro: Any) -> Any:
    """把一个 coroutine 在 sync 上下文里跑到底，返回其结果。

    记忆系统升级 WI-M1.6：``dispatch()`` 是 sync 的，但 file 工具 handler
    改成了 async。无 running loop（测试 / smoke / 遗留 sync 调用）→ 直接
    ``asyncio.run``；万一在 running loop 里被调到（不应发生 —— 生产 async
    路走 V2 ``execute_tool``）→ 丢进独立线程各自起 loop 跑，避免
    "loop already running"。
    """
    import asyncio as _asyncio

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(coro)
    import concurrent.futures as _cf

    with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
        return _ex.submit(lambda: _asyncio.run(coro)).result()


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
        # P4-S22: per-session tool-arg context. ``execute_tool`` merges
        # this dict into the LLM-supplied params before invoking the
        # handler, so tools like ``glob`` / ``grep`` can read
        # ``_project_root`` without the LLM having to repeat it every
        # call. Keys conventionally start with underscore so they don't
        # collide with LLM-supplied ones.
        self._session_context: dict[str, dict[str, Any]] = {}
        # P5-S2 Phase 3: optional circuit breaker. When set, every
        # ``execute_tool`` call first asks the breaker whether the tool
        # is currently allowed for this session, and records the outcome
        # afterwards. Defaults to None for backward compat with tests
        # and legacy callers that don't want breaker semantics.
        self._breaker = None  # type: Optional[Any]  # ToolCircuitBreaker
        # WI-T1.1 (last-mile): optional ToolsConfig provider for D1 信封包装。
        # 默认 None → execute_tool 不加 artifacts 键（BC + 字节级一致硬保证）。
        # 启动期 main.py 调 set_tools_config_provider(lambda: cfg.tools)。
        self._tools_config_provider: Optional[Callable[[], Any]] = None
        # WI-T2.2 (last-mile P0 修): optional ReceiptStore provider 真正插电。
        # 默认 None → 不产 receipt（BC）；main.py 启动时按 cfg.verifier.emit_receipts
        # 决定是否构造 ReceiptStore 并注入。
        self._receipt_store_provider: Optional[Callable[[], Any]] = None
        # WI-T2.3 session iteration 计数 (per session_id) — receipt.iteration 字段
        self._session_iteration: dict[str, int] = {}

    def set_permission_gate(self, gate) -> None:  # type: ignore[no-untyped-def]
        """Wire a PermissionGate. Called once at backend startup."""
        self._gate = gate

    def set_tools_config_provider(self, provider) -> None:  # type: ignore[no-untyped-def]
        """WI-T1.1 wire a callable returning current ``ToolsConfig``.

        Provider 模式（不直传 cfg）允许 runtime 切换 flag 而无需重启 registry。
        provider 返回值需有 ``.last_mile.artifact_envelope`` 属性（ducktype）；
        返回 None 时按 BC 路径（不包装 envelope）。
        """
        self._tools_config_provider = provider

    def set_receipt_store_provider(self, provider) -> None:  # type: ignore[no-untyped-def]
        """WI-T2.2 P0 修：wire a callable returning current ``ReceiptStore`` (or None).

        与 set_tools_config_provider 同模式：provider 返回 None → BC 路径
        不产 receipt；ReceiptStore 已构造时每次 execute_tool 都 emit。
        """
        self._receipt_store_provider = provider

    def set_circuit_breaker(self, breaker) -> None:  # type: ignore[no-untyped-def]
        """P5-S2 Phase 3: wire a :class:`agent.circuit_breaker.ToolCircuitBreaker`.

        After this is set, ``execute_tool`` consults it before invoking
        each handler and records every outcome. Pass ``None`` to detach
        (mostly useful in tests).
        """
        self._breaker = breaker

    def set_session_context(
        self,
        session_id: str,
        context: dict[str, Any] | None,
    ) -> None:
        """P4-S22 — bind extra args injected into every tool call for
        this session. Pass None to clear. Typical use: chat handler
        sets ``{"_project_root": str(project_root)}`` when entering
        Code mode; clears it on exit.
        """
        if context is None:
            self._session_context.pop(session_id, None)
        else:
            self._session_context[session_id] = dict(context)

    def get_session_context(self, session_id: str) -> dict[str, Any]:
        """Read-only snapshot of the session's tool-arg context."""
        return dict(self._session_context.get(session_id, {}))

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
        timeout_seconds: float = 60.0,
        replace_allowed: bool = False,
    ) -> None:
        """Register a single tool.

        Name conflict policy (WI-T4.1 v3):
          - 默认 ``replace_allowed=False``: 重复 name 且 existing spec 也未
            opt-in → 抛 :class:`ToolNameConflictError`
          - 任一方 (existing 或 new) ``replace_allowed=True`` → 允许覆盖
            （仅 log.warning）
          - stubs.py 应改用守卫模式 ``if not registry.has(name): register``，
            真实现注册时不再被 stub 覆盖

        The ``schema`` argument is the raw OpenAI ``function`` object
        (``{name, description, parameters}``). ``schemas()`` wraps each
        with the outer ``{type: "function", function: ...}`` envelope,
        so callers don't need to repeat it here.

        Args:
            replace_allowed: 显式声明"这个名字允许被覆盖"。两边都未 opt-in
                时同名注册 raise；仅一边 True 也允许覆盖（用于 MCP 热重连 /
                测试 fixture / stubs.py 守卫模式）。
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
            timeout_seconds=float(timeout_seconds),
            replace_allowed=replace_allowed,
        )
        with self._lock:
            existing = self._tools.get(name)
            if existing is not None:
                # WI-T4.1 v3 D11: 同名重注册策略
                if not (existing.replace_allowed or replace_allowed):
                    raise ToolNameConflictError(
                        f"Tool {name!r} already registered "
                        f"(toolset={existing.toolset}, source={existing.source}). "
                        f"Set replace_allowed=True on either registration to "
                        f"allow override (e.g. stubs.py guard-mode), or rename "
                        f"one of the tools."
                    )
                logger.warning(
                    "tool %r re-registered (toolset=%s → %s, source=%s → %s); "
                    "previous definition replaced (replace_allowed opt-in)",
                    name, existing.toolset, toolset, existing.source, source,
                )
            self._tools[name] = spec

    def has(self, name: str) -> bool:
        """Return True iff a tool with this name is currently registered.

        Stubs.py 守卫模式：``if not registry.has(name): register(...)``
        防止 late-loaded stub 覆盖 already-registered 真实现。
        """
        with self._lock:
            return name in self._tools

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
          3. ★v3 WI-T5.1：``cfg.tools.disabled_toolsets`` — 强 strict 模式
             过滤；同时 schema 层 + execute_tool 层都挡（默认双层）。
          4. ★v3 WI-T5.1：``cfg.tools.disabled_toolsets_schema_only`` —
             opt-in 仅 schema 层挡（execute_tool 仍可调）。
          5. ★v3 WI-T5.1：``cfg.tools.dangerous_tools_allowlist`` —
             非空时过 dangerous=True 工具白名单。
        """
        allowed: Optional[set[str]] = (
            set(enabled_toolsets) if enabled_toolsets is not None else None
        )

        # WI-T5.1 v3：cfg.tools 字段（cfg provider 已注入）
        cfg_disabled: set[str] = set()
        cfg_disabled_schema_only: set[str] = set()
        dangerous_allowlist: set[str] = set()
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                cfg_disabled = set(getattr(cfg, "disabled_toolsets", []) or [])
                cfg_disabled_schema_only = set(
                    getattr(cfg, "disabled_toolsets_schema_only", []) or []
                )
                dangerous_allowlist = set(
                    getattr(cfg, "dangerous_tools_allowlist", []) or []
                )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("tools_config_provider read failed in schemas: %s", _exc)

        with self._lock:
            specs = list(self._tools.values())

        out: list[dict[str, Any]] = []
        for spec in specs:
            if not spec.env_satisfied():
                continue
            if allowed is not None and spec.toolset not in allowed:
                continue
            # WI-T5.1 v3：cfg.disabled_toolsets / _schema_only 双层挡
            if spec.toolset in cfg_disabled:
                continue
            if spec.toolset in cfg_disabled_schema_only:
                continue
            # WI-T5.1 v3：dangerous allowlist — 非空时仅 allowlist 中 dangerous 工具
            if dangerous_allowlist and spec.dangerous and spec.name not in dangerous_allowlist:
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
            # 记忆系统升级 WI-M1.6：file_read/file_write handler 改成
            # async（直接 await record_action）。sync 的 dispatch() 路径
            # （遗留 fallback + 测试 + smoke 脚本）需把 coroutine 跑到底。
            # 生产 code-mode 走 V2 registry.execute_tool（原生 async 分流），
            # 不经此处。
            import inspect as _inspect2
            if _inspect2.iscoroutine(result):
                result = _run_coro_sync(result)
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

        # WI-T5.1 v3：disabled_toolsets 双层挡 — strict 模式下 execute_tool
        # 也拒绝（schema_only 仅 schemas() 过滤，execute_tool 仍可调）。
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                disabled_strict = set(getattr(cfg, "disabled_toolsets", []) or [])
                if spec.toolset in disabled_strict:
                    return {
                        "ok": False,
                        "result": None,
                        "error": (
                            f"tool {name!r} disabled by [tools] "
                            f"disabled_toolsets (toolset={spec.toolset})"
                        ),
                    }
            except Exception as _exc:  # noqa: BLE001
                logger.warning(
                    "tools_config_provider read failed in execute_tool: %s", _exc,
                )

        # P5-S2 Phase 3: per-(session, tool) circuit breaker. If the
        # breaker is OPEN we synthesize a structured ``circuit_open``
        # envelope so the LLM sees a real tool_result with a hint and
        # alternatives — much better than silently retrying the broken
        # tool until max_iterations.
        if self._breaker is not None:
            allowed = await self._breaker.can_call(session_id, name)
            if not allowed:
                return await self._build_circuit_open_envelope(name, session_id, spec)

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

        # P4-S22: merge per-session context into params. LLM-supplied
        # values win on key collision so the LLM can override (e.g.
        # explicitly passing ``path`` to glob the user's home dir
        # instead of the project root).
        merged_params: dict[str, Any] = {}
        merged_params.update(self._session_context.get(session_id, {}))
        merged_params.update(dict(params or {}))

        # P4-S22: run sync handlers in a thread executor. Some new
        # Code-mode tools (todo_write, agent) need to bridge sync→async
        # via ``asyncio.run_coroutine_threadsafe``, which deadlocks when
        # called from the main event-loop thread (the handler blocks
        # waiting for a coro that can't dispatch because the thread is
        # blocked). Running every sync handler in a worker thread is
        # cheap and uniformly safe; handlers that are already async
        # (rare) get awaited directly.
        # P5-S1: tool-level hard timeout. Default 60s; specific tools may
        # override via ``ToolSpec.timeout_seconds`` (e.g. bash_run = 300s).
        # On timeout: return a uniform ``tool_timeout`` error envelope so
        # the agent loop carries on instead of dying with TimeoutError.
        #
        # WI-T2.3 v3 P0 修：dispatch 真实开始时间。原 emit_receipt 处用了两次
        # datetime.now() → duration_ms 永远 ~0μs（last-mile round2 P0-3）。
        # 这里捕真 started_at，emit_receipt 时用它对账 ended_at。
        from datetime import datetime as _dt, timezone as _tz
        _started_at = _dt.now(_tz.utc)
        try:
            import asyncio as _asyncio
            import inspect as _inspect

            # WI-T5.1 v3 default_timeout_seconds：cfg 兜底 ToolSpec 未配 timeout
            # 时的默认值。ToolSpec.timeout_seconds 默认 60.0，cfg 60.0 → 与
            # 现状字节级一致；用户在 [tools] default_timeout_seconds=30 时
            # 全局缩短未显式 override 的工具 timeout。
            cfg_default_timeout = 60.0
            if self._tools_config_provider is not None:
                try:
                    cfg = self._tools_config_provider()
                    cfg_default_timeout = float(
                        getattr(cfg, "default_timeout_seconds", 60.0) or 60.0
                    )
                except Exception:  # noqa: BLE001
                    pass
            timeout_s = float(getattr(spec, "timeout_seconds", cfg_default_timeout)) or cfg_default_timeout

            async def _run_handler() -> Any:
                if _inspect.iscoroutinefunction(spec.handler):
                    return await spec.handler(merged_params, task_id)
                loop = _asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None, spec.handler, merged_params, task_id
                )

            try:
                result = await _asyncio.wait_for(_run_handler(), timeout=timeout_s)
            except _asyncio.TimeoutError:
                logger.warning(
                    "execute_tool %r timed out after %.1fs", name, timeout_s
                )
                if self._breaker is not None:
                    await self._breaker.record_call(session_id, name, ok=False)
                return {
                    "ok": False,
                    "result": None,
                    "error": f"tool_timeout: {name} exceeded {timeout_s:.0f}s",
                }
        except Exception as exc:  # noqa: BLE001 — uniform error envelope
            err = f"{type(exc).__name__}: {exc}"
            logger.info("execute_tool %r raised: %s", name, err)
            if self._breaker is not None:
                await self._breaker.record_call(session_id, name, ok=False)
            return {"ok": False, "result": None, "error": err}

        if not isinstance(result, str):
            try:
                result = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                envelope_bad = {
                    "ok": False,
                    "result": None,
                    "error": f"handler returned non-JSON value: {exc}",
                }
                if self._breaker is not None:
                    await self._breaker.record_call(session_id, name, ok=False)
                return envelope_bad
        envelope = {"ok": True, "result": result, "error": None}

        # WI-T1.1 last-mile: 信封包装（PRD §3 D1）。
        # 仅在 provider 已设 + cfg.tools.last_mile.artifact_envelope=True
        # 且 result 含可推断 path/url 时，追加 ``artifacts`` 键。
        # 字节级一致硬保证：flag OFF 时 envelope dict 不含 ``artifacts`` 键
        # （不是空数组，是缺键 —— 见 TG-2 T2-5b）。
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                envelope_on = bool(
                    getattr(getattr(cfg, "last_mile", None),
                            "artifact_envelope", False)
                )
                if envelope_on:
                    from deskpet.tools.artifact import maybe_add_artifacts
                    envelope = maybe_add_artifacts(
                        envelope=envelope, tool_name=name, enable=True,
                    )
            except Exception as _exc:  # noqa: BLE001 — never break dispatch
                logger.warning(
                    "tools_config_provider raised in execute_tool %r: %s",
                    name, _exc,
                )

        # WI-T2.2 P0 修：emit receipt（PRD §3 D5 + 二轮 P0-1 接电）。
        # ReceiptStore 在 main.py 启动期按 cfg.verifier.emit_receipts 构造并
        # set_receipt_store_provider 注入；未注入则 BC 路径不产 receipt。
        if self._receipt_store_provider is not None:
            try:
                store = self._receipt_store_provider()
                if store is not None:
                    from deskpet.tools.receipt_store import emit_receipt
                    self._session_iteration[session_id] = (
                        self._session_iteration.get(session_id, 0) + 1
                    )
                    iteration = self._session_iteration[session_id]
                    # use envelope.ok 表示工具是否成功（即便 handler 返了
                    # ok=False 也算 dispatch 完成 - 而 envelope.ok=True 仅
                    # 表示 dispatch 路径没有异常）
                    envelope_ok = envelope.get("ok") is True
                    # WI-T2.3 v3 P0 修：用真实 _started_at（dispatch 开始时记
                    # 录）+ now() 算 duration_ms。原 v2.1 用两次 now() 间隔仅
                    # 微秒，导致 receipt duration_ms ~0 → p95 监控失效。
                    emit_receipt(
                        store,
                        tool_name=name,
                        args=dict(merged_params or {}),
                        started_at=_started_at,
                        ended_at=_dt.now(_tz.utc),
                        ok=envelope_ok,
                        session_id=session_id,
                        iteration=iteration,
                    )
            except Exception as _exc:  # noqa: BLE001 — never break dispatch
                logger.warning(
                    "receipt_store_provider raised in execute_tool %r: %s",
                    name, _exc,
                )

        # P5-S2 Phase 3: record success/failure to the breaker. The
        # handler returned a JSON string (typically an envelope itself)
        # — if THAT envelope says ``ok=false`` we count it as a failure
        # even though the Python call didn't raise.
        if self._breaker is not None:
            outcome_ok = _envelope_indicates_success(result)
            await self._breaker.record_call(session_id, name, ok=outcome_ok)
        return envelope

    async def _build_circuit_open_envelope(
        self, name: str, session_id: str, spec: ToolSpec
    ) -> dict[str, Any]:
        """Synthesize the dispatch envelope returned when the breaker
        is OPEN. Includes a Chinese hint + the list of sibling tools in
        the same toolset so the LLM has something concrete to fall back
        to.
        """
        # Cooldown remaining (best-effort; breaker may not expose it).
        cooldown_left: Optional[float] = None
        if hasattr(self._breaker, "cooldown_remaining"):
            try:
                cooldown_left = await self._breaker.cooldown_remaining(  # type: ignore[union-attr]
                    session_id, name
                )
            except Exception:  # noqa: BLE001 — never break dispatch over this
                cooldown_left = None

        # Find sibling tools (same toolset, different name). Skip env-
        # gated tools — they wouldn't survive ``schemas()`` either.
        with self._lock:
            siblings = [
                s.name
                for s in self._tools.values()
                if s.toolset == spec.toolset
                and s.name != name
                and s.env_satisfied()
            ]
        siblings.sort()

        if cooldown_left is not None and cooldown_left > 0:
            cooldown_str = f"剩余 {cooldown_left:.0f} 秒"
        else:
            cooldown_str = "请稍后重试"
        hint = (
            f"{name} 连续失败 3 次已熔断 ({cooldown_str})。"
            "检查参数或换个工具。"
        )
        result_payload = {
            "ok": False,
            "error": "circuit_open",
            "hint": hint,
            "available_alternatives": siblings,
        }
        return {
            "ok": False,
            "result": json.dumps(result_payload, ensure_ascii=False),
            "error": "circuit_open",
        }

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
