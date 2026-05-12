"""Register the 7 OS tools into a ToolRegistry.

Called once at backend startup from main.py — kept out of __init__.py
so importing the package doesn't trigger registration as a side effect
(prevents duplicate-register warnings during tests).
"""
from __future__ import annotations

from typing import Any

from .desktop_create_file import desktop_create_file
from .edit_file import edit_file
from .list_directory import list_directory
from .read_file import read_file
from .run_shell import run_shell
from .web_fetch import web_fetch
from .write_file import write_file


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def register_os_tools(registry) -> None:  # type: ignore[no-untyped-def]
    """Register all 7 OS tools onto ``registry``.

    Idempotent — re-registration replaces (with the existing warning
    log). Safe to call from main.py at startup.
    """
    registry.register(
        name="read_file",
        toolset="os",
        schema=_schema(
            "read_file",
            "Read a text file. Returns content, line count, truncation flag. Pass offset+limit for large files.",
            {
                "path": {"type": "string", "description": "Absolute file path"},
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 2000},
            },
            ["path"],
        ),
        handler=read_file,
        permission_category="read_file",
    )

    registry.register(
        name="write_file",
        toolset="os",
        schema=_schema(
            "write_file",
            "Create a new file. Refuses to overwrite unless overwrite=true. Creates parent dirs. "
            "IMPORTANT: content is HARD-CAPPED at 4096 characters per call (G3 reliability fix). "
            "For longer files, split into multiple calls: first call with content=<part 1>, "
            "then subsequent calls with mode='append' for each chunk. Calls over 4KB are "
            "rejected before execution to avoid streaming JSON-escape failures.",
            {
                "path": {"type": "string", "description": "Absolute file path"},
                "content": {
                    "type": "string",
                    "description": "File content. MAX 4096 chars per call — split longer files into append-mode chunks.",
                    "maxLength": 4096,
                },
                "overwrite": {"type": "boolean", "default": False},
            },
            ["path", "content"],
        ),
        handler=write_file,
        permission_category="write_file",
    )

    registry.register(
        name="edit_file",
        toolset="os",
        schema=_schema(
            "edit_file",
            "Replace exact text in a file. old_string must be unique unless replace_all=true.",
            {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            ["path", "old_string", "new_string"],
        ),
        handler=edit_file,
        permission_category="write_file",
    )

    registry.register(
        name="list_directory",
        toolset="os",
        schema=_schema(
            "list_directory",
            "List files + subdirectories of a path with name/type/size.",
            {
                "path": {"type": "string"},
                "max_entries": {"type": "integer", "default": 100},
            },
            ["path"],
        ),
        handler=list_directory,
        permission_category="read_file",
    )

    registry.register(
        name="run_shell",
        toolset="os",
        schema=_schema(
            "run_shell",
            (
                "Execute a shell command. On Windows the runtime auto-picks the "
                "best shell available: Git Bash (preferred) → bundled busybox sh "
                "→ PowerShell → cmd. Use Linux-style commands (ls / grep / sed / "
                "awk / find / cat / curl all work via Git Bash or busybox); "
                "forward-slash paths are accepted. Captures stdout/stderr/"
                "exit_code. Default timeout 30s. Asks user permission first."
            ),
            {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
            },
            ["command"],
        ),
        handler=run_shell,
        permission_category="shell",
        dangerous=True,
        # P5-S1: shell commands can legitimately take a while (pip install,
        # cargo build). 5 minutes is the operative wall — the supervisor
        # watchdog still sits one level above this and will catch true
        # hangs at the 15-minute session-inactivity threshold.
        timeout_seconds=300.0,
    )

    # NOTE: ``web_fetch`` is intentionally NOT registered here — the
    # full-featured implementation lives in ``deskpet/tools/web_tools.py``
    # (with robots.txt, rate-limiting, and 429 block-cache). We patch
    # *that* spec to set the network permission_category instead of
    # overwriting it with our minimal version.
    existing = registry.get("web_fetch")
    if existing is not None and existing.permission_category != "network":
        # Re-register through the public API so the warning appears
        # once per startup (matches the existing convention).
        registry.register(
            name="web_fetch",
            toolset=existing.toolset,
            schema=existing.schema,
            handler=existing.handler,
            check_fn=existing.check_fn,
            requires_env=list(existing.requires_env),
            permission_category="network",
            source=existing.source,
            dangerous=existing.dangerous,
        )

    registry.register(
        name="desktop_create_file",
        toolset="os",
        schema=_schema(
            "desktop_create_file",
            "Create a file on the user's Desktop with given name and content. Cross-platform.",
            {
                "name": {"type": "string", "description": "File name (no path separators)"},
                "content": {"type": "string"},
            },
            ["name", "content"],
        ),
        handler=desktop_create_file,
        permission_category="desktop_write",
    )
