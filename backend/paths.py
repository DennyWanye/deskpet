"""Model / asset directory resolution.

P3-S1: single source of truth for where bundled model files live, so
PyInstaller ``--onedir`` packaging (P3-S4) and dev-mode execution both go
through one place.

Priority order for :func:`model_root`:

1. ``DESKPET_MODEL_ROOT`` environment variable (escape hatch for CI / debug).
2. ``sys._MEIPASS`` if set (PyInstaller runtime marker) → ``<_MEIPASS>/models``.
3. Dev-mode fallback: ``backend/models/`` relative to this file.

Callers use :func:`resolve_model_dir` with a subfolder name (e.g.
``"faster-whisper-large-v3-turbo"``) to get an absolute path. Existence is
NOT verified — the caller decides whether a missing directory is fatal.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def model_root() -> Path:
    """Return the root directory containing all bundled model subfolders."""
    override = os.environ.get("DESKPET_MODEL_ROOT")
    if override:
        return Path(override)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "models"
    # Dev mode: backend/models/ beside this file.
    return Path(__file__).resolve().parent / "models"


def resolve_model_dir(subdir: str) -> Path:
    """Return absolute path for a named model subfolder under :func:`model_root`.

    ``subdir`` examples: ``"faster-whisper-large-v3-turbo"``, ``"cosyvoice2"``,
    ``"silero_vad"``. Does NOT verify the directory exists.
    """
    return (model_root() / subdir).resolve()
