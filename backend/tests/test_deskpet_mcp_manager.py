"""P4-S9 task 14.11: MCPManager unit tests.

Covers every Requirement in ``openspec/changes/p4-poseidon-agent-harness
/specs/mcp-integration/spec.md`` via an in-process fake instead of real
subprocess spawn — CI must stay under 2s.

Test matrix:

* ``test_spawn_two_servers_registers_all_tools`` — namespace injection
* ``test_crash_reconnect_exponential_backoff`` — 1s → success
* ``test_max_retries_marks_server_failed``     — 5 × fail → failed
* ``test_namespace_no_conflict``                — same tool on two servers
* ``test_dead_session_dispatch_fast_fail``      — no hang on dead session
* ``test_unknown_server_returns_error``         — mcp_call sad path
* ``test_graceful_shutdown_calls_close``        — stop() → close() per server
* ``test_unknown_tool_returns_error``           — tool not in schema
* ``test_disabled_server_skipped``              — enabled=false → no spawn
* ``test_unknown_transport_skipped``            — typoed transport logged+skipped
* ``test_default_config_no_brave_search``       — audit guardrail (§14.9)
* ``test_default_config_filesystem_scoped``     — §14.8 scope check
"""
from __future__ import annotations

import asyncio
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from deskpet.mcp import manager as mcp_manager_mod
from deskpet.mcp.manager import MCPManager, _BACKOFF_SCHEDULE
from deskpet.tools.registry import ToolRegistry


# --------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------


