# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S21 #12 — `seed_user_config_if_missing` auto-migrates legacy
``[llm.local]`` / ``[llm.cloud]`` configs to the unified ``[llm]``
schema. Backs up the old file as ``.legacy-bak`` and copies the
bundle's new-format config in place.

Regression guard for the "sealos 401" bug: legacy config had a
hardcoded ``[llm.cloud].base_url = https://vcrppsmofoyv.cloud.sealos.io/v1``
that the upgraded backend kept hitting even after the user reconfigured
LLM via the Settings panel (those settings live in llm_runtime.json,
not config.toml). Without this migration users had to manually delete
``$APPDATA/deskpet/config.toml`` after every upgrade.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import config as cfg


LEGACY_TOML = b"""
schema_version = 1

[backend]
host = "127.0.0.1"
port = 8100

[llm]
strategy = "cloud_first"

[llm.local]
model = "gemma4:e4b"
base_url = "http://localhost:11434/v1"

[llm.cloud]
model = "qwen3.6-plus"
base_url = "https://vcrppsmofoyv.cloud.sealos.io/v1"
"""

UNIFIED_TOML = b"""
schema_version = 1

[backend]
host = "127.0.0.1"
port = 8100

[llm]
model = "gemma4:e4b"
base_url = "http://localhost:11434/v1"
api_key = "ollama"
"""


# ---------------------------------------------------------------------------
# is_legacy_llm_schema detector
# ---------------------------------------------------------------------------


def test_detector_flags_local_subtable():
    raw = {"llm": {"local": {"model": "x"}}}
    assert cfg._is_legacy_llm_schema(raw) is True


def test_detector_flags_cloud_subtable():
    raw = {"llm": {"cloud": {"base_url": "https://x"}}}
    assert cfg._is_legacy_llm_schema(raw) is True


def test_detector_passes_unified_schema():
    raw = {"llm": {"model": "x", "base_url": "y", "api_key": "z"}}
    assert cfg._is_legacy_llm_schema(raw) is False


def test_detector_handles_missing_llm_section():
    assert cfg._is_legacy_llm_schema({}) is False
    assert cfg._is_legacy_llm_schema({"llm": "string-not-table"}) is False


# ---------------------------------------------------------------------------
# Migration: legacy file → backup + replace
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_dirs(tmp_path, monkeypatch):
    """Override user_data_dir + bundle source. Yields (user_dir, bundle_src)."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle_src = bundle_dir / "config.toml"
    bundle_src.write_bytes(UNIFIED_TOML)

    # paths.user_data_dir() is normally read from platformdirs / DESKPET env.
    # We monkeypatch the module attribute the seed function uses.
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_dir))
    # _bundle_default_config_path is its own function — replace it.
    monkeypatch.setattr(cfg, "_bundle_default_config_path", lambda: bundle_src)
    yield user_dir, bundle_src


def test_seed_first_run_copies_bundle_when_user_target_missing(fake_dirs):
    user_dir, bundle_src = fake_dirs
    user_target = user_dir / "config.toml"
    assert not user_target.exists()

    result = cfg.seed_user_config_if_missing()

    assert result == user_target
    assert user_target.read_bytes() == UNIFIED_TOML


def test_seed_skips_when_user_target_already_unified(fake_dirs):
    user_dir, _bundle_src = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_bytes(UNIFIED_TOML)
    mtime_before = user_target.stat().st_mtime_ns

    result = cfg.seed_user_config_if_missing()

    assert result == user_target
    # File untouched
    assert user_target.read_bytes() == UNIFIED_TOML
    assert user_target.stat().st_mtime_ns == mtime_before
    # No backup created
    assert not (user_dir / "config.legacy-bak").exists()


def test_seed_migrates_legacy_schema_creates_backup_and_replaces(fake_dirs):
    user_dir, _bundle_src = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_bytes(LEGACY_TOML)

    result = cfg.seed_user_config_if_missing()

    assert result == user_target
    # New content is the unified schema from bundle
    assert user_target.read_bytes() == UNIFIED_TOML
    # Backup of old content exists
    bak = user_target.with_suffix(".legacy-bak")
    assert bak.exists()
    assert bak.read_bytes() == LEGACY_TOML


def test_seed_migration_no_bundle_source_keeps_legacy_in_place(monkeypatch, tmp_path):
    """If we can't find the bundle config, leave the legacy file alone
    rather than nuke it. Better to keep a working-but-old config than
    to break the user's startup."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    user_target = user_dir / "config.toml"
    user_target.write_bytes(LEGACY_TOML)
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_dir))
    monkeypatch.setattr(cfg, "_bundle_default_config_path", lambda: None)

    result = cfg.seed_user_config_if_missing()

    assert result == user_target
    # File unchanged, no backup
    assert user_target.read_bytes() == LEGACY_TOML
    assert not user_target.with_suffix(".legacy-bak").exists()


# ---------------------------------------------------------------------------
# _bundle_default_config_path: _MEIPASS preferred over exe_dir
# ---------------------------------------------------------------------------


def test_bundle_default_config_path_prefers_meipass_in_frozen(monkeypatch, tmp_path):
    meipass = tmp_path / "_MEIPASS"
    meipass.mkdir()
    target = meipass / "config.toml"
    target.write_bytes(UNIFIED_TOML)

    # Pretend we're frozen and _MEIPASS is set
    import sys as _sys
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "_MEIPASS", str(meipass), raising=False)
    # Also point sys.executable somewhere benign so the exe_dir branch
    # would fail without _MEIPASS taking priority
    fake_exe = tmp_path / "fake.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(_sys, "executable", str(fake_exe))

    result = cfg._bundle_default_config_path()
    assert result == target
