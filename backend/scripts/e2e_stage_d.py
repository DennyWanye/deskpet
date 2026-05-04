"""P4-S20 Wave 5 — Stage D plugin system E2E smoke.

Validates end-to-end plugin lifecycle:
  1. scaffold_plugin generates a plugin layout
  2. PluginManager discovers it
  3. Plugin's SKILL.md parses via the v1 parser
  4. enable/disable toggles plugin visibility cleanly

Hermetic: uses tempdir for plugins_dir.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deskpet.plugins import PluginManager
from deskpet.skills.parser import parse_skill_md


def _print(*parts: object) -> None:
    print("[e2e-stage-d]", *parts, flush=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="deskpet_e2e_stage_d_"))
    plugins_dir = tmp / "plugins"
    plugins_dir.mkdir()
    _print("plugins_dir:", plugins_dir)

    # 1. Scaffold a plugin
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.scaffold_plugin", "demo-plugin",
         "--out", str(plugins_dir),
         "--description", "demo plugin for e2e"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _print("FAIL scaffold:", proc.stderr)
        return 1
    plugin_dir = plugins_dir / "demo-plugin"
    assert (plugin_dir / "plugin.json").exists()
    assert (plugin_dir / "skills" / "example" / "SKILL.md").exists()
    _print("scaffold ok at", plugin_dir)

    # 2. Discover via PluginManager
    mgr = PluginManager(plugins_dir=plugins_dir)
    mgr.discover()
    plugins = mgr.list_plugins()
    if len(plugins) != 1 or plugins[0]["name"] != "demo-plugin":
        _print("FAIL discover:", plugins)
        return 1
    _print("discovered:", plugins[0])

    # 3. Plugin's SKILL.md parses cleanly
    skills = mgr.collect_skill_paths()
    if not skills:
        _print("FAIL no skills collected")
        return 1
    meta = parse_skill_md(skills[0])
    _print(
        "skill parsed:",
        f"name={meta.name}, description={meta.description!r}, "
        f"source={meta.source}",
    )
    assert meta.name == "example"
    assert meta.description == "Example skill shipped with this plugin"
    assert meta.source == "claude-code-v1"

    # 4. Disable hides the skill, enable restores it
    assert mgr.is_enabled("demo-plugin") is True
    mgr.disable("demo-plugin")
    assert mgr.is_enabled("demo-plugin") is False
    assert mgr.collect_skill_paths() == []
    mgr.enable("demo-plugin")
    assert mgr.is_enabled("demo-plugin") is True
    assert len(mgr.collect_skill_paths()) == 1
    _print("enable/disable cycle ok")

    shutil.rmtree(tmp, ignore_errors=True)
    _print("PASS — full Stage D lifecycle works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