class _FakeTool:
    """Minimal stand-in for :class:`mcp.types.Tool`."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.inputSchema: dict[str, Any] = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }


class _FakeToolsResult:
    def __init__(self, tools: list[_FakeTool]) -> None:
        self.tools = tools


class _FakeCallResult:
    """Stand-in for :class:`mcp.types.CallToolResult`."""

    def __init__(self, text: str) -> None:
        self._text = text

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {
            "isError": False,
            "content": [{"type": "text", "text": self._text}],
        }


class _FakeSession:
    """Stand-in for :class:`mcp.ClientSession`.

    Behaviour controlled by a ``script`` dict:

      * ``initialize_error``  — exception to raise in ``initialize()``
      * ``call_error``         — exception to raise in ``call_tool()``
      * ``tools``              — list[_FakeTool] returned by list_tools
      * ``call_text``          — text returned by every call_tool
    """

    instances: list["_FakeSession"] = []

    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.initialize_calls = 0
        self.list_tools_calls = 0
        self.call_tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.close_calls = 0
        _FakeSession.instances.append(self)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # ClientSession's __aexit__ normally closes streams; our fake
        # just counts the close.
        self.close_calls += 1

    async def initialize(self) -> None:
        self.initialize_calls += 1
        err = self.script.get("initialize_error")
        if err is not None:
            raise err

    async def list_tools(self, *a: Any, **kw: Any) -> _FakeToolsResult:
        self.list_tools_calls += 1
        return _FakeToolsResult(list(self.script.get("tools") or []))

    async def call_tool(
        self, name: str, args: dict[str, Any] | None = None, **_: Any
    ) -> _FakeCallResult:
        self.call_tool_calls.append((name, dict(args or {})))
        err = self.script.get("call_error")
        if err is not None:
            raise err
        return _FakeCallResult(self.script.get("call_text", "ok"))

    async def close(self) -> None:
        self.close_calls += 1

    async def list_resources(self) -> Any:
        return _FakeCallResult("res-list")

    async def read_resource(self, uri: Any) -> Any:
        return _FakeCallResult(f"res:{uri}")

    async def list_prompts(self) -> Any:
        return _FakeCallResult("prompts")

    async def get_prompt(self, name: str, args: dict[str, str]) -> Any:
        return _FakeCallResult(f"prompt:{name}")


class _FakeStreams:
    async def send(self, *a: Any, **kw: Any) -> None:  # pragma: no cover
        pass

    async def receive(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        pass


def _build_fake_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scripts_by_command: dict[str, dict[str, Any]] | None = None,
    default_script: dict[str, Any] | None = None,
    fail_connect_n_times: dict[str, int] | None = None,
) -> None:
    """Monkeypatch the SDK symbols used by MCPManager.

    ``scripts_by_command`` — dispatch on ``StdioServerParameters.command``
      to pick which script the resulting FakeSession runs.
    ``fail_connect_n_times`` — per-command counter of how many calls to
      ``stdio_client`` must raise (for reconnect tests).
    """
    failures = dict(fail_connect_n_times or {})

    @asynccontextmanager
    async def _fake_stdio_client(params: Any, errlog: Any = None) -> Any:
        cmd = getattr(params, "command", "")
        remaining = failures.get(cmd, 0)
        if remaining > 0:
            failures[cmd] = remaining - 1
            raise RuntimeError(f"fake stdio_client fail ({remaining} left)")
        # Yield a pair of dummy streams. ClientSession is also faked,
        # so it never touches them.
        yield (_FakeStreams(), _FakeStreams())

    def _fake_client_session(read: Any, write: Any) -> _FakeSession:
        # Pick script by *which* transport invocation this is. Both
        # commands get their own script if registered; else default.
        # We infer the current command via a sentinel: the most-recently
        # opened stdio call stores it below via _last_command.
        cmd = getattr(_fake_stdio_client, "_last_command", None)
        script: dict[str, Any] = (
            (scripts_by_command or {}).get(cmd) if cmd else None
        ) or default_script or {}
        return _FakeSession(script)

    # Capture the command each time for the ClientSession factory to
    # route correctly. Wrap the asynccontextmanager in another cm.
    real_fake = _fake_stdio_client

    @asynccontextmanager
    async def _stdio_tracked(params: Any, errlog: Any = None) -> Any:
        real_fake._last_command = getattr(params, "command", "")  # type: ignore[attr-defined]
        async with real_fake(params, errlog) as streams:
            yield streams

    class _FakeStdioParams:
        def __init__(
            self,
            *,
            command: str,
            args: list[str] | None = None,
            env: dict[str, str] | None = None,
        ) -> None:
            self.command = command
            self.args = list(args or [])
            self.env = env

    monkeypatch.setattr(mcp_manager_mod, "stdio_client", _stdio_tracked)
    monkeypatch.setattr(
        mcp_manager_mod, "StdioServerParameters", _FakeStdioParams
    )
    monkeypatch.setattr(mcp_manager_mod, "ClientSession", _fake_client_session)
    _FakeSession.instances.clear()


# --------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_two_servers_registers_all_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(
        monkeypatch,
        scripts_by_command={
            "cmd_fs": {"tools": [_FakeTool("read_file"), _FakeTool("write_file")]},
            "cmd_git": {"tools": [_FakeTool("status"), _FakeTool("log")]},
        },
    )
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {
                    "name": "filesystem",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "cmd_fs",
                    "args": [],
                },
                {
                    "name": "git",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "cmd_git",
                    "args": [],
                },
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        names = set(registry.list_tools())
        assert {
            "mcp_filesystem_read_file",
            "mcp_filesystem_write_file",
            "mcp_git_status",
            "mcp_git_log",
        } <= names
        assert mgr.server_state() == {
            "filesystem": "running",
            "git": "running",
        }
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_namespace_no_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _build_fake_env(
        monkeypatch,
        scripts_by_command={
            "srv1": {"tools": [_FakeTool("read_file")]},
            "srv2": {"tools": [_FakeTool("read_file")]},
        },
    )
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {"name": "srv1", "enabled": True, "transport": "stdio", "command": "srv1", "args": []},
                {"name": "srv2", "enabled": True, "transport": "stdio", "command": "srv2", "args": []},
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        names = registry.list_tools()
        assert "mcp_srv1_read_file" in names
        assert "mcp_srv2_read_file" in names
        assert names.count("mcp_srv1_read_file") == 1
        assert names.count("mcp_srv2_read_file") == 1
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_crash_reconnect_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First stdio_client call fails; second succeeds.
    _build_fake_env(
        monkeypatch,
        default_script={"tools": [_FakeTool("ping")]},
        fail_connect_n_times={"srv": 1},
    )
    # Patch asyncio.sleep within the manager module to go fast.
    sleeps: list[float] = []

    async def _fast_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(mcp_manager_mod.asyncio, "sleep", _fast_sleep)

    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {
                    "name": "srv",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "srv",
                    "args": [],
                }
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        # Initial connect fails → reconnect task spawned.
        # Wait for the reconnect task to complete.
        runtime = mgr._servers["srv"]  # noqa: SLF001
        assert runtime.reconnect_task is not None
        await runtime.reconnect_task
        assert mgr.server_state()["srv"] == "running"
        # Tools registered after reconnect
        assert "mcp_srv_ping" in registry.list_tools()
        # First backoff was 1.0s
        assert sleeps and sleeps[0] == _BACKOFF_SCHEDULE[0]
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_max_retries_marks_server_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1 initial + 5 reconnect attempts all fail ⇒ state=failed.
    _build_fake_env(
        monkeypatch,
        default_script={"tools": [_FakeTool("x")]},
        fail_connect_n_times={"srv": 100},  # effectively infinite
    )

    async def _fast_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(mcp_manager_mod.asyncio, "sleep", _fast_sleep)

    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {
                    "name": "srv",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "srv",
                    "args": [],
                }
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        runtime = mgr._servers["srv"]  # noqa: SLF001
        assert runtime.reconnect_task is not None
        await runtime.reconnect_task
        assert mgr.server_state()["srv"] == "failed"
        # Tools dropped
        assert "mcp_srv_x" not in registry.list_tools()
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_dead_session_dispatch_fast_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(
        monkeypatch,
        default_script={"tools": [_FakeTool("x")]},
    )
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {
                    "name": "srv",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "srv",
                    "args": [],
                }
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        # Simulate session died mid-flight.
        runtime = mgr._servers["srv"]  # noqa: SLF001
        runtime.state = "reconnecting"
        runtime.session = None

        # Patch sleep before the reconnect task the next _mark_disconnected
        # would spawn (though this test doesn't trigger that path).
        async def _fast_sleep(delay: float) -> None:
            return None

        monkeypatch.setattr(mcp_manager_mod.asyncio, "sleep", _fast_sleep)

        t0 = asyncio.get_event_loop().time()
        result = await mgr.mcp_call("srv", "x", {"a": 1})
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed < 0.05, f"fast-fail took {elapsed:.3f}s"
        assert result["error"] == "mcp_session_dead"
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_unknown_server_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(monkeypatch, default_script={"tools": []})
    registry = ToolRegistry()
    mgr = MCPManager({"enabled": True, "servers": []}, registry)
    await mgr.start()
    try:
        result = await mgr.mcp_call("ghost", "foo", {})
        assert result["error"] == "unknown_mcp_server"
        assert result["server"] == "ghost"
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(
        monkeypatch,
        default_script={"tools": [_FakeTool("alpha")]},
    )
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {
                    "name": "srv",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "srv",
                    "args": [],
                }
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        result = await mgr.mcp_call("srv", "beta", {})
        assert result["error"] == "unknown_mcp_tool"
        assert result["tool"] == "beta"
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown_calls_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(
        monkeypatch,
        scripts_by_command={
            "a": {"tools": [_FakeTool("one")]},
            "b": {"tools": [_FakeTool("two")]},
        },
    )
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {"name": "a", "enabled": True, "transport": "stdio", "command": "a", "args": []},
                {"name": "b", "enabled": True, "transport": "stdio", "command": "b", "args": []},
            ],
        },
        registry,
    )
    await mgr.start()
    sessions_before_stop = list(_FakeSession.instances)
    await mgr.stop()
    # Each fake session saw at least one close (__aexit__ bumps close_calls
    # or close() was called directly).
    for s in sessions_before_stop:
        assert s.close_calls >= 1, f"session {s!r} not closed"
    # Subsequent stop() is idempotent.
    await mgr.stop()


@pytest.mark.asyncio
async def test_disabled_server_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(monkeypatch, default_script={"tools": [_FakeTool("x")]})
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {"name": "weather", "enabled": False, "transport": "stdio", "command": "weather", "args": []},
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        assert mgr.server_state() == {}
        assert registry.list_tools() == []
        # No FakeSession ever instantiated.
        assert len(_FakeSession.instances) == 0
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_unknown_transport_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(monkeypatch, default_script={"tools": []})
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {"name": "wat", "enabled": True, "transport": "smokesignal", "command": "x", "args": []},
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        # Server was never spawned; state map stays empty.
        assert "wat" not in mgr.server_state()
    finally:
        await mgr.stop()


def test_default_config_no_brave_search() -> None:
    """§14.9 audit guardrail — default config must not ship
    brave-search / any paid-API MCP server."""
    root = Path(__file__).resolve().parents[2]
    cfg_path = root / "config.toml"
    assert cfg_path.exists(), f"default config missing at {cfg_path}"
    text = cfg_path.read_text(encoding="utf-8").lower()
    for banned in ("brave-search", "brave_search", "tavily", "perplexity"):
        assert banned not in text, f"default config contains banned {banned!r}"


def test_default_config_filesystem_scoped() -> None:
    """§14.8 — filesystem MCP must be scoped to workspace dir, not
    root or user profile."""
    root = Path(__file__).resolve().parents[2]
    cfg_path = root / "config.toml"
    text = cfg_path.read_text(encoding="utf-8")
    # find the filesystem server block
    m = re.search(
        r"\[\[mcp\.servers\]\]\s*name\s*=\s*[\"']filesystem[\"'].*?(?=\[\[|\Z)",
        text,
        re.DOTALL,
    )
    assert m, "no filesystem MCP server entry in default config"
    block = m.group(0)
    assert "deskpet/workspace" in block or "deskpet\\workspace" in block, (
        "filesystem args must be scoped to %APPDATA%\\deskpet\\workspace"
    )
    # MUST NOT be scoped to these wide paths
    for wide in ["%USERPROFILE%", "C:/", "C:\\", "/home/", "/Users/"]:
        assert wide not in block, f"filesystem scope too wide: {wide!r}"


@pytest.mark.asyncio
async def test_mcp_call_success_returns_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_fake_env(
        monkeypatch,
        default_script={
            "tools": [_FakeTool("echo")],
            "call_text": "hello",
        },
    )
    registry = ToolRegistry()
    mgr = MCPManager(
        {
            "enabled": True,
            "servers": [
                {
                    "name": "srv",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "srv",
                    "args": [],
                }
            ],
        },
        registry,
    )
    await mgr.start()
    try:
        result = await mgr.mcp_call("srv", "echo", {"msg": "hi"})
        assert result.get("isError") is False
        assert result["content"][0]["text"] == "hello"
    finally:
        await mgr.stop()
