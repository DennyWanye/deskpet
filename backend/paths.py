# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Model / asset / user-data directory resolution.

P3-S1 birthed :func:`model_root` and :func:`resolve_model_dir`.
P3-S6 + P3-S7 extend this module to cover the full "where does each kind
of path live" question, because model storage and user data both moved
out of the repo tree when we started shipping a frozen bundle.

P4-S22+ adds **portable mode**: when an ``<install_dir>/userdata/``
folder exists alongside the frozen exe, ALL user-mutable state
(config, SQLite, logs, models, skills, plugins) lives under it
instead of `%AppData%`. This:
  * keeps the C: drive clean (everything next to the install)
  * survives MSI uninstall (Windows Installer only deletes files it
    explicitly listed at install time; an empty userdata/ folder
    sentinel created post-install is preserved)
  * makes "move my install + data to another drive" a directory copy

Detection: the bundle ships an empty ``<install_dir>/userdata/.deskpet-portable``
sentinel. If that sentinel exists, we're portable; if not, classic
AppData layout.

Three orthogonal questions this module answers:

1. **Where are model checkpoints?** (:func:`model_root` / :func:`resolve_model_dir`)
2. **Where does the user's mutable state live?** (:func:`user_data_dir`)
3. **Where do big, regenerable files live?** (:func:`user_cache_dir` /
   :func:`user_models_dir`)

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

