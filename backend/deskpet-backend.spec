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

import glob
import os
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --- 0. mypyc runtime shims ---------------------------------------------
# `tomli` (and a few other deps) are compiled with mypyc. Mypyc emits a
# companion top-level `<hash>__mypyc.cp311-win_amd64.pyd` module alongside
# the package; both must be importable or `import tomli` raises
# `ModuleNotFoundError: No module named '<hash>__mypyc'` at startup.
# Auto-discover them so the hash is never hardcoded (it changes when the
# upstream wheel is rebuilt).
_site_packages = sysconfig.get_paths()["purelib"]
_mypyc_modules = [
    os.path.basename(p).split(".", 1)[0]
    for p in glob.glob(os.path.join(_site_packages, "*__mypyc.*.pyd"))
]

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
hiddenimports += _mypyc_modules

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

# --- 3b. Defensive torch CUDA DLL strip ---------------------------------
# REQUIRED SETUP: the backend venv MUST install torch's CPU-only wheel
#   pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
# torch is used ONLY for VRAM detection (observability/vram.py), which
# wraps every call in try/except and falls back to vram_gb = 0.0 (→ CPU
# tier) when CUDA is unavailable. faster-whisper's GPU path uses
# ctranslate2's independent CUDA DLLs under _internal/ctranslate2/.
# If someone accidentally installs torch+cu124 (3.5 GB of CUDA DLLs under
# torch/lib/), the filter below strips the biggest offenders at build
# time so the bundle stays under P3-G2's 3.5 GB budget. On a CPU-only
# install this filter is a no-op.
_CUDA_DLL_PREFIXES = (
    "torch_cuda", "cudnn", "cublas", "cufft", "cusparse", "cusolver",
    "curand", "nvrtc", "nvjitlink", "cupti", "nvtoolsext",
    # torch/__init__.py's `_load_dll_libraries` globs every *.dll in
    # torch/lib/ and raises if any fails to load. These three depend on
    # the big CUDA stack; dropping them lets torch import as CPU-only
    # when CUDA deps are missing.
    "c10_cuda", "caffe2_nvrtc", "cudart",
)


def _is_torch_cuda_bloat(entry):
    dest = entry[0].replace("\\", "/").lower()
    if not dest.startswith("torch/lib/"):
        return False
    name = dest.rsplit("/", 1)[-1]
    return any(name.startswith(p) for p in _CUDA_DLL_PREFIXES)


a.binaries = [b for b in a.binaries if not _is_torch_cuda_bloat(b)]

# --- 3c. Re-bundle the minimal CUDA DLLs ctranslate2 actually needs -----
# After the torch CUDA strip above (saves ~2.9 GB), ctranslate2's GPU
# path dlopen's a small set of NVIDIA DLLs that torch's filter removed:
#   cublas64_12.dll, cublasLt64_12.dll  — matrix kernels (~370 MB together)
#   cudart64_12.dll                     — CUDA runtime shim (~600 KB)
#   nvrtc64_120_0.dll, nvrtc-builtins64_129.dll — runtime kernel compile
# ctranslate2 already ships cudnn64_9.dll in its own wheel.
#
# These DLLs come from the standalone pip packages:
#   pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12
# Dropped into `_internal/ctranslate2/` alongside cudnn64_9.dll so they
# resolve via ctranslate2's own AddDllDirectory registration.
_NVIDIA_DLL_DIRS = [
    os.path.join(_site_packages, "nvidia", "cublas", "bin"),
    os.path.join(_site_packages, "nvidia", "cuda_runtime", "bin"),
    os.path.join(_site_packages, "nvidia", "cuda_nvrtc", "bin"),
]
for _dir in _NVIDIA_DLL_DIRS:
    if not os.path.isdir(_dir):
        continue
    for _dll in glob.glob(os.path.join(_dir, "*.dll")):
        # Dest "ctranslate2/<name>.dll" → ends up next to cudnn64_9.dll
        # inside the ctranslate2 search dir registered by the wheel.
        a.binaries.append(
            (f"ctranslate2/{os.path.basename(_dll)}", _dll, "BINARY")
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
