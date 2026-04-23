"""Tests for backend.paths — model + user-data directory resolution.

Covers both the original P3-S1 model_root/resolve_model_dir API and the
P3-S6 + P3-S7 additions (user_data_dir, user_cache_dir, user_models_dir,
user_log_dir, ensure_user_dirs).

Priority order the implementation must satisfy for :func:`model_root`:
  1. DESKPET_MODEL_ROOT env var
  2. user_models_dir() if it exists on disk (LocalAppData on Windows)
  3. sys._MEIPASS if set (PyInstaller runtime marker)
  4. backend/models/ relative to paths.py (dev mode)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import paths


# ---- Fixtures --------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    """Strip every DESKPET_* env we care about and remove _MEIPASS so tests
    start from a known-clean state. Also point user_models_dir at a path
    that does NOT exist, so the is_dir() check in model_root falls through
    to _MEIPASS / dev fallback unless the test explicitly wants otherwise.
    """
    for var in (
        "DESKPET_MODEL_ROOT",
        "DESKPET_USER_DATA_DIR",
        "DESKPET_USER_CACHE_DIR",
        "DESKPET_USER_LOG_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    # Neutralise user_models_dir so whatever really exists on the dev
    # machine's LocalAppData doesn't leak into the test.
    monkeypatch.setattr(paths, "user_models_dir", lambda: Path("/__definitely_not_a_dir__"))
    return monkeypatch


# ---- model_root ------------------------------------------------------

def test_model_root_dev_mode(clean_env):
    """Dev mode: no env, no _MEIPASS, user_models_dir empty → backend/models/."""
    root = paths.model_root()
    assert root.name == "models"
    assert root.parent.name == "backend"


def test_model_root_env_override(clean_env, tmp_path):
    """DESKPET_MODEL_ROOT env var overrides everything."""
    clean_env.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    assert paths.model_root() == Path(str(tmp_path))


def test_model_root_env_override_beats_meipass(clean_env, tmp_path):
    """Env var wins even if _MEIPASS is set."""
    clean_env.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    clean_env.setattr(sys, "_MEIPASS", "/some/other/meipass", raising=False)
    assert paths.model_root() == Path(str(tmp_path))


def test_model_root_user_dir_beats_meipass(clean_env, tmp_path):
    """If user_models_dir exists on disk it wins over _MEIPASS.

    This is the P3-S6 production path — installer drops weights in
    LocalAppData, backend finds them without needing DESKPET_MODEL_ROOT.
    """
    user_models = tmp_path / "user_models"
    user_models.mkdir()
    clean_env.setattr(paths, "user_models_dir", lambda: user_models)
    clean_env.setattr(sys, "_MEIPASS", "/ignored/meipass", raising=False)
    assert paths.model_root() == user_models


def test_model_root_meipass(clean_env, tmp_path):
    """PyInstaller: _MEIPASS set → <_MEIPASS>/models (when user_models_dir absent)."""
    clean_env.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.model_root() == tmp_path / "models"


def test_resolve_model_dir_joins_subdir(clean_env, tmp_path):
    """resolve_model_dir('foo') → <root>/foo (resolved)."""
    clean_env.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    assert paths.resolve_model_dir("foo") == (tmp_path / "foo").resolve()


def test_resolve_model_dir_does_not_verify_existence(clean_env, tmp_path):
    """Must not raise if the subdir does not exist — callers decide."""
    clean_env.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    p = paths.resolve_model_dir("does-not-exist")
    assert p.name == "does-not-exist"


# ---- user_data_dir / user_cache_dir / user_log_dir -------------------

def test_user_data_dir_env_override(clean_env, tmp_path):
    clean_env.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    assert paths.user_data_dir() == tmp_path


def test_user_data_dir_default_is_absolute(clean_env):
    """Without an override, platformdirs returns an absolute OS-standard path."""
    p = paths.user_data_dir()
    assert p.is_absolute()
    assert p.name == "deskpet"


def test_user_cache_dir_env_override(clean_env, tmp_path):
    clean_env.setenv("DESKPET_USER_CACHE_DIR", str(tmp_path))
    assert paths.user_cache_dir() == tmp_path


def test_user_log_dir_follows_user_data_dir(clean_env, tmp_path):
    """By default logs live under user_data_dir / 'logs', not platformdirs' own
    Logs subdir, so support bundles capture them with the rest of user data.
    """
    clean_env.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    assert paths.user_log_dir() == tmp_path / "logs"


def test_user_log_dir_env_override(clean_env, tmp_path):
    clean_env.setenv("DESKPET_USER_LOG_DIR", str(tmp_path))
    assert paths.user_log_dir() == tmp_path


# ---- ensure_user_dirs ------------------------------------------------

def test_ensure_user_dirs_creates_expected_tree(clean_env, tmp_path):
    data = tmp_path / "data_root"
    cache = tmp_path / "cache_root"
    models = tmp_path / "models_root"
    clean_env.setenv("DESKPET_USER_DATA_DIR", str(data))
    clean_env.setenv("DESKPET_USER_CACHE_DIR", str(cache))
    clean_env.setenv("DESKPET_MODEL_ROOT", str(models))
    # Reset the monkeypatch on user_models_dir from clean_env — we want the
    # real function now so ensure_user_dirs creates the env-pointed path.
    clean_env.setattr(paths, "user_models_dir", paths.user_models_dir.__wrapped__ if hasattr(paths.user_models_dir, "__wrapped__") else lambda: Path(str(models)))

    paths.ensure_user_dirs()
    assert data.is_dir()
    assert (data / "data").is_dir()
    assert (data / "logs").is_dir()
    assert cache.is_dir()
    assert models.is_dir()


def test_ensure_user_dirs_idempotent(clean_env, tmp_path):
    clean_env.setenv("DESKPET_USER_DATA_DIR", str(tmp_path / "d"))
    clean_env.setenv("DESKPET_USER_CACHE_DIR", str(tmp_path / "c"))
    clean_env.setenv("DESKPET_MODEL_ROOT", str(tmp_path / "m"))
    clean_env.setattr(paths, "user_models_dir", lambda: tmp_path / "m")
    paths.ensure_user_dirs()
    paths.ensure_user_dirs()  # must not raise
    assert (tmp_path / "d" / "data").is_dir()
