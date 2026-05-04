"""P4-S20 Stage D — PluginManager.

Plugin manifest format (``plugin.json``):
    {
      "name": "notion",                    // required, unique
      "version": "1.0.0",                  // required, semver
      "description": "...",                // required
      "skills_dir": "skills",              // default
      "mcp_servers_file": "mcp.json",      // default
      "tools_dir": "tools",                // optional, future use
      "requires": ["other-plugin", ...]    // optional
    }

Lifecycle:
    discover()   — scan disk, parse manifests, populate registry
    enable(name) — mark plugin live; collect_* methods include it
    disable(name)— mark plugin dormant; collect_* methods skip it
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[\w.]+)?(?:\+[\w.]+)?$"
)


class PluginManifestError(ValueError):
    """Raised when plugin.json is invalid; manager logs + skips."""


@dataclass
class PluginManifest:
    name: str
    version: str
    description: str
    plugin_dir: Path
    skills_dir: str = "skills"
    mcp_servers_file: str = "mcp.json"
    tools_dir: str = ""
    requires: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def skills_path(self) -> Path:
        return self.plugin_dir / self.skills_dir

    @property
    def mcp_path(self) -> Path:
        return self.plugin_dir / self.mcp_servers_file


def _parse_manifest(plugin_dir: Path) -> PluginManifest:
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        raise PluginManifestError("plugin.json missing")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginManifestError(f"plugin.json invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise PluginManifestError(
            f"plugin.json must be an object, got {type(raw).__name__}"
        )

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise PluginManifestError("plugin.json missing 'name'")
    version = raw.get("version")
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise PluginManifestError(f"plugin.json 'version' not semver: {version!r}")
    description = raw.get("description")
    if not isinstance(description, str):
        raise PluginManifestError("plugin.json missing 'description'")

    return PluginManifest(
        name=name,
        version=version,
        description=description,
        plugin_dir=plugin_dir,
        skills_dir=str(raw.get("skills_dir") or "skills"),
        mcp_servers_file=str(raw.get("mcp_servers_file") or "mcp.json"),
        tools_dir=str(raw.get("tools_dir") or ""),
        requires=list(raw.get("requires") or []),
        raw=dict(raw),
    )


class PluginManager:
    """Process-wide plugin registry."""

    def __init__(
        self,
        *,
        plugins_dir: Path,
        enabled: Optional[list[str]] = None,
    ) -> None:
        self.plugins_dir = Path(plugins_dir)
        # ``enabled=None`` → all discovered plugins enabled by default.
        # ``enabled=[]`` → none enabled (explicit opt-in mode).
        # ``enabled=["a","b"]`` → only those by name.
        self._enabled: Optional[set[str]] = (
            None if enabled is None else set(enabled)
        )
        self._plugins: dict[str, PluginManifest] = {}
        # Inverted mapping: skill_path → "plugin:<name>"
        self._skill_owner: dict[str, str] = {}

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------
    def discover(self) -> None:
        self._plugins.clear()
        self._skill_owner.clear()
        if not self.plugins_dir.exists():
            return
        for child in self.plugins_dir.iterdir():
            if not child.is_dir():
                continue
            if not (child / "plugin.json").exists():
                logger.info("plugin.skipped_no_manifest", dir=str(child))
                continue
            try:
                manifest = _parse_manifest(child)
            except PluginManifestError as exc:
                logger.warning(
                    "plugin.invalid_manifest",
                    dir=str(child),
                    error=str(exc),
                )
                continue
            if manifest.name in self._plugins:
                logger.warning(
                    "plugin.duplicate_name",
                    existing=str(self._plugins[manifest.name].plugin_dir),
                    duplicate=str(child),
                )
                continue
            self._plugins[manifest.name] = manifest
            logger.info(
                "plugin.discovered",
                name=manifest.name,
                version=manifest.version,
                dir=str(child),
            )

    # -----------------------------------------------------------------
    # Enable / disable
    # -----------------------------------------------------------------
    def is_enabled(self, name: str) -> bool:
        if name not in self._plugins:
            return False
        if self._enabled is None:
            return True  # default-enabled
        return name in self._enabled

    def enabled_names(self) -> list[str]:
        if self._enabled is None:
            return sorted(self._plugins.keys())
        return sorted(n for n in self._enabled if n in self._plugins)

    def enable(self, name: str) -> bool:
        if name not in self._plugins:
            return False
        if self._enabled is None:
            self._enabled = set(self._plugins.keys())
        self._enabled.add(name)
        return True

    def disable(self, name: str) -> bool:
        if self._enabled is None:
            self._enabled = set(self._plugins.keys())
        self._enabled.discard(name)
        return True

    # -----------------------------------------------------------------
    # Inventory
    # -----------------------------------------------------------------
    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "enabled": self.is_enabled(p.name),
                "dir": str(p.plugin_dir),
                "requires": list(p.requires),
            }
            for p in sorted(self._plugins.values(), key=lambda m: m.name)
        ]

    def collect_skill_paths(self) -> list[Path]:
        """All SKILL.md files belonging to enabled plugins."""
        out: list[Path] = []
        for name in self.enabled_names():
            p = self._plugins[name]
            if not p.skills_path.exists():
                continue
            for sd in p.skills_path.iterdir():
                if not sd.is_dir():
                    continue
                sm = sd / "SKILL.md"
                if sm.exists():
                    out.append(sm)
                    self._skill_owner[str(sm)] = f"plugin:{name}"
        return out

    def skill_source_for(self, skill_md: Path) -> str:
        return self._skill_owner.get(str(skill_md), "unknown")

    def collect_mcp_servers(self) -> list[dict[str, Any]]:
        """Merge mcp.json server entries across enabled plugins.

        Each server gets a ``source: "plugin:<name>"`` annotation so the
        MCPManager can audit provenance and uninstall cleanly when a
        plugin is disabled.
        """
        out: list[dict[str, Any]] = []
        for name in self.enabled_names():
            p = self._plugins[name]
            if not p.mcp_path.exists():
                continue
            try:
                raw = json.loads(p.mcp_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "plugin.mcp_invalid",
                    plugin=name,
                    error=str(exc),
                )
                continue
            servers = raw.get("servers") or []
            for s in servers:
                if not isinstance(s, dict):
                    continue
                merged = dict(s)
                merged["source"] = f"plugin:{name}"
                out.append(merged)
        return out
