"""run_shell tool — `run_shell(command, cwd=None, timeout=30)`.

P5-S2 (2026-05-10): instead of unconditionally `shell=True` (which on
Windows means cmd.exe — terrible for LLM-generated commands because
it lacks ls/grep/sed/awk/find/cat/curl, defaults to GBK encoding, and
needs `\\` path separators), we pick the best shell available at
each invocation:

    Tier 1: Git Bash (user installed Git for Windows)
        → 100% LLM-Linux-style compat. ls/grep/sed/awk/find/curl
          all in /usr/bin. UTF-8 default. Most developers have it.

    Tier 2: bundled busybox-w32 (~700KB shipped in our MSI)
        → 95% compat. POSIX sh + 200+ unix applets. Works on machines
          that don't have Git installed. Single-file, zero-install.

    Tier 3: PowerShell 5.1 (built into every Windows box)
        → 70% compat. Has ls/cat/cp aliases, real pipes. No `&&` /
          `||` operators (5.1 limitation), but better than cmd.

    Tier 4: cmd.exe (last resort)
        → 30% compat. The historic default. Kept so we never crash on
          weird stripped-down Windows installs.

Detection runs on every call (cost: ~1ms — `shutil.which()` + a few
`Path.exists()`). This means: user installs Git mid-session → next
command auto-picks Git Bash; user uninstalls Git → auto-falls-back to
busybox. Zero-restart, self-healing.

Permission category: ``shell``. Deny patterns are enforced by
PermissionGate before this runs (config-deny precedence).
Captures stdout, stderr, exit_code; kills on timeout.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Last picked shell — only used to log changes (avoid spamming the log
# every call when the same shell stays elected). Module-global is fine
# because run_shell is called serially from the agent loop.
_last_picked: tuple[str, tuple[str, ...]] | None = None


def _bundled_busybox_path() -> Path | None:
    """Locate the busybox.exe we ship in the MSI (or its dev fallback).

    Returns None on non-Windows or when the binary is genuinely absent.
    Mirrors the pattern from ``config._bundle_default_config_path``:
    PyInstaller _MEIPASS first, then exe-dir variants, then dev
    repo-relative path.
    """
    if os.name != "nt":
        return None
    candidates: list[Path] = []
    # Frozen bundle (PyInstaller): the spec ships it via datas → _MEIPASS.
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "busybox.exe")
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "busybox.exe")
        candidates.append(exe_dir / "resources" / "busybox-w32" / "busybox.exe")
    # Dev: repo/resources/busybox-w32/busybox.exe (this file lives at
    # backend/deskpet/tools/os_tools/run_shell.py → repo root is 4 up).
    here = Path(__file__).resolve()
    repo_root = here.parents[4]  # …/deskpet/
    candidates.append(repo_root / "resources" / "busybox-w32" / "busybox.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _git_bash_path() -> Path | None:
    """Locate Git for Windows' bash.exe (NOT WSL bash).

    We DON'T use ``shutil.which("bash")`` because on Windows that often
    finds ``C:\\Windows\\System32\\bash.exe`` first — which is the WSL
    launcher, not a real bash. If the user has no WSL distro installed
    that .exe errors with "no installed distributions" instead of
    running anything.

    The reliable way: locate ``git.exe`` (which is unambiguously the
    Git for Windows install), then walk to ``../usr/bin/bash.exe``
    (Git installs bash there in mingw64 layout).
    """
    if os.name != "nt":
        return None
    git = shutil.which("git")
    if not git:
        return None
    git_path = Path(git).resolve()
    # parents[0] = the dir containing git.exe (e.g. "cmd" or "mingw64/bin")
    # Walk up looking for the Git for Windows install root that has
    # ``usr/bin/bash.exe`` underneath it. Standard install layouts:
    #   <root>/cmd/git.exe               → root = parents[1]
    #   <root>/mingw64/bin/git.exe       → root = parents[2]
    for parents_up in (1, 2):
        try:
            root = git_path.parents[parents_up]
        except IndexError:
            continue
        bash = root / "usr" / "bin" / "bash.exe"
        if bash.is_file():
            return bash
    return None


def _powershell_path() -> Path | None:
    """Locate Windows PowerShell. Prefers pwsh 7+ (rare), falls back
    to PowerShell 5.1 (always present on modern Windows)."""
    if os.name != "nt":
        return None
    for name in ("pwsh", "powershell"):
        p = shutil.which(name)
        if p:
            return Path(p)
    return None


def _pick_shell() -> tuple[str, tuple[str, ...]]:
    """Pick the best shell + leading args for executing a command string.

    Returns ``(shell_path, leading_args)`` where the final ``argv`` is
    ``[shell_path, *leading_args, command]``. Detection is cheap
    (filesystem stat + PATH lookup, ~1ms total) so we re-run on every
    call. That makes shell choice self-healing: install Git → next call
    uses Git Bash; uninstall → falls back to busybox/PowerShell.
    """
    global _last_picked
    if os.name != "nt":
        # Non-Windows: trust the system shell. Behaviour matches the
        # original `shell=True` for unix users.
        picked: tuple[str, tuple[str, ...]] = ("/bin/sh", ("-c",))
    else:
        # Tier 1: Git Bash
        bash = _git_bash_path()
        if bash is not None:
            picked = (str(bash), ("-l", "-c"))
        else:
            # Tier 2: bundled busybox
            bb = _bundled_busybox_path()
            if bb is not None:
                # busybox sh is busybox's own POSIX shell applet
                picked = (str(bb), ("sh", "-c"))
            else:
                # Tier 3: PowerShell
                psh = _powershell_path()
                if psh is not None:
                    picked = (
                        str(psh),
                        ("-NoProfile", "-NonInteractive", "-Command"),
                    )
                else:
                    # Tier 4: cmd.exe — the absolute fallback
                    picked = ("cmd.exe", ("/c",))

    if _last_picked != picked:
        logger.info(
            "run_shell_runtime_picked shell=%s args=%s",
            picked[0], list(picked[1]),
        )
        _last_picked = picked
    return picked


def run_shell(args: dict[str, Any], task_id: str = "") -> str:
    command = args.get("command", "")
    cwd = args.get("cwd")
    timeout = int(args.get("timeout", 30) or 30)

    if not isinstance(command, str) or not command:
        return json.dumps({"error": "command required"})

    shell, leading = _pick_shell()

    # Force UTF-8 so child processes that respect PYTHONIOENCODING /
    # LC_ALL emit unicode-clean output. Most LLM-emitted commands write
    # Chinese / emoji that GBK eats.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "LC_ALL": "C.UTF-8"}

    # busybox-w32 quirk (verified 2026-05-10): non-ASCII characters in
    # `-c "..."` argv are mangled because busybox-w32's main() converts
    # the Windows command line through the ANSI codepage (GBK on
    # Chinese systems) instead of using GetCommandLineW(). Result:
    # `echo 你好` prints as `��...`. Workaround: feed the
    # command via STDIN, which busybox reads as raw UTF-8 bytes —
    # bypasses the args-encoding path entirely. Git Bash, PowerShell,
    # and cmd all handle -c correctly so they keep the simple path.
    is_busybox = "busybox" in Path(shell).name.lower()
    if is_busybox:
        argv = [shell, "sh"]
        stdin_payload: str | None = command
    else:
        argv = [shell, *leading, command]
        stdin_payload = None

    try:
        proc = subprocess.run(  # noqa: S603 — shell selection happens above
            argv,
            input=stdin_payload,
            cwd=cwd if isinstance(cwd, str) and cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
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
