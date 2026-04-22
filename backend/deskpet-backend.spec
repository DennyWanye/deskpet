# P3-S4 — PyInstaller spec for the frozen backend.
#
# Produces `dist/deskpet-backend/deskpet-backend.exe` + a `_internal/`
# sidecar directory with all Python bytecode, native DLLs, and the
# data files listed below. The Rust supervisor (post-P3-S3) picks
# this up via the `Bundled` branch of `backend_launch::resolve`.
#
# Usage (from `backend/`):
#   .\.venv\Scripts\python.exe -m PyInstaller deskpet-backend.spec --noconfirm --clean
#
# Or via the wrapper:
#   powershell ..\scripts\build_backend.ps1

# ruff: noqa — PyInstaller injects builtins like `Analysis`, `PYZ`, `EXE`,
# `COLLECT`, `block_cipher` into this file's scope.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --- 1. Hidden imports --------------------------------------------------
# Providers that dlopen / importlib their implementations at runtime
# won't be discovered by the default import graph. List every top-level
# package that the frozen exe must be able to `import` lazily.
hiddenimports: list[str] = []
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("ctranslate2")
hiddenimports += collect_submodules("silero_vad")
hiddenimports += [
    "tzdata",                   # zoneinfo needs this on Windows
    "prometheus_client",
    "aiosqlite",
    # uvicorn auto-loaders
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    # edge-tts uses aiohttp via optional import chain
    "edge_tts",
]

# --- 2. Data files ------------------------------------------------------
# (source, dest-inside-bundle) tuples. Use collect_data_files() for
# installed packages; hardcode relative paths for our own repo files.
datas: list[tuple[str, str]] = []
datas += collect_data_files("silero_vad")          # silero_vad/data/*.jit
datas += collect_data_files("faster_whisper")      # tokenizer.json
datas += collect_data_files("tzdata")              # IANA tz db
datas += collect_data_files("ctranslate2")         # any shipped configs
datas += [
    ("memory/migrations", "memory/migrations"),    # SQL migration scripts
]

# --- 3. Analysis --------------------------------------------------------
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Things torch/transformers sometimes drag in that we never use.
        # Keep this conservative — overzealous excludes cause runtime
        # ImportError deep in a stack trace.
        "tkinter",
        "matplotlib",
        "IPython",
        "notebook",
        "jupyter",
        "pytest",
        "_pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# --- 4. EXE + COLLECT ---------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="deskpet-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                                     # UPX breaks CUDA DLLs
    console=True,                                  # SHARED_SECRET on stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="deskpet-backend",                        # dist/<this>/
)
