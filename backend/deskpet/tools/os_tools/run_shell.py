"""run_shell tool — `run_shell(command, cwd=None, timeout=30)`.

Permission category: ``shell``. Deny patterns are enforced by
PermissionGate before this runs (config-deny precedence).
Captures stdout, stderr, exit_code; kills on timeout.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any


def run_shell(args: dict[str, Any], task_id: str = "") -> str:
    command = args.get("command", "")
    cwd = args.get("cwd")
    timeout = int(args.get("timeout", 30) or 30)

    if not isinstance(command, str) or not command:
        return json.dumps({"error": "command required"})

    try:
        proc = subprocess.run(  # noqa: S602 — shell=True is the point of this tool
            command,
            shell=True,
            cwd=cwd if isinstance(cwd, str) and cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return json.dumps(
            {
                "error": "timeout",
                "stdout_partial": partial[:2000],
                "timeout_s": timeout,
            },
            ensure_ascii=False,
        )
    except OSError as exc:
        return json.dumps({"error": f"OSError: {exc}"})

    return json.dumps(
        {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        },
        ensure_ascii=False,
    )