def _install_dir() -> Path | None:
    """Return the directory containing the frozen exe, or None in dev mode."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def _portable_userdata_dir() -> Path | None:
    """Return ``<install_root>/userdata/`` when running from a frozen install.

    Layout we expect (Tauri MSI ships this)::

        <install_root>/                  (e.g. G:\\tools\\deskpet\\)
            deskpet.exe                  (Tauri main exe)
            backend/
                deskpet-backend.exe      ← sys.executable when frozen
                _internal/
            userdata/
                config.toml
                data/state.db
                models/
                logs/
                skills/

    The backend exe's ``Path.parent`` is `<install>/backend/`; we walk
    upward to find a sibling ``userdata/`` directory (or its parent
    that *should* contain one). We don't gate on a sentinel file —
    DeskPet has no legacy users to preserve `%AppData%` state for, so
    "frozen ⇒ portable" is the simpler invariant.

    The MSI installer's component manifest does NOT list any files
    under ``userdata/``, so MSI uninstall leaves the entire userdata
    tree (state.db, config.toml, models/, skills/) untouched. That's
    how "uninstall doesn't lose my chat history" works.
    """
    base = _install_dir()
    if base is None:
        return None
    # Pick the "install root" deterministically based on the exe's
    # parent dir name:
    #   exe parent named "backend" → Tauri layout, root is 1 level up
    #   anything else              → standalone, exe parent IS the root
    # Defensive: if the chosen root isn't writable (e.g. Program Files
    # without admin), walk one more level up before giving up.
    if base.name.lower() == "backend":
        candidates_up = (1, 2, 0)
    else:
        candidates_up = (0, 1, 2)
    for steps_up in candidates_up:
        anchor = base
        for _ in range(steps_up):
            anchor = anchor.parent
        candidate = anchor / "userdata"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        # Prove the dir is writable: probe-write then delete.
        probe = candidate / ".deskpet-write-probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            continue
        return candidate
    return None


def is_portable_mode() -> bool:
    """True when running from a frozen install with a writable userdata dir."""
    return _portable_userdata_dir() is not None


def user_data_dir() -> Path:
    """Roaming user data directory (config, DBs, logs).

    Resolution order:
      1. ``DESKPET_USER_DATA_DIR`` env override (tests, power users).
      2. **Portable mode** — ``<install_dir>/userdata/`` (if sentinel
         present). All state lives next to the install, not under C:.
      3. Classic — Windows ``%AppData%\\deskpet\\``, macOS/Linux XDG.
    """
    override = os.environ.get("DESKPET_USER_DATA_DIR")
    if override:
        return Path(override)
    portable = _portable_userdata_dir()
    if portable is not None:
        return portable
    return Path(platformdirs.user_data_dir(_APP_NAME, appauthor=_APP_AUTHOR, roaming=True))


def output_dir(kind: str = "") -> Path:
    """用户生成物统一输出目录: ``<user_data>/OutPut[/<kind>]``。

    桌宠生成的所有交付物(PPT/文档/图片等)都落这里,按类型分子目录
    (如 ``OutPut/PPT``),用户好找。目录按需创建;创建失败返回路径本身
    (调用方写文件时自然报错,不在这里抛)。
    """
    base = user_data_dir() / "OutPut"
    target = base / kind if kind else base
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 — 只读盘等;调用方写文件时报错
        pass
    return target


def user_cache_dir() -> Path:
    """Cache directory (HF scratch, temp files).

    Portable: ``<install_dir>/userdata/cache``.
    Classic:  ``%LocalAppData%\\deskpet\\Cache\\`` (Windows).

    Override via ``DESKPET_USER_CACHE_DIR``.
    """
    override = os.environ.get("DESKPET_USER_CACHE_DIR")
    if override:
        return Path(override)
    portable = _portable_userdata_dir()
    if portable is not None:
        return portable / "cache"
    return Path(platformdirs.user_cache_dir(_APP_NAME, appauthor=_APP_AUTHOR))


def user_models_dir() -> Path:
    """Models directory.

    Portable: ``<install_dir>/userdata/models``.
    Classic:  ``%LocalAppData%\\deskpet\\models\\`` (Windows).

    Override via ``DESKPET_MODEL_ROOT`` (same env var :func:`model_root`
    uses, so a single setting covers both lookup paths).
    """
    override = os.environ.get("DESKPET_MODEL_ROOT")
    if override:
        return Path(override)
    portable = _portable_userdata_dir()
    if portable is not None:
        return portable / "models"
    # Sits under LocalAppData\deskpet\ (alongside Cache\, Logs\ if we ever
    # want them) via platformdirs' non-roaming user_data_dir.
    local_base = Path(platformdirs.user_data_dir(_APP_NAME, appauthor=_APP_AUTHOR, roaming=False))
    return local_base / "models"


def user_log_dir() -> Path:
    """User log directory.

    Portable: ``<install_dir>/userdata/logs``.
    Classic:  ``user_data_dir() / "logs"`` (under %AppData% on Windows).

    We deliberately put logs alongside the rest of the user data
    (rather than the platformdirs ``user_log_dir`` default of a
    separate "Logs" subtree) so support bundles capture them in one
    place.

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
    """Return absolute path for a named model subfolder, with multi-source fallback.

    ``subdir`` examples: ``"faster-whisper-large-v3-turbo"``, ``"cosyvoice2"``,
    ``"silero_vad"``, ``"bge-m3-int8"``.

    Priority (first **existing** directory wins so user-installed weights
    override the bundle, and bundled weights override "nothing"):

    1. ``DESKPET_MODEL_ROOT/<subdir>`` — explicit env override (CI/E2E).
    2. ``user_models_dir()/<subdir>`` — user-installed (let the user swap
       in their own quantization or fine-tune by dropping it here).
    3. ``sys._MEIPASS/models/<subdir>`` — frozen bundle's bundled weights
       (P4-S20+ install bundle ships BGE-M3 + faster-whisper here).
    4. ``backend/assets/<subdir>`` — dev-mode equivalent of #3 (this is
       what PyInstaller reads from when freezing).
    5. ``backend/models/<subdir>`` — legacy dev-mode location (kept for
       back-compat with pre-P4-S20 dev setups).

    If none of the above exist, returns the path under
    ``user_models_dir()/<subdir>`` so error messages point users at the
    "right" place to drop missing weights. Callers must handle the
    "directory still doesn't exist" case (e.g. faster-whisper falls back
    to HuggingFace auto-download, BGE-M3 falls back to mock vectors).
    """
    override = os.environ.get("DESKPET_MODEL_ROOT")
    if override:
        return (Path(override) / subdir).resolve()

    backend_dir = Path(__file__).resolve().parent
    candidates: list[Path] = [user_models_dir() / subdir]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "models" / subdir)
    candidates.append(backend_dir / "assets" / subdir)
    candidates.append(backend_dir / "models" / subdir)

    for c in candidates:
        if c.is_dir():
            return c.resolve()

    # Nothing exists → return the user_models_dir path so missing-model
    # errors point at the writable user-friendly location.
    return (user_models_dir() / subdir).resolve()


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
        user_data_dir() / "workspace",  # P4-S22: MCP filesystem root
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
