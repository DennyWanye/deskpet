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
# 2026-05-30 P0 bug fix #10: deskpet/tools/__init__.py uses pkgutil
# .iter_modules to dynamically discover + import every tool module
# (excel_tools, doc_tools, ppt_tools, image_tools, etc). PyInstaller's
# static analysis can't see these — without explicit hidden_imports the
# office tools (excel_create / doc_create / ppt_create) NEVER reach the
# registry → LLM tool list lacks them → B1/B2/B5 (and many others) fail.
# Discovered via CDP-driven prompt asking LLM to enumerate tools.
hiddenimports += collect_submodules("deskpet.tools")
hiddenimports += collect_submodules("deskpet.skills")
hiddenimports += ["sqlite_vec"]                    # P4-S20: L3 vector recall
hiddenimports += [
    "tzdata",                   # zoneinfo needs this on Windows
    # config.py additive feature-flag backfill writes via tomlkit. It's a
    # static import inside _merge_missing_feature_flags (lazy, try-guarded),
    # so static analysis may miss it — pin it explicitly to be safe.
    "tomlkit",
    "prometheus_client",
    "aiosqlite",
    # P3-S6+S7: user data / cache / models dir resolution at startup.
    "platformdirs",
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
    # deep-research CDP-Edge JS 渲染兜底(research_cdp_edge.py)直接 import websockets
    # 连本地无头 Edge → 显式入包,使冻结正式包也能用 js_render=cdp-edge(Windows)。
    "websockets",
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
datas += collect_data_files("sqlite_vec", includes=["*.dll"])  # vec0.dll for L3 recall
datas += [
    # P4-S22 fix: ship the canonical migrations directory under
    # ``deskpet/memory/migrations`` (where the actual v9/v10/v11 SQL
    # files live). The legacy ``memory/migrations`` only contains
    # ``001_initial.sql`` (long stale) — keep both for back-compat
    # with any tests that still touch the legacy path.
    ("memory/migrations", "memory/migrations"),    # legacy path
    ("deskpet/memory/migrations", "deskpet/memory/migrations"),  # canonical
    # 2026-05-30 P0 bug fix #7: ship builtin skills directory.
    # Production install had `skill.reload_ok count=0` because PyInstaller
    # never bundled `deskpet/skills/builtin/` (it's a data tree, not a
    # Python module). Result: B1-B10 skill tests all failed — LLM真选
    # skill_invoke 但 SkillLoader 找不到 excel-generate / doc-edit / ppt-
    # generate 等 → 没文件生成 → fake completion 漏网。
    # Discovered via CDP-driven R3-3 test + backend log skill.reload_ok=0.
    ("deskpet/skills/builtin", "deskpet/skills/builtin"),
    # P4-S21 #12: ship the unified-schema config.toml so seed_user_config_if_missing
    # has a source to seed from / migrate legacy installs against. Without this,
    # frozen builds with no <exe_dir>/config.toml returned None and the migration
    # path was a no-op.
    ("../config.toml", "."),
]

# --- 2a-bis. busybox-w32 (P5-S2 — 2026-05-10) --------------------------
# Ship the ~700KB busybox.exe so end users without Git for Windows still
# get a competent unix-like shell for run_shell. run_shell.py picks
# this up via _bundled_busybox_path() — checks _MEIPASS first, so the
# datas entry below is what makes the frozen build self-sufficient.
#
# The file is fetched by `scripts/download_busybox.ps1` and committed
# to the repo at `resources/busybox-w32/busybox.exe`. If that file
# isn't present at build time, the spec gracefully omits it (the
# runtime falls back to PowerShell or cmd, which the tier system
# handles).
import os as _os  # noqa: PLC0415
_busybox_src = _os.path.join("..", "resources", "busybox-w32", "busybox.exe")
if _os.path.isfile(_busybox_src):
    datas += [(_busybox_src, ".")]
    print(f"[spec] bundling busybox: {_busybox_src}")
else:
    print(
        f"[spec] WARNING: busybox not found at {_busybox_src} — "
        "frozen build will rely on Git Bash / PowerShell / cmd at runtime. "
        "Run scripts/download_busybox.ps1 to enable bundled fallback."
    )

# --- 2b. Bundled model weights (P4-S20+ install bundle) -----------------
# Ship BGE-M3 (vector embedder, ~2.2 GB) and faster-whisper-large-v3-turbo
# INT8 (ASR, ~1.5 GB) inside the frozen bundle. Lands at
# `_internal/models/<subdir>/...` and is picked up by paths.resolve_model_dir
# when the user has not provisioned `%LocalAppData%/deskpet/models/<subdir>/`.
#
# Together these add ~3.7 GB to the bundle. NSIS LZMA compression usually
# halves it, so the final installer is ~2 GB. Acceptable for "download &
# run, no further setup" UX.
#
# Sources are populated by the dev workflow:
#   - assets/bge-m3-int8/                        (robocopy from %LocalAppData%/deskpet/models/)
#   - assets/faster-whisper-large-v3-turbo/      (copied from HF cache,
#                                                 mobiuslabsgmbh INT8 ct2 build)
# Both directories are gitignored — see backend/.gitignore.
# P4-S20 install bundle Plan A (post-NSIS-mmap-failure):
# We previously bundled BGE-M3 (1.1 GB fp16) + faster-whisper INT8 (1.5 GB)
# directly into the PyInstaller dist. Tauri NSIS makensis is 32-bit and
# its cumulative mmap address space caps out around ~3.5 GB; bundling
# 7.7 GB of resources blows that limit with `Internal compiler error
# #12345: error mmapping file (...) is out of range`. NSIS amd64-Unicode
# builds exist as community forks (e.g. negrutiu/nsis) but introduce a
# trust dependency we'd rather avoid.
#
# Plan A: ship a thin (~1.5 GB) bundle, rely on first-run download.
# `paths.resolve_model_dir` already does multi-source fallback
# (user_models_dir → _MEIPASS/models → backend/assets), so:
#  - Frozen bundle: models NOT bundled → first-run code (or user-run
#    download script) populates `%LocalAppData%/deskpet/models/`.
#  - Dev mode: backend/assets/ still has the weights → resolves there.
# Either way the runtime path is identical.
#
# P4-S20 MSI fat bundle: ship models inside the installer for true
# zero-config "download → install → launch" UX. Set
# DESKPET_BUNDLE_MODELS=0 to opt out (yields a thin bundle that relies
# on first-run download via scripts/setup_models.py).
if os.environ.get("DESKPET_BUNDLE_MODELS", "1") == "1":
    _BUNDLED_MODELS = [
        ("assets/bge-m3-int8", "models/bge-m3-int8"),
        ("assets/faster-whisper-large-v3-turbo", "models/faster-whisper-large-v3-turbo"),
    ]
    for _src, _dest in _BUNDLED_MODELS:
        if os.path.isdir(_src):
            datas.append((_src, _dest))
            print(f"[spec] bundling model: {_src} -> {_dest}")
        else:
            print(f"[spec] WARN: bundled model source missing, skipping: {_src}")
else:
    print("[spec] thin bundle: models NOT bundled (DESKPET_BUNDLE_MODELS=0)")

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
        # FlagEmbedding 推理经 transformers.trainer 拉入 datasets(训练用)，
        # 但推理 lazy `is_datasets_available()` 可选；其 PyInstaller 隔离
        # 子进程 bindepend 导入会崩(SubprocessDiedError)→ 排除即修复 +
        # 减体积，运行时 is_datasets_available()→False 安全降级。
        "datasets",
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
    # P4-S20 fat MSI bundle: empty strip list — keep the full torch
    # CUDA stack. shm.dll directly imports torch_cuda.dll which in
    # turn lazily binds cudnn / cublas / cufft / etc., so cherry-
    # picking what to strip is fragile (broke on first try).
    # MSI (vs NSIS) handles 7+ GB dist sizes natively, so we don't
    # need the strip dance.
    # Original aggressive list (kept for reference, was active in
    # P3-S5's NSIS-only era):
    #   "torch_cuda", "cudnn", "cublas", "cufft", "cusparse",
    #   "cusolver", "curand", "nvrtc", "nvjitlink", "cupti",
    #   "nvtoolsext", "c10_cuda", "caffe2_nvrtc", "cudart",
)


def _is_torch_cuda_bloat(entry):
    dest = entry[0].replace("\\", "/").lower()
    if not dest.startswith("torch/lib/"):
        return False
    name = dest.rsplit("/", 1)[-1]
    return any(name.startswith(p) for p in _CUDA_DLL_PREFIXES)


# P4-S20 thin bundle: re-enabled strip. We keep c10_cuda + cudart in
# torch/lib/ (removed from `_CUDA_DLL_PREFIXES`) so shm.dll's deps
# resolve, while everything else (cudnn family, cufft, curand,
# cusolver, cusparse — collectively ~3 GB) is dropped. main.py
# registers `_MEIPASS/ctranslate2/` in the DLL search path BEFORE any
# `import torch`, so torch's `_load_dll_libraries` finds cudart/cublas
# there. Without the strip, dist is ~5 GB (vs ~1.5 GB with strip),
# which blows NSIS 32-bit makensis's cumulative mmap budget.
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
