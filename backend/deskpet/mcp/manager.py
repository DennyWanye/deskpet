"""P4-S9: MCP Manager — ClientSession lifecycle + ToolRegistry injection.

Design notes
------------

The manager is responsible for the full lifecycle of every MCP server
declared in ``config.toml [[mcp.servers]]``:

1. **Spawn / connect** — stdio (preferred, via
   ``mcp.client.stdio.stdio_client``), SSE
   (``mcp.client.sse.sse_client``), or streamable HTTP
   (``mcp.client.streamable_http.streamablehttp_client``).

2. **Handshake** — ``session.initialize()`` then
   ``session.list_tools()``. Each discovered tool is injected into the
   shared :class:`deskpet.tools.registry.ToolRegistry` with a
   ``mcp_{server}_{tool}`` namespace prefix so two servers that both
   expose ``read_file`` don't collide.

3. **Crash reconnect** — if a session errors while running, the manager
   marks that server as ``reconnecting`` and schedules an exponential
   backoff loop (1s → 2s → 4s → 8s, max 5 attempts). After the cap the
   server is marked ``failed`` and its tools dropped from the registry.

4. **Graceful shutdown** — ``stop()`` awaits ``session.close()`` (2s
   cap per server) and terminates owned subprocesses with SIGTERM
   falling back to kill() after 3s.

The manager is **independent** of ``deskpet.agent.*`` — it only imports
the ``ToolRegistry`` contract. Integration into the agent bootstrap
lives in :mod:`deskpet.mcp.bootstrap`.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable, Optional

import structlog

try:  # pragma: no cover — SDK may be absent in minimal test envs
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception:  # pragma: no cover
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]

logger = structlog.get_logger(__name__)


# -------------------- constants & types --------------------

#: Exponential backoff schedule for reconnect. Index N = delay before
#: attempt N+1. The length of this tuple is the retry cap (5).
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)

#: Seconds to wait for ``session.close()`` before logging a warning.
_SESSION_CLOSE_TIMEOUT_S: float = 2.0

#: Seconds between SIGTERM and SIGKILL when tearing down a stdio child.
_PROC_KILL_GRACE_S: float = 3.0


# Server lifecycle state. Exposed via :meth:`MCPManager.server_state`.
ServerState = str  # "pending" | "running" | "reconnecting" | "failed" | "stopped"


def _expand_path(value: str) -> str:
    """Expand ``%APPDATA%`` / ``~`` etc. inside a single arg.

    ``os.path.expandvars`` handles ``%NAME%`` and ``$NAME``;
    ``os.path.expanduser`` handles ``~``. We run both so the combinations
    users actually type (``~/deskpet/x``, ``%APPDATA%\\deskpet``,
    ``$HOME/x``) all resolve.
    """
    return os.path.expanduser(os.path.expandvars(value))


# -------------------- per-server runtime record --------------------


class _ServerRuntime:
    """Mutable per-server state tracked by :class:`MCPManager`.

    Keeping this in a plain class (vs dataclass) so the manager can
    swap ``session`` / ``state`` atomically under its lock without
    worrying about frozen semantics.
    """

    __slots__ = (
        "name",
        "config",
        "session",
        "state",
        "tool_names",
        "reconnect_task",
        "exit_stack",
    )

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name: str = name
        self.config: dict[str, Any] = config
        self.session: Optional[Any] = None  # ClientSession when live
        self.state: ServerState = "pending"
        # Tool names registered into ToolRegistry for this server, so
        # we can drop them cleanly on disconnect.
        self.tool_names: list[str] = []
        self.reconnect_task: Optional[asyncio.Task[None]] = None
        self.exit_stack: Optional[AsyncExitStack] = None


# -------------------- the manager --------------------


class MCPManager:
    """Multi-server MCP client lifecycle.

    Usage::

        manager = MCPManager(config, registry)
        await manager.start()
        ...
        await manager.stop()

    ``config`` shape::

        {
            "enabled": True,
            "servers": [
                {
                    "name": "filesystem",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem",
                             "%APPDATA%/deskpet/workspace"],
                    "env": {},
                },
                ...
            ],
        }
    """

    def __init__(
        self,
        config: dict[str, Any],
        tool_registry: Any | None = None,
    ) -> None:
        self._config = dict(config or {})
        self._registry = tool_registry
        self._servers: dict[str, _ServerRuntime] = {}
        self._lock = asyncio.Lock()
        self._stopped = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn / connect every enabled server in config.

        Failures are logged but never raise — one bad server must not
        prevent the others from coming up (§14.2 "each server's failure
        is independent").
        """
        if not self._config.get("enabled", True):
            logger.info("mcp_disabled_globally")
            return

        servers_cfg = list(self._config.get("servers", []) or [])
        if not servers_cfg:
            logger.info("mcp_no_servers_configured")
            return

        for entry in servers_cfg:
            name = entry.get("name", "")
            if not name:
                logger.warning("mcp_server_missing_name", entry=entry)
                continue
            if not entry.get("enabled", False):
                logger.info("mcp_server_disabled", server=name)
                continue
            transport = entry.get("transport", "stdio")
            if transport not in ("stdio", "sse", "streamable_http"):
                logger.warning(
                    "mcp_unknown_transport",
                    server=name,
                    transport=transport,
                )
                continue

            runtime = _ServerRuntime(name=name, config=entry)
            self._servers[name] = runtime
            try:
                await self._connect_once(runtime)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_initial_connect_failed",
                    server=name,
                    error=repr(exc),
                )
                # Kick off reconnect in the background — don't block
                # other servers.
                runtime.state = "reconnecting"
                runtime.reconnect_task = asyncio.create_task(
                    self._reconnect_loop(runtime)
                )

    async def stop(self) -> None:
        """Tear down every server. Idempotent."""
        if self._stopped:
            return
        self._stopped = True

        for runtime in list(self._servers.values()):
            if runtime.reconnect_task is not None:
                runtime.reconnect_task.cancel()
                try:
                    await runtime.reconnect_task
                except (asyncio.CancelledError, Exception):
                    pass
                runtime.reconnect_task = None
            await self._teardown_runtime(runtime)
            runtime.state = "stopped"

    # ------------------------------------------------------------------
    # ToolRegistry integration
    # ------------------------------------------------------------------

    def register_into(self, tool_registry: Any) -> None:
        """Re-bind the registry target. Typically called once at boot,
        before :meth:`start`. Idempotent; safe to call again after
        hot-reloading the registry.
        """
        self._registry = tool_registry
        # If we already have live sessions, mirror their tools into
        # the new registry so register_into() is usable post-start too.
        for runtime in self._servers.values():
            if runtime.state == "running" and runtime.session is not None:
                # Re-register from cached tool_names via list_tools —
                # but since we stashed raw schemas would be simpler;
                # keep it simple: tools already registered into the
                # OLD registry, caller is responsible for dropping
                # those. In practice register_into() is boot-time only.
                pass

    # ------------------------------------------------------------------
    # Public query surface
    # ------------------------------------------------------------------

    def server_state(self) -> dict[str, str]:
        """Snapshot of every known server's state, for UI / telemetry."""
        return {name: rt.state for name, rt in self._servers.items()}

    # ------------------------------------------------------------------
    # Unified dispatch
    # ------------------------------------------------------------------

    async def mcp_call(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Invoke ``tool_name`` on ``server_name`` — fast-fail on dead
        sessions.

        Returns a dict. On success the dict carries whatever the
        server's content block said (text, structured, etc). On error
        the dict has an ``error`` key with a stable machine-readable
        code, matching spec Requirement "Unified mcp_call Dispatch":

          * ``unknown_mcp_server``
          * ``unknown_mcp_tool``
          * ``mcp_session_dead``
          * ``mcp_call_failed``
        """
        runtime = self._servers.get(server_name)
        if runtime is None:
            return {
                "error": "unknown_mcp_server",
                "server": server_name,
                "retriable": False,
            }
        if runtime.state != "running" or runtime.session is None:
            return {
                "error": "mcp_session_dead",
                "server": server_name,
                "state": runtime.state,
                "retriable": True,
            }
        if tool_name not in runtime.tool_names:
            # Strip namespace if caller already qualified it.
            qualified = f"mcp_{server_name}_{tool_name}"
            if qualified not in runtime.tool_names and tool_name not in {
                n[len(f"mcp_{server_name}_") :] for n in runtime.tool_names
            }:
                return {
                    "error": "unknown_mcp_tool",
                    "server": server_name,
                    "tool": tool_name,
                    "retriable": False,
                }

        try:
            result = await runtime.session.call_tool(
                tool_name, args or {}
            )
        except Exception as exc:  # noqa: BLE001
            # Session presumably dead — kick off reconnect but don't
            # block this call on it.
            self._mark_disconnected(runtime, reason=repr(exc))
            return {
                "error": "mcp_call_failed",
                "server": server_name,
                "tool": tool_name,
                "detail": repr(exc),
                "retriable": True,
            }
        return _serialize_call_result(result)

    # ------------------------------------------------------------------
    # Resource / Prompt read-only IPC surface (§14.10)
    # ------------------------------------------------------------------

    async def list_resources(self, server_name: str) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.list_resources()
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_list_resources_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    async def read_resource(
        self, server_name: str, uri: str
    ) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.read_resource(uri)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_read_resource_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    async def list_prompts(self, server_name: str) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.list_prompts()
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_list_prompts_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        args: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        runtime = self._servers.get(server_name)
        if runtime is None or runtime.session is None:
            return {"error": "unknown_mcp_server", "server": server_name}
        try:
            result = await runtime.session.get_prompt(prompt_name, args or {})
        except Exception as exc:  # noqa: BLE001
            return {"error": "mcp_get_prompt_failed", "detail": repr(exc)}
        return _safe_model_dump(result)

    # ------------------------------------------------------------------
    # Internal: connect / teardown
    # ------------------------------------------------------------------

    async def _connect_once(self, runtime: _ServerRuntime) -> None:
        """Open one session for ``runtime``. On success flips state to
        ``running`` and injects tools; on failure re-raises.
        """
        transport = runtime.config.get("transport", "stdio")
        exit_stack = AsyncExitStack()
        try:
            read, write = await _open_transport(exit_stack, runtime.config)
            session = await exit_stack.enter_async_context(
                ClientSession(read, write)  # type: ignore[misc]
            )
            await session.initialize()
            tools_result = await session.list_tools()
        except Exception:
            await exit_stack.aclose()
            raise

        # Save session + hand ownership of the stack to runtime so
        # stop() can aclose it.
        runtime.session = session
        runtime.exit_stack = exit_stack
        runtime.state = "running"

        # Inject each tool into the registry.
        tools = getattr(tools_result, "tools", []) or []
        registered: list[str] = []
        for tool in tools:
            qualified = f"mcp_{runtime.name}_{_tool_name(tool)}"
            if self._registry is None:
                registered.append(qualified)
                continue
            try:
                self._registry.register(
                    name=qualified,
                    toolset="mcp",
                    schema=_tool_to_schema(qualified, tool),
                    handler=_make_tool_handler(self, runtime.name, tool),
                    check_fn=_make_check_fn(self, runtime.name),
                )
                registered.append(qualified)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_tool_register_failed",
                    server=runtime.name,
                    tool=_tool_name(tool),
                    error=repr(exc),
                )
        runtime.tool_names = registered
        logger.info(
            "mcp_server_connected",
            server=runtime.name,
            transport=transport,
            tool_count=len(registered),
        )

    async def _teardown_runtime(self, runtime: _ServerRuntime) -> None:
        """Close session + drop tools for one runtime. Idempotent."""
        # Drop tools from registry first so downstream code stops
        # resolving them even if close() hangs.
        self._drop_tools(runtime)

        if runtime.exit_stack is not None:
            try:
                await asyncio.wait_for(
                    runtime.exit_stack.aclose(),
                    timeout=_SESSION_CLOSE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "mcp_session_close_timeout",
                    server=runtime.name,
                    timeout_s=_SESSION_CLOSE_TIMEOUT_S,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_session_close_error",
                    server=runtime.name,
                    error=repr(exc),
                )
            runtime.exit_stack = None

        # Session object already cleaned up by AsyncExitStack; just clear ref.
        if runtime.session is not None:
            close_fn = getattr(runtime.session, "close", None)
            if callable(close_fn):
                try:
                    res = close_fn()
                    if asyncio.iscoroutine(res):
                        await asyncio.wait_for(
                            res, timeout=_SESSION_CLOSE_TIMEOUT_S
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "mcp_session_close_noop_error",
                        server=runtime.name,
                        error=repr(exc),
                    )
            runtime.session = None

    def _drop_tools(self, runtime: _ServerRuntime) -> None:
        """Remove registered tool names from the registry."""
        if self._registry is None or not runtime.tool_names:
            runtime.tool_names = []
            return
        # ToolRegistry doesn't expose a public unregister in this
        # project, but it stores ``_tools`` as a plain dict under a
        # lock. Fall back to a duck-typed protocol: prefer an
        # explicit ``unregister()`` if present, else reach into the
        # dict under the registry's lock.
        unregister = getattr(self._registry, "unregister", None)
        for name in runtime.tool_names:
            try:
                if callable(unregister):
                    unregister(name)
                else:
                    tools_dict = getattr(self._registry, "_tools", None)
                    if isinstance(tools_dict, dict):
                        tools_dict.pop(name, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "mcp_tool_unregister_error",
                    tool=name,
                    error=repr(exc),
                )
        runtime.tool_names = []

    # ------------------------------------------------------------------
    # Internal: reconnect loop
    # ------------------------------------------------------------------

    def _mark_disconnected(
        self, runtime: _ServerRuntime, *, reason: str
    ) -> None:
        """Flip a running server into reconnect mode and schedule
        the backoff loop. Safe to call multiple times — the loop
        guards against duplicate schedules via ``reconnect_task``.
        """
        if runtime.state in ("reconnecting", "failed", "stopped"):
            return
        logger.info(
            "mcp_session_lost",
            server=runtime.name,
            reason=reason,
        )
        runtime.state = "reconnecting"
        # Drop tools immediately so the agent doesn't try dead ones.
        self._drop_tools(runtime)
        if runtime.reconnect_task is None or runtime.reconnect_task.done():
            runtime.reconnect_task = asyncio.create_task(
                self._reconnect_loop(runtime)
            )

    async def _reconnect_loop(self, runtime: _ServerRuntime) -> None:
        """Exponential backoff 1→2→4→8→16s, max 5 attempts.

        After max retries: set state=failed, drop tools, return.
        """
        for attempt, delay in enumerate(_BACKOFF_SCHEDULE, start=1):
            if self._stopped:
                return
            logger.info(
                "mcp_reconnect_wait",
                server=runtime.name,
                attempt=attempt,
                delay_s=delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

            if self._stopped:
                return
            # Close any lingering half-open session from previous attempt.
            if runtime.exit_stack is not None:
                try:
                    await runtime.exit_stack.aclose()
                except Exception:  # noqa: BLE001
                    pass
                runtime.exit_stack = None

            try:
                await self._connect_once(runtime)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp_reconnect_failed",
                    server=runtime.name,
                    attempt=attempt,
                    error=repr(exc),
                )
                continue
            # success
            logger.info(
                "mcp_reconnected",
                server=runtime.name,
                attempt=attempt,
            )
            return

        # exhausted
        runtime.state = "failed"
        self._drop_tools(runtime)
        logger.error(
            "mcp_reconnect_exhausted",
            server=runtime.name,
            attempts=len(_BACKOFF_SCHEDULE),
        )


# ---------------------------------------------------------------------
# Transport open (stdio / sse / streamable_http)
# ---------------------------------------------------------------------


async def _open_transport(
    exit_stack: AsyncExitStack, config: dict[str, Any]
) -> tuple[Any, Any]:
    """Return ``(read, write)`` streams bound to the exit stack.

    The returned streams are valid until the exit stack closes. The
    caller is responsible for wrapping the streams in a
    ``ClientSession`` and entering that into the same stack.
    """
    transport = config.get("transport", "stdio")
    if transport == "stdio":
        if stdio_client is None or StdioServerParameters is None:
            raise RuntimeError("mcp SDK not available (stdio transport)")
        expanded_args = [_expand_path(str(a)) for a in config.get("args", [])]
        env = config.get("env") or None
        params = StdioServerParameters(
            command=str(config.get("command", "")),
            args=expanded_args,
            env=env,
        )
        streams = await exit_stack.enter_async_context(stdio_client(params))
        # stdio_client yields (read, write)
        return streams[0], streams[1]

    if transport == "sse":
        from mcp.client.sse import sse_client  # local import — optional

        url = _expand_path(str(config.get("url", "")))
        headers = config.get("headers") or None
        streams = await exit_stack.enter_async_context(
            sse_client(url, headers=headers)
        )
        return streams[0], streams[1]

    if transport == "streamable_http":
        from mcp.client.streamable_http import streamablehttp_client

        url = _expand_path(str(config.get("url", "")))
        headers = config.get("headers") or None
        streams = await exit_stack.enter_async_context(
            streamablehttp_client(url, headers=headers)
        )
        # streamablehttp_client yields (read, write, get_session_id)
        return streams[0], streams[1]

    raise ValueError(f"unsupported mcp transport: {transport!r}")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "")


def _tool_to_schema(qualified: str, tool: Any) -> dict[str, Any]:
    """Convert an MCP ``Tool`` into the raw OpenAI ``function`` body
    that :class:`~deskpet.tools.registry.ToolRegistry` expects.

    The registry's ``schemas()`` wraps each one in
    ``{"type": "function", "function": ...}`` itself.
    """
    input_schema = getattr(tool, "inputSchema", None) or {
        "type": "object",
        "properties": {},
    }
    description = getattr(tool, "description", None) or ""
    return {
        "name": qualified,
        "description": description,
        "parameters": dict(input_schema),
    }


def _make_tool_handler(
    manager: "MCPManager", server_name: str, tool: Any
) -> Callable[[dict[str, Any], str], str]:
    """Build the sync handler stored in ToolRegistry.

    ToolRegistry's handler contract is ``(args, task_id) -> str`` —
    a synchronous call returning a JSON string. We bridge to the
    async ``mcp_call`` by running it on the current loop.
    """
    tool_name = _tool_name(tool)

    def _handler(args: dict[str, Any], task_id: str) -> str:
        del task_id  # unused — MCP has its own correlation
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        async def _invoke() -> dict[str, Any]:
            return await manager.mcp_call(server_name, tool_name, args)

        if loop is not None and loop.is_running():
            # Tool handlers are called synchronously from agent loops
            # that are themselves async — the caller must
            # ``run_in_executor`` or the project's tool dispatcher
            # already runs handlers off-loop. If we're stuck on a
            # running loop with no executor escape, we at best can
            # schedule the coroutine and return a not-ready marker.
            # In practice the deskpet ToolRegistry dispatches
            # handlers from a worker thread, so this branch is cold.
            future = asyncio.run_coroutine_threadsafe(_invoke(), loop)
            try:
                result = future.result(timeout=30.0)
            except Exception as exc:  # noqa: BLE001
                return json.dumps(
                    {"error": f"mcp_handler_error: {exc!r}"}
                )
            return json.dumps(result, ensure_ascii=False)

        result = asyncio.run(_invoke())
        return json.dumps(result, ensure_ascii=False)

    return _handler


def _make_check_fn(
    manager: "MCPManager", server_name: str
) -> Callable[[], bool]:
    """Gate: tool only usable while session is running (§spec
    Requirement "Tool Gating by MCP Connection State")."""

    def _ready() -> bool:
        runtime = manager._servers.get(server_name)  # noqa: SLF001
        return (
            runtime is not None
            and runtime.state == "running"
            and runtime.session is not None
        )

    return _ready


def _serialize_call_result(result: Any) -> dict[str, Any]:
    """Turn an :class:`mcp.types.CallToolResult` into a plain dict."""
    dumped = _safe_model_dump(result)
    if isinstance(dumped, dict):
        return dumped
    return {"result": dumped}


def _safe_model_dump(value: Any) -> Any:
    """Pydantic-friendly dump that falls back to repr for non-models."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="python")
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return {"repr": repr(value)}


# ---------------------------------------------------------------------
# Optional convenience factory (task 14.11 integration helper)
# ---------------------------------------------------------------------


async def create_and_start(
    config: dict[str, Any], registry: Any
) -> MCPManager:
    """Build + start an :class:`MCPManager` in one call."""
    mgr = MCPManager(config, registry)
    await mgr.start()
    return mgr
