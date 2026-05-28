# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# P5-S2 Phase 0: sensor-style error responses include hint + examples
# so the LLM can self-correct without supervisor escalation.
_EXAMPLES = [
    {"command": "echo hello"},
    {"command": "ls -la", "cwd": "."},
    {"command": "pip install requests", "timeout": 120},
]


def _err(error: str, hint: str, **extra: Any) -> str:
    body: dict[str, Any] = {
        "ok": False,
        "error": error,
        "hint": hint,
        "examples": _EXAMPLES,
    }
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)

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
        return _err(
            "command required",
            "run_shell 的 command 字段必填，且必须是非空字符串。"
            "例如 {\"command\": \"echo hello\"}。如需指定工作目录加 cwd，"
            "如需调整超时加 timeout（秒）。",
        )

    # OpenSpec §D3 — companion session write-scope。chat handler 给陪伴
    # session 经 session_context 注入 ``_write_scope_root``；命令里的
    # mkdir/touch/cp/重定向 等写盘类操作越界 workspace → 拒绝整条命令
    # （读类命令 ls/cat/grep 不受影响 — 不是沙箱）。code session /
    # write_scope_enforced=false 不注入该键 → scope_root=None → 不拦。
    _scope_root = args.get("_write_scope_root")
    if _scope_root:
        from agent.write_scope import shell_write_scope_check as _shell_ws_check

        _violation = _shell_ws_check(command, scope_root=_scope_root)
        if _violation is not None:
            return _err(
                _violation,
                _violation,
                command_preview=command[:200],
            )

    # P6 bugfix 2026-05-14 (live-test R7): agent self-destruction guard.
    # LLM 在帮用户启动开发服务器时遇到端口占用，自作主张写了
    # "kill processes on port 5173" — 但 5173 是 deskpet 自己 vite dev
    # server 的端口！agent 把承载它的整个 deskpet stack 杀了。
    # 拒绝任何看起来要 kill / netstat-then-kill / fuser deskpet 自己
    # 关键端口的命令。这些端口默认是 dev/prod deskpet 占用的。
    _DESKPET_RESERVED_PORTS = ("5173", "8100", "4001", "4006")
    _cmd_lower = command.lower()
    _looks_destructive = any(
        kw in _cmd_lower
        for kw in ("kill", "taskkill", "stop-process", "pkill", "fuser")
    )
    if _looks_destructive:
        _hit_port = None
        for _p in _DESKPET_RESERVED_PORTS:
            # match `:5173` `port 5173` `5173 ` etc. but not `15173`
            import re as _re
            if _re.search(rf"(?<!\d){_p}(?!\d)", command):
                _hit_port = _p
                break
        if _hit_port:
            return _err(
                "self_destruction_blocked",
                f"拒绝执行：你的命令试图 kill 端口 {_hit_port} 上的进程，但该端口是 "
                "deskpet 自身的 dev/prod 服务（5173=vite, 8100=backend, "
                "4001/4006=audio/control ws）。kill 它会让 deskpet 整个崩溃。"
                "请改用其他端口（例如 5174、3001、8000）启动你的开发服务器；"
                "如果你确实需要清空被占用的端口，请告诉用户让 ta 手动处理。",
                blocked_port=_hit_port,
                command_preview=command[:200],
            )

    start = time.monotonic()
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
        elapsed = round(time.monotonic() - start, 3)
        partial = (exc.stdout or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return _err(
            "timeout",
            f"命令在 {timeout} 秒内未结束已被中止。"
            "如果是耗时操作（pip install / cargo build），请把 timeout 调大；"
            "如果是死循环，先 Ctrl+C 排查。",
            stdout_partial=partial[:2000],
            timeout_s=timeout,
            elapsed_seconds=elapsed,
        )
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            "执行命令时操作系统报错。常见原因：cwd 路径不存在、"
            "可执行文件未安装、权限不足。请确认 cwd 是有效目录、"
            "命令程序在 PATH 中。",
            cwd=cwd if isinstance(cwd, str) else None,
        )

    return json.dumps(
        {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        },
        ensure_ascii=False,
    )
