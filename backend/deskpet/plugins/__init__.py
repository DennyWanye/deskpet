"""P4-S20 Stage D — plugin system.

A plugin is a directory under ``%APPDATA%/deskpet/plugins/<name>/`` that
contains:
  - plugin.json   — required manifest (name/version/description/...)
  - skills/       — optional, contains SKILL.md sub-skills
  - mcp.json      — optional, declares custom MCP servers

PluginManager scans the plugins dir, loads valid manifests, and exposes
helpers for the SkillLoader (collect_skill_paths) and MCPManager
(collect_mcp_servers) to ingest. Per-plugin enable/disable is gated by
the in-memory ``enabled`` list which the IPC layer persists to config.
"""

from .manager import PluginManager, PluginManifest, PluginManifestError

__all__ = [
    "PluginManager",
    "PluginManifest",
    "PluginManifestError",
]
