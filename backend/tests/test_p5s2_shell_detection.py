# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 (2026-05-10): tier-based shell detection for run_shell.

Verifies the priority chain Git Bash → bundled busybox → PowerShell →
cmd, plus the WSL-bash safety net (don't accidentally invoke the WSL
launcher when no distro is installed) and the self-healing property
(picks change automatically when filesystem state changes between calls).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Shell tier detection is Windows-specific; unix uses /bin/sh",
)


@pytest.fixture(autouse=True)
def _reset_shell_picker_cache():
    """Wipe the module-global last-picked cache between tests so each
    test sees a fresh log emission and doesn't get fooled by state
    from a previous test."""
    # NB: ``from deskpet.tools.os_tools import run_shell`` resolves to
    # the FUNCTION (the package's __init__.py re-exports it that way),
    # not the module. Go through importlib to grab the module object.
    import importlib  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")

    rs._last_picked = None
    yield
    rs._last_picked = None


def test_picks_git_bash_when_git_present(monkeypatch, tmp_path: Path) -> None:
    """Tier 1 wins when git.exe leads to a real bash.exe."""
    fake_root = tmp_path / "Git"
    (fake_root / "cmd").mkdir(parents=True)
    (fake_root / "usr" / "bin").mkdir(parents=True)
    fake_git = fake_root / "cmd" / "git.exe"
    fake_bash = fake_root / "usr" / "bin" / "bash.exe"
    fake_git.write_bytes(b"")
    fake_bash.write_bytes(b"")

    # NB: ``from deskpet.tools.os_tools import run_shell`` resolves to
    # the FUNCTION (the package's __init__.py re-exports it that way),
    # not the module. Go through importlib to grab the module object.
    import importlib  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")

    monkeypatch.setattr(
        rs.shutil,
        "which",
        lambda name: str(fake_git) if name == "git" else None,
    )
    shell, args = rs._pick_shell()
    assert shell == str(fake_bash)
    assert args == ("-l", "-c")


def test_picks_busybox_when_no_git_but_bundled(monkeypatch, tmp_path: Path) -> None:
    """Tier 2: fall back to bundled busybox when no Git is on PATH."""
    fake_busybox = tmp_path / "busybox.exe"
    fake_busybox.write_bytes(b"")

    # NB: ``from deskpet.tools.os_tools import run_shell`` resolves to
    # the FUNCTION (the package's __init__.py re-exports it that way),
    # not the module. Go through importlib to grab the module object.
    import importlib  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")

    monkeypatch.setattr(rs.shutil, "which", lambda name: None)
    monkeypatch.setattr(rs, "_bundled_busybox_path", lambda: fake_busybox)
    shell, args = rs._pick_shell()
    assert shell == str(fake_busybox)
    assert args == ("sh", "-c")


def test_picks_powershell_when_no_git_no_busybox(monkeypatch, tmp_path: Path) -> None:
    """Tier 3: PowerShell when neither Git nor busybox available."""
    fake_psh = tmp_path / "powershell.exe"
    fake_psh.write_bytes(b"")

    # NB: ``from deskpet.tools.os_tools import run_shell`` resolves to
    # the FUNCTION (the package's __init__.py re-exports it that way),
    # not the module. Go through importlib to grab the module object.
    import importlib  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")

    def _which(name: str) -> str | None:
        if name == "powershell":
            return str(fake_psh)
        return None  # git, pwsh both absent

    monkeypatch.setattr(rs.shutil, "which", _which)
    monkeypatch.setattr(rs, "_bundled_busybox_path", lambda: None)
    shell, args = rs._pick_shell()
    assert shell == str(fake_psh)
    assert "-NoProfile" in args


def test_falls_through_to_cmd_when_nothing_else(monkeypatch) -> None:
    """Tier 4: cmd.exe when nothing else is available."""
    # NB: ``from deskpet.tools.os_tools import run_shell`` resolves to
    # the FUNCTION (the package's __init__.py re-exports it that way),
    # not the module. Go through importlib to grab the module object.
    import importlib  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")

    monkeypatch.setattr(rs.shutil, "which", lambda name: None)
    monkeypatch.setattr(rs, "_bundled_busybox_path", lambda: None)
    shell, args = rs._pick_shell()
    assert shell == "cmd.exe"
    assert args == ("/c",)


