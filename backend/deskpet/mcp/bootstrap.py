"""P4-S9 task 14.1: MCP bootstrap helper.

A thin factory that reads ``[mcp]`` from the deskpet ``config.toml`` and
returns a live :class:`~deskpet.mcp.manager.MCPManager`.

The backend entry point (``backend/main.py``) doesn't wire this in yet —
that happens in P4-S12 integration. For now the factory is covered by
unit tests and used by the OpenSpec review to prove the surface exists.

Typical use::

    from deskpet.tools.registry import registry
    from deskpet.mcp.bootstrap import create_and_start_from_config

    mcp = await create_and_start_from_config(app_config, registry)
    ...
    await mcp.stop()

TODO P4-S12: call ``create_and_start_from_config`` from ``main.py``'s
lifespan hook AFTER the tool registry is assembled, and await
``mcp.stop()`` during shutdown.
"""
from __future__ import annotations

from typing import Any

import structlog

from .manager import MCPManager

logger = structlog.get_logger(__name__)


def _extract_mcp_config(app_config: Any) -> dict[str, Any]:
    """Read the ``[mcp]`` section out of a loaded deskpet config.

    Accepts either a plain ``dict`` (as ``tomllib.loads`` returns) or
    an object with attribute-style access. Returns an empty dict when
    the section is absent — :meth:`MCPManager.start` treats that as
    "no servers configured" and logs a notice.
    """
    if app_config is None:
        return {}
    # dict-style
    if isinstance(app_config, dict):
        return dict(app_config.get("mcp") or {})
    # attribute-style
    mcp = getattr(app_config, "mcp", None)
    if mcp is None:
        return {}
    if isinstance(mcp, dict):
        return dict(mcp)
    # Pydantic models expose model_dump().
    dump = getattr(mcp, "model_dump", None)
    if callable(dump):
        return dict(dump())
    return {}


async def create_and_start_from_config(
    app_config: Any, tool_registry: Any | None
) -> MCPManager:
    """Build and ``start()`` an :class:`MCPManager` from a deskpet
    config object. Never raises — the manager internally tolerates
    individual server failures.
    """
    mcp_cfg = _extract_mcp_config(app_config)
    manager = MCPManager(mcp_cfg, tool_registry)
    await manager.start()
    logger.info("mcp_bootstrap_started", states=manager.server_state())
    return manager
