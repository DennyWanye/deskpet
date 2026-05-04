"""P4-S20 Stage D — plugin scaffold generator.

Usage:
    python -m scripts.scaffold_plugin <name> [--out <dir>]

Generates a starter plugin layout:
    <out>/<name>/
      plugin.json
      README.md
      skills/example/SKILL.md

Without ``--out``, defaults to ``./<name>``. After scaffolding, drop the
plugin into ``%APPDATA%/deskpet/plugins/`` and DeskPet's PluginManager
will pick it up on next discover().
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path


SKILL_TEMPLATE = """\
---
description: Example skill shipped with this plugin
when_to_use: When the user asks the example to run
allowed-tools: [read_file]
---
# Example skill

This is the body of the skill. The LLM will receive this Markdown
verbatim (with variable substitution applied) when the skill is
invoked.

You can use:
- ${{CLAUDE_SKILL_DIR}} — absolute path to this skill's directory
- ${{CLAUDE_SESSION_ID}} — current session id
- $ARGUMENTS — all args joined by single space
- $0, $1, ... — positional args

You can also embed shell command output with !\\`<command>\\`:

    Today's date: !\\`date\\`
"""

README_TEMPLATE = """\
# {name}

A DeskPet plugin scaffolded by `scripts/scaffold_plugin.py`.

## Layout

- `plugin.json` — required manifest
- `skills/` — sub-skills following Claude Code SKILL.md format
- `mcp.json` — optional MCP server registrations

## Install

Copy this directory into `%APPDATA%/deskpet/plugins/`. DeskPet's
PluginManager will discover it on next start (or on hot-reload).

## Develop

Edit `skills/example/SKILL.md` and re-run DeskPet — the SkillLoader
hot-reload watcher will pick up your changes within ~1.5s.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a DeskPet plugin")
    parser.add_argument("name", help="plugin name (kebab-case recommended)")
    parser.add_argument(
        "--out",
        default=".",
        help="output directory (default: current dir)",
    )
    parser.add_argument(
        "--description",
        default="",
        help="optional description for plugin.json",
    )
    args = parser.parse_args(argv)

    name = args.name.strip()
    if not name:
        print("error: name must not be empty", file=sys.stderr)
        return 2

    out_root = Path(args.out).resolve()
    plugin_dir = out_root / name
    if plugin_dir.exists():
        print(f"error: {plugin_dir} already exists", file=sys.stderr)
        return 1

    description = args.description or f"{name} — DeskPet plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": description,
                "skills_dir": "skills",
                "mcp_servers_file": "mcp.json",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (plugin_dir / "README.md").write_text(
        README_TEMPLATE.format(name=name), encoding="utf-8"
    )
    skill_dir = plugin_dir / "skills" / "example"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_TEMPLATE, encoding="utf-8")

    print(f"created plugin scaffold at {plugin_dir}")
    print("Next steps:")
    print(
        f"  1. Edit {plugin_dir / 'skills' / 'example' / 'SKILL.md'} to write your skill"
    )
    print(
        f"  2. Copy {plugin_dir} into %APPDATA%/deskpet/plugins/ to install"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
