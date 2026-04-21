"""P3-S1: tests for backend.paths — model directory resolution.

Priority order the implementation must satisfy:
  1. DESKPET_MODEL_ROOT env var
  2. sys._MEIPASS if set (PyInstaller runtime marker)
  3. backend/models/ relative to this file (dev mode)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import paths


def test_model_root_dev_mode(monkeypatch):
    """Dev mode: no env, no _MEIPASS → backend/models/ beside paths.py."""
    monkeypatch.delenv("DESKPET_MODEL_ROOT", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    root = paths.model_root()
    assert root.name == "models"
    assert root.parent.name == "backend"


def test_model_root_env_override(monkeypatch, tmp_path):
    """DESKPET_MODEL_ROOT env var overrides everything."""
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    assert paths.model_root() == Path(str(tmp_path))


def test_model_root_env_override_beats_meipass(monkeypatch, tmp_path):
    """Env var wins even if _MEIPASS is set."""
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "_MEIPASS", "/some/other/meipass", raising=False)
    assert paths.model_root() == Path(str(tmp_path))


def test_model_root_meipass(monkeypatch, tmp_path):
    """PyInstaller: _MEIPASS set → <_MEIPASS>/models."""
    monkeypatch.delenv("DESKPET_MODEL_ROOT", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.model_root() == tmp_path / "models"


def test_resolve_model_dir_joins_subdir(monkeypatch, tmp_path):
    """resolve_model_dir('foo') → <root>/foo (resolved)."""
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    assert paths.resolve_model_dir("foo") == (tmp_path / "foo").resolve()


def test_resolve_model_dir_does_not_verify_existence(monkeypatch, tmp_path):
    """Must not raise if the subdir does not exist — callers decide."""
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    # Should not raise
    p = paths.resolve_model_dir("does-not-exist")
    assert p.name == "does-not-exist"