def test_does_not_pick_wsl_bash_when_no_git(monkeypatch, tmp_path: Path) -> None:
    """Critical regression guard: ``shutil.which("bash")`` on Windows
    often resolves to ``C:\\Windows\\System32\\bash.exe`` (the WSL
    launcher). Without a WSL distro that .exe errors loudly. Our code
    MUST find bash via git-relative resolution only — never via
    ``which("bash")``.
    """
    # NB: ``from deskpet.tools.os_tools import run_shell`` resolves to
    # the FUNCTION (the package's __init__.py re-exports it that way),
    # not the module. Go through importlib to grab the module object.
    import importlib  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")

    # WSL bash exists at System32 path
    wsl_bash = tmp_path / "System32" / "bash.exe"
    wsl_bash.parent.mkdir()
    wsl_bash.write_bytes(b"")

    def _which(name: str) -> str | None:
        if name == "bash":
            return str(wsl_bash)  # WSL would win here if we used which("bash")
        return None  # No git, no powershell either

    monkeypatch.setattr(rs.shutil, "which", _which)
    monkeypatch.setattr(rs, "_bundled_busybox_path", lambda: None)
    shell, args = rs._pick_shell()
    # Should fall through to cmd.exe — NOT pick the WSL bash.
    assert shell == "cmd.exe"
    assert "bash.exe" not in shell.lower()


def test_self_healing_picks_change_when_git_appears(monkeypatch, tmp_path: Path) -> None:
    """If Git is installed mid-session, the very next call to
    _pick_shell picks Git Bash without any restart."""
    # NB: ``from deskpet.tools.os_tools import run_shell`` resolves to
    # the FUNCTION (the package's __init__.py re-exports it that way),
    # not the module. Go through importlib to grab the module object.
    import importlib  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")

    # Round 1: no git anywhere → cmd
    monkeypatch.setattr(rs.shutil, "which", lambda name: None)
    monkeypatch.setattr(rs, "_bundled_busybox_path", lambda: None)
    shell1, _ = rs._pick_shell()
    assert shell1 == "cmd.exe"

    # Round 2: user installs git, materialise the layout
    fake_root = tmp_path / "Git"
    (fake_root / "cmd").mkdir(parents=True)
    (fake_root / "usr" / "bin").mkdir(parents=True)
    fake_git = fake_root / "cmd" / "git.exe"
    fake_bash = fake_root / "usr" / "bin" / "bash.exe"
    fake_git.write_bytes(b"")
    fake_bash.write_bytes(b"")
    monkeypatch.setattr(
        rs.shutil,
        "which",
        lambda name: str(fake_git) if name == "git" else None,
    )
    shell2, _ = rs._pick_shell()
    assert shell2 == str(fake_bash)
    assert shell1 != shell2  # changed without restart


def test_real_world_runtime_picks_a_shell() -> None:
    """Smoke test on the host: detection must always return SOMETHING
    that exists (even if it's only cmd.exe). No exceptions, no None."""
    from deskpet.tools.os_tools.run_shell import _pick_shell  # noqa: PLC0415

    shell, args = _pick_shell()
    assert shell, "shell should not be empty"
    assert isinstance(args, tuple) and len(args) >= 1
    # On any modern Windows box, AT LEAST cmd.exe is present.
    assert Path(shell).name.lower() in {
        "bash.exe",
        "busybox.exe",
        "pwsh.exe",
        "powershell.exe",
        "cmd.exe",
    }


def test_busybox_utf8_via_stdin_workaround(monkeypatch) -> None:
    """Regression: busybox-w32 mangles non-ASCII in `-c "..."` args
    on Chinese-locale Windows because main() decodes via the OEM
    codepage instead of CommandLineToArgvW(). run_shell must feed
    the command via STDIN when the picked shell is busybox so UTF-8
    survives.

    This test only runs when the bundled busybox.exe is actually
    present (we ship it via scripts/download_busybox.ps1 — not
    committed to the test environment by default). Skipped otherwise.
    """
    import importlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    rs = importlib.import_module("deskpet.tools.os_tools.run_shell")
    bb = rs._bundled_busybox_path()
    if bb is None:
        pytest.skip("bundled busybox.exe not present (run scripts/download_busybox.ps1)")

    # Force the picker to choose busybox even though Git Bash is on
    # this machine — we want to exercise the busybox code path.
    monkeypatch.setattr(rs, "_git_bash_path", lambda: None)

    out = json.loads(rs.run_shell({"command": "echo 你好世界", "timeout": 5}))
    assert out.get("exit_code") == 0, out
    # � is the Unicode replacement char emitted on encoding loss;
    # a working UTF-8 path will return the original Chinese text.
    assert "�" not in (out.get("stdout") or ""), (
        "busybox -c args got mangled — stdin workaround broken? "
        f"stdout={out.get('stdout')!r}"
    )
    assert "你好世界" in (out.get("stdout") or ""), out
