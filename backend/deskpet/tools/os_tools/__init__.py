"""P4-S20 OS-level tools.

These are the 7 tools exposed to the LLM via the new tool_use loop.
Each handler is a sync function with signature ``(args: dict, task_id: str) -> str``
returning a JSON string per the existing ToolRegistry contract.

Permission gating happens *outside* the handler (in
``ToolRegistry.execute_tool``); the handlers themselves assume they are
allowed to run. They still validate inputs and return error envelopes
for filesystem / network / process failures.

Spec: openspec/changes/deskpet-skill-platform/specs/os-tools/spec.md
"""

from .desktop_create_file import desktop_create_file
from .edit_file import edit_file
from .list_directory import list_directory
from .read_file import read_file
from .registration import register_os_tools
from .run_shell import run_shell
from .web_fetch import web_fetch
from .write_file import write_file

__all__ = [
    "desktop_create_file",
    "edit_file",
    "list_directory",
    "read_file",
    "register_os_tools",
    "run_shell",
    "web_fetch",
    "write_file",
]
