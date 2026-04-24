"""MCP (Model Context Protocol) client (P4-S9, tasks 14.x).

Wraps the official ``mcp>=1.0`` SDK so DeskPet can consume third-party
MCP servers as just-another toolset.

- ``manager.py``     — ClientSession lifecycle (spawn stdio subprocess /
  connect SSE / connect streamable HTTP), ``session.initialize()``
  handshake, ``session.list_tools()`` → ToolRegistry injection with
  namespaced names ``mcp_{server}_{tool}``.
- Crash reconnect    — exponential backoff 1s -> 2s -> 4s -> 8s, max 5
  attempts. Exhausted servers are marked ``state=failed`` and dropped
  from the schemas export.
- Graceful shutdown  — SIGTERM calls ``session.close()`` on every active
  server and kills the owned subprocesses.
- MVP config         — ``@modelcontextprotocol/server-filesystem`` scoped
  to ``%APPDATA%/deskpet/workspace/`` + an open-meteo weather wrapper
  (task 14.8).
"""
