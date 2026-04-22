"""P3-S7: config.toml resolution + memory/billing db_path behaviour.

Covers:
  * resolve_config_path priority (DESKPET_CONFIG > user_data > bundle)
  * seed_user_config_if_missing copies bundle default on first run,
    leaves existing user config alone on subsequent runs
  * load_config maps empty / relative db_path into user_data_dir
  * load_config keeps absolute db_path verbatim
  * billing.db_path follows memory.db_path's directory
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import config as config_module
import paths


@pytest.fixture
def isolated_dirs(monkeypatch, tmp_path):
    """Point every user-dir env var at a clean tmp path so nothing leaks
    from the dev machine's real AppData.
    """
    user_data = tmp_path / "user_data"
    user_cache = tmp_path / "user_cache"
    user_models = tmp_path / "user_models"
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data))
    monkeypatch.setenv("DESKPET_USER_CACHE_DIR", str(user_cache))
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(user_models))
    monkeypatch.delenv("DESKPET_CONFIG", raising=False)
    monkeypatch.delenv("DESKPET_USER_LOG_DIR", raising=False)
    return tmp_path, user_data


# ---- resolve_config_path --------------------------------------------

def test_resolve_config_path_env_override(isolated_dirs, monkeypatch, tmp_path):
    override = tmp_path / "custom-config.toml"
    override.write_text("# empty\n", encoding="utf-8")
    monkeypatch.setenv("DESKPET_CONFIG", str(override))
    assert config_module.resolve_config_path() == override


def test_resolve_config_path_env_missing_falls_through(isolated_dirs, monkeypatch, tmp_path):
    """A DESKPET_CONFIG pointing at a non-existent file is ignored with a
    warning — we don't want a stale env var to wedge the backend.
    """
    monkeypatch.setenv("DESKPET_CONFIG", str(tmp_path / "does-not-exist.toml"))
    # Block bundle-default lookup by monkey-patching its return to None.
    monkeypatch.setattr(config_module, "_bundle_default_config_path", lambda: None)
    result = config_module.resolve_config_path()
    # Falls through to <user_data>/config.toml (may or may not exist — that's fine).
    _tmp, user_data = isolated_dirs
    assert result == user_data / "config.toml"


def test_resolve_config_path_prefers_user_data(isolated_dirs, monkeypatch):
    _tmp, user_data = isolated_dirs
    user_data.mkdir(parents=True, exist_ok=True)
    user_cfg = user_data / "config.toml"
    user_cfg.write_text("[memory]\ndb_path = \"\"\n", encoding="utf-8")
    # Provide a bundle default too; the user copy must still win.
    monkeypatch.setattr(config_module, "_bundle_default_config_path", lambda: Path("/some/bundle/config.toml"))
    assert config_module.resolve_config_path() == user_cfg


# ---- seed_user_config_if_missing ------------------------------------

def test_seed_copies_bundle_when_user_missing(isolated_dirs, monkeypatch, tmp_path):
    _tmp, user_data = isolated_dirs
    bundle = tmp_path / "bundle_config.toml"
    bundle.write_text("[backend]\nport = 9999\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "_bundle_default_config_path", lambda: bundle)

    seeded = config_module.seed_user_config_if_missing()
    assert seeded == user_data / "config.toml"
    assert seeded.read_text(encoding="utf-8") == bundle.read_text(encoding="utf-8")


def test_seed_is_idempotent(isolated_dirs, monkeypatch, tmp_path):
    """If user config already exists, seed must not overwrite it."""
    _tmp, user_data = isolated_dirs
    user_data.mkdir(parents=True, exist_ok=True)
    user_cfg = user_data / "config.toml"
    user_cfg.write_text("USER-CUSTOMISED\n", encoding="utf-8")
    bundle = tmp_path / "bundle_config.toml"
    bundle.write_text("BUNDLE-DEFAULT\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "_bundle_default_config_path", lambda: bundle)

    result = config_module.seed_user_config_if_missing()
    assert result == user_cfg
    assert user_cfg.read_text(encoding="utf-8") == "USER-CUSTOMISED\n"


def test_seed_returns_none_when_bundle_missing(isolated_dirs, monkeypatch):
    monkeypatch.setattr(config_module, "_bundle_default_config_path", lambda: None)
    assert config_module.seed_user_config_if_missing() is None


# ---- load_config db_path resolution ---------------------------------

def test_load_config_empty_db_path_maps_to_user_data(isolated_dirs, tmp_path):
    _tmp, user_data = isolated_dirs
    cfg = tmp_path / "cfg.toml"
    cfg.write_text('[memory]\ndb_path = ""\n', encoding="utf-8")
    loaded = config_module.load_config(cfg)
    assert Path(loaded.memory.db_path) == user_data / "data" / "memory.db"
    # Billing should pin to the same directory.
    assert loaded.billing.db_path == user_data / "data" / "billing.db"


def test_load_config_missing_memory_section_still_resolves(isolated_dirs, tmp_path):
    _tmp, user_data = isolated_dirs
    cfg = tmp_path / "cfg.toml"
    cfg.write_text("[backend]\nport = 8100\n", encoding="utf-8")
    loaded = config_module.load_config(cfg)
    assert Path(loaded.memory.db_path) == user_data / "data" / "memory.db"


def test_load_config_absent_file_resolves_defaults(isolated_dirs, tmp_path):
    """Missing config.toml → AppConfig() defaults but db_path still resolves
    to user_data_dir (not the old CWD-relative ./data/)."""
    _tmp, user_data = isolated_dirs
    loaded = config_module.load_config(tmp_path / "does-not-exist.toml")
    assert Path(loaded.memory.db_path) == user_data / "data" / "memory.db"


def test_load_config_absolute_db_path_preserved(isolated_dirs, tmp_path):
    abs_db = tmp_path / "elsewhere" / "mydb.db"
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(f'[memory]\ndb_path = "{abs_db.as_posix()}"\n', encoding="utf-8")
    loaded = config_module.load_config(cfg)
    assert Path(loaded.memory.db_path) == abs_db
    # billing.db sits next to memory.db regardless.
    assert loaded.billing.db_path == abs_db.parent / "billing.db"


def test_load_config_legacy_relative_path_reroutes_to_user_data(isolated_dirs, tmp_path):
    """Old configs still ship './data/memory.db' — we want the value
    honoured but anchored under user_data_dir, not CWD."""
    _tmp, user_data = isolated_dirs
    cfg = tmp_path / "cfg.toml"
    cfg.write_text('[memory]\ndb_path = "./data/memory.db"\n', encoding="utf-8")
    loaded = config_module.load_config(cfg)
    assert Path(loaded.memory.db_path) == user_data / "data" / "memory.db"
