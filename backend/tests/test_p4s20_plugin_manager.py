"""P4-S20 Wave 5: PluginManager TDD tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.plugins.manager import PluginManager, PluginManifestError


def _write_plugin(
    plugins_dir: Path,
    name: str,
    *,
    manifest: dict,
    skills: dict[str, str] | None = None,
    mcp: dict | None = None,
) -> Path:
    pdir = plugins_dir / name
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    if skills:
        sdir = pdir / "skills"
        sdir.mkdir()
        for skill_name, body in skills.items():
            sd = sdir / skill_name
            sd.mkdir()
            (sd / "SKILL.md").write_text(body, encoding="utf-8")
    if mcp is not None:
        (pdir / "mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    return pdir


# ---------------------------------------------------------------------
# Manifest format
# ---------------------------------------------------------------------


def test_valid_plugin_loaded(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "notion-plugin",
        manifest={
            "name": "notion",
            "version": "1.0.0",
            "description": "Notion integration",
            "skills_dir": "skills",
            "mcp_servers_file": "mcp.json",
        },
        skills={
            "create-page": "---\ndescription: Create a Notion page\n---\nbody",
        },
    )
    mgr = PluginManager(plugins_dir=plugins_dir)
    mgr.discover()
    plugins = mgr.list_plugins()
    assert len(plugins) == 1
    p = plugins[0]
    assert p["name"] == "notion"
    assert p["version"] == "1.0.0"


def test_missing_manifest_skipped(tmp_path: Path, caplog) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    # No plugin.json
    junk = plugins_dir / "junk"
    junk.mkdir()
    (junk / "README.md").write_text("not a plugin", encoding="utf-8")
    mgr = PluginManager(plugins_dir=plugins_dir)
    mgr.discover()
    assert mgr.list_plugins() == []


def test_invalid_version_rejected(tmp_path: Path, caplog) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "bad",
        manifest={"name": "bad", "version": "not-semver", "description": "."},
    )
    mgr = PluginManager(plugins_dir=plugins_dir)
    mgr.discover()
    assert mgr.list_plugins() == []


def test_missing_required_fields_rejected(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "bad",
        manifest={"version": "1.0.0"},  # no name, no description
    )
    mgr = PluginManager(plugins_dir=plugins_dir)
    mgr.discover()
    assert mgr.list_plugins() == []


# ---------------------------------------------------------------------
# Skills loaded from plugin
# ---------------------------------------------------------------------


def test_plugin_skills_discoverable(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "notion",
        manifest={
            "name": "notion",
            "version": "0.1.0",
            "description": "Notion",
        },
        skills={
            "create-page": "---\ndescription: Create page\n---\nbody",
        },
    )
    mgr = PluginManager(plugins_dir=plugins_dir)
    mgr.discover()
    skill_paths = mgr.collect_skill_paths()
    assert len(skill_paths) == 1
    sp = skill_paths[0]
    assert sp.name == "SKILL.md"
    assert "create-page" in str(sp)


def test_plugin_namespacing_by_name(tmp_path: Path) -> None:
    """Two plugins with same skill name remain distinguishable."""
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "notion-plugin",
        manifest={"name": "notion", "version": "0.1.0", "description": "."},
        skills={"summarize": "---\ndescription: summarize notion\n---\nbody"},
    )
    _write_plugin(
        plugins_dir,
        "slack-plugin",
        manifest={"name": "slack", "version": "0.1.0", "description": "."},
        skills={"summarize": "---\ndescription: summarize slack\n---\nbody"},
    )
    mgr = PluginManager(plugins_dir=plugins_dir)
    mgr.discover()
    paths = mgr.collect_skill_paths()
    assert len(paths) == 2
    sources = sorted(mgr.skill_source_for(p) for p in paths)
    assert sources == ["plugin:notion", "plugin:slack"]


# ---------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------


def test_enable_disable(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "notion",
        manifest={"name": "notion", "version": "1.0.0", "description": "."},
        skills={"a": "---\ndescription: a\n---\nbody"},
    )
    mgr = PluginManager(plugins_dir=plugins_dir, enabled=["notion"])
    mgr.discover()
    assert mgr.is_enabled("notion") is True
    assert len(mgr.collect_skill_paths()) == 1

    mgr.disable("notion")
    assert mgr.is_enabled("notion") is False
    assert mgr.collect_skill_paths() == []

    mgr.enable("notion")
    assert mgr.is_enabled("notion") is True
    assert len(mgr.collect_skill_paths()) == 1


def test_default_disabled(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "p1",
        manifest={"name": "p1", "version": "1.0.0", "description": "."},
        skills={"a": "---\ndescription: a\n---\nbody"},
    )
    # Empty enabled list means all plugins start disabled
    mgr = PluginManager(plugins_dir=plugins_dir, enabled=[])
    mgr.discover()
    assert mgr.is_enabled("p1") is False
    assert mgr.collect_skill_paths() == []


# ---------------------------------------------------------------------
# MCP server registration
# ---------------------------------------------------------------------


def test_plugin_mcp_servers(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "slack",
        manifest={
            "name": "slack",
            "version": "1.0.0",
            "description": ".",
            "mcp_servers_file": "mcp.json",
        },
        mcp={
            "servers": [
                {
                    "name": "slack-mcp",
                    "command": "node",
                    "args": ["./slack-server.js"],
                }
            ]
        },
    )
    mgr = PluginManager(plugins_dir=plugins_dir, enabled=["slack"])
    mgr.discover()
    servers = mgr.collect_mcp_servers()
    assert len(servers) == 1
    s = servers[0]
    assert s["name"] == "slack-mcp"
    assert s["command"] == "node"
    assert s.get("source") == "plugin:slack"


def test_plugin_disabled_no_mcp(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(
        plugins_dir,
        "slack",
        manifest={"name": "slack", "version": "1.0.0", "description": "."},
        mcp={"servers": [{"name": "x", "command": "node", "args": []}]},
    )
    mgr = PluginManager(plugins_dir=plugins_dir, enabled=[])
    mgr.discover()
    assert mgr.collect_mcp_servers() == []
