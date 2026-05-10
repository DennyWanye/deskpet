"""P4-S22+ portable mode — `<install>/userdata/.deskpet-portable` sentinel
re-routes all user paths from `%AppData%` to the install directory.

Tested via the public surface: monkeypatch `sys.frozen=True` and
`sys.executable` to mimic a frozen install layout, drop a sentinel,
then assert ``user_data_dir`` / ``user_models_dir`` / ``user_log_dir``
all resolve under the install's `userdata/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import paths


def _make_fake_install(tmp_path) -> tuple[Path, Path]:
    """Lay out a fake install tree under tmp_path. Returns (install_root, exe)."""
    install = tmp_path / "install"
    backend_dir = install / "backend"
    backend_dir.mkdir(parents=True)
    fake_exe = backend_dir / "deskpet-backend.exe"
    fake_exe.write_bytes(b"")
    return install, fake_exe


def _activate_frozen(monkeypatch, exe: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    # Strip env overrides so only the install-dir layout matters.
    monkeypatch.delenv("DESKPET_USER_DATA_DIR", raising=False)
    monkeypatch.delenv("DESKPET_USER_CACHE_DIR", raising=False)
    monkeypatch.delenv("DESKPET_USER_LOG_DIR", raising=False)
    monkeypatch.delenv("DESKPET_MODEL_ROOT", raising=False)


def test_frozen_with_writable_install_activates_portable(monkeypatch, tmp_path):
    """Frozen + writable install dir → automatically portable. Default
    layout has exe at `<install>/backend/`, so paths.py walks up 1
    level to find/create `<install>/userdata/`."""
    install, exe = _make_fake_install(tmp_path)
    _activate_frozen(monkeypatch, exe)
    assert paths.is_portable_mode() is True
    assert paths.user_data_dir() == install / "userdata"
    assert paths.user_models_dir() == install / "userdata" / "models"
    assert paths.user_cache_dir() == install / "userdata" / "cache"
    assert paths.user_log_dir() == install / "userdata" / "logs"


def test_portable_creates_userdata_dir_lazily(monkeypatch, tmp_path):
    """If `<install>/userdata/` doesn't exist, paths.py mkdir's it on
    first access — no separate bootstrap step needed."""
    install, exe = _make_fake_install(tmp_path)
    _activate_frozen(monkeypatch, exe)
    assert not (install / "userdata").exists()
    paths.user_data_dir()
    assert (install / "userdata").is_dir()


def test_standalone_exe_layout_activates_portable(monkeypatch, tmp_path):
    """Standalone exe layout: exe at `<install>/deskpet-backend.exe`
    (no `backend/` subdir). Walk-up still finds the right userdata."""
    install = tmp_path / "install"
    install.mkdir()
    fake_exe = install / "deskpet-backend.exe"
    fake_exe.write_bytes(b"")
    _activate_frozen(monkeypatch, fake_exe)
    assert paths.is_portable_mode() is True
    assert paths.user_data_dir() == install / "userdata"


def test_dev_mode_never_portable(monkeypatch):
    """Non-frozen interpreter: portable mode never activates."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("DESKPET_USER_DATA_DIR", raising=False)
    assert paths.is_portable_mode() is False


def test_env_override_beats_portable(monkeypatch, tmp_path):
    """`DESKPET_USER_DATA_DIR` env wins even when portable would
    otherwise activate (covers test runs and CI overrides)."""
    install, exe = _make_fake_install(tmp_path)
    _activate_frozen(monkeypatch, exe)
    custom = tmp_path / "custom-userdata"
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(custom))
    assert paths.user_data_dir() == custom


def test_user_models_env_override_still_works_in_portable(monkeypatch, tmp_path):
    """`DESKPET_MODEL_ROOT` continues to override user_models_dir even
    when portable mode is active — power users can still pin a separate
    drive for model weights."""
    install, exe = _make_fake_install(tmp_path)
    _activate_frozen(monkeypatch, exe)
    custom = tmp_path / "models-elsewhere"
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(custom))
    assert paths.user_models_dir() == custom
