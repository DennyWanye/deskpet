"""Model / asset / user-data directory resolution.

P3-S1 birthed :func:`model_root` and :func:`resolve_model_dir`.
P3-S6 + P3-S7 extend this module to cover the full "where does each kind
of path live" question, because model storage and user data both moved
out of the repo tree when we started shipping a frozen bundle.

Three orthogonal questions this module answers:

1. **Where are model checkpoints?** (:func:`model_root` / :func:`resolve_model_dir`)
   Priority: ``DESKPET_MODEL_ROOT`` env → user models dir (LocalAppData) →
   ``sys._MEIPASS/models`` (frozen bundle) → dev-mode ``backend/models/``.

2. **Where does the user's mutable state live?** (:func:`user_data_dir`)
   Roaming AppData (follows the user across machines) — config.toml,
   memory.db, billing.db, logs/. Platformdirs picks the right OS dir.

3. **Where do big, regenerable files live?** (:func:`user_cache_dir` /
   :func:`user_models_dir`) Local (non-roaming) AppData. Models, HF
   cache, temp files. Won't bloat roaming profiles.

The ``DESKPET_*`` env vars exist so CI, E2E scripts, and dev workflows
can pin paths explicitly without touching the filesystem defaults.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

# Constants used by platformdirs. `appauthor=False` means "don't insert
# an AppAuthor\AppName folder stack on Windows" — we own the top-level
# name "deskpet" directly, giving clean paths like `AppData\Roaming\deskpet\`
# instead of `AppData\Roaming\deskpet\deskpet\`.
_APP_NAME = "deskpet"
_APP_AUTHOR: str | bool = False


def user_data_dir() -> Path:
    """Roaming user data directory (config, DBs, logs).

    Windows: ``%AppData%\\deskpet\\`` (e.g. ``C:\\Users\\X\\AppData\\Roaming\\deskpet``).
    macOS/Linux: platformdirs' XDG defaults.

    Override via ``DESKPET_USER_DATA_DIR`` — useful for tests that
    want a tmp path, or for users who keep config under version control.
    """
    override = os.environ.get("DESKPET_USER_DATA_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_data_dir(_APP_NAME, appauthor=_APP_AUTHOR, roaming=True))


def user_cache_dir() -> Path:
    """Non-roaming cache directory (HF cache, scratch).

    Windows: ``%LocalAppData%\\deskpet\\Cache\\``.

    Override via ``DESKPET_USER_CACHE_DIR``.
    """
    override = os.environ.get("DESKPET_USER_CACHE_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir(_APP_NAME, appauthor=_APP_AUTHOR))


def user_models_dir() -> Path:
    """Non-roaming models directory.

    Windows: ``%LocalAppData%\\deskpet\\models\\``.

    Distinct from :func:`user_cache_dir` because models are:
      * Large (multi-GB) — don't want them clobbered by cache-cleaners.
      * Slow to re-download (or unavailable without HF access).
      * Semi-permanent state — users may add their own checkpoints here.

    Override via ``DESKPET_MODEL_ROOT`` (same env var :func:`model_root`
    uses, so a single setting covers both lookup paths).
    """
    override = os.environ.get("DESKPET_MODEL_ROOT")
    if override:
        return Path(override)
    # Sits under LocalAppData\deskpet\ (alongside Cache\, Logs\ if we ever
    # want them) via platformdirs' non-roaming user_data_dir.
    local_base = Path(platformdirs.user_data_dir(_APP_NAME, appauthor=_APP_AUTHOR, roaming=False))
    return local_base / "models"


def user_log_dir() -> Path:
    """User log directory. On Windows platformdirs returns
    ``%LocalAppData%\\deskpet\\Logs``. We prefer keeping logs with the
    rest of the roaming user data (so support bundles capture them),
    so this intentionally deviates from the platformdirs default.

    Override via ``DESKPET_USER_LOG_DIR``.
    """
    override = os.environ.get("DESKPET_USER_LOG_DIR")
    if override:
        return Path(override)
    return user_data_dir() / "logs"


def model_root() -> Path:
    """Return the root directory containing all bundled model subfolders.

    Priority (first hit wins):

    1. ``DESKPET_MODEL_ROOT`` env — explicit override (CI/E2E/debug).
    2. :func:`user_models_dir` if it exists on disk — production path
       post-P3-S6. This is where the installer / setup script drops
       real model weights.
    3. ``sys._MEIPASS/models`` — PyInstaller bundle. Normally empty
       after P3-S6 (too big to fit under P3-G2's 3.5 GB cap), but kept
       as a fallback so tiny bundled checkpoints (or dev-only slim
       bundles) still resolve without extra setup.
    4. Dev fallback: ``backend/models/`` beside this file.

    Note: step 2 requires the directory to **exist** — if the user
    hasn't run ``setup_user_data.ps1`` yet we fall through to the
    bundle / dev path rather than returning a phantom empty dir.
    Callers use :func:`resolve_model_dir` to join a subfolder; they
    decide whether a missing subdir is fatal.
    """
    override = os.environ.get("DESKPET_MODEL_ROOT")
    if override:
        return Path(override)
    # P3-S6: check the production (LocalAppData) path first, but only
    # if it has been provisioned. This lets dev mode keep resolving to
    # backend/models/ without forcing devs to populate LocalAppData.
    user_dir = user_models_dir()
    if user_dir.is_dir():
        return user_dir
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


def ensure_user_dirs() -> None:
    """Create the standard user directories if missing. Idempotent.

    Called once at backend startup (from ``main.py``) so downstream
    code can assume ``user_data_dir() / "data"`` etc. exist without
    each caller racing on ``mkdir(parents=True)``.

    Failures are swallowed with a warning rather than raised — the
    backend should still come up on e.g. a read-only profile, using
    whatever paths the user explicitly configured.
    """
    for d in (
        user_data_dir(),
        user_data_dir() / "data",
        user_log_dir(),
        user_cache_dir(),
        user_models_dir(),
    ):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Non-fatal: user may have overridden to a read-only path.
            # Downstream writes will surface the real error.
            pass
