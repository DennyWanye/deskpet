# Packaging DeskPet (Phase 3)

**Status**: skeleton — filled out slice-by-slice as Phase 3 lands.
**Last updated**: 2026-04-22 (P3-S4 PyInstaller freeze)

---

## 1. Overview

DeskPet ships as a Tauri installer (`.msi` on Windows) that carries:

- The Rust supervisor / launcher (`deskpet.exe`)
- The React + Vite frontend (bundled into Tauri resources)
- A **frozen Python backend** built with PyInstaller `--onedir`
- All model weights (faster-whisper, CosyVoice2, silero-vad) pre-bundled

Target: clean NVIDIA + Windows 10/11 machine → double-click installer →
runs in < 90 s cold boot, no Python toolchain required.

**Non-goal (Phase 3)**: CPU-only fallback. DeskPet v0.3.x requires a CUDA-
capable NVIDIA GPU. The launcher shows a clear error message on unsupported
hardware (see P3-S2).

## 2. Path conventions (P3-S1)

All model paths flow through `backend/paths.py`:

```python
from paths import resolve_model_dir
whisper_dir = resolve_model_dir(config.asr.model_dir)   # absolute Path
cosy_dir    = resolve_model_dir(config.tts.model_dir)
```

`paths.model_root()` resolves in priority order:

| Order | Source | Use case |
|---|---|---|
| 1 | `DESKPET_MODEL_ROOT` env var | CI, debugging, custom install locations |
| 2 | `sys._MEIPASS / "models"` | PyInstaller frozen build (onefile OR onedir) |
| 3 | `backend/models/` beside `paths.py` | Source-code dev mode |

Config entries name **bare subfolders**, never relative paths:

```toml
[asr]
model_dir = "faster-whisper-large-v3-turbo"

[tts]
model_dir = "cosyvoice2"
```

Legacy config values like `"./assets/cosyvoice2"` are auto-stripped to
`"cosyvoice2"` at load time with a WARNING (P3-S1 backwards-compat).

**Forbidden**: any `Path(__file__).parent / "assets" / ...` in production
code paths. `scripts/check_no_hardcoded_assets.py` enforces this in CI.

## 3. Development mode

```
backend/
├── main.py
├── paths.py
├── config.py
└── models/                    # gitignored, provisioned manually
    ├── faster-whisper-large-v3-turbo/
    ├── cosyvoice2/
    └── silero_vad/            # (if cached)
```

Provisioning: developers download model weights once (script TBD in P3-S6).
`.gitignore` currently allows both `backend/assets/` (legacy) and
`backend/models/` (canonical) so a mid-migration clone keeps working.

## 4. PyInstaller 冻结产物 (P3-S4)

### 构建产物布局

```
backend/dist/deskpet-backend/
├── deskpet-backend.exe         (入口，console 模式，stdout 首行 SHARED_SECRET=)
└── _internal/                  (PyInstaller onedir)
    ├── python311.dll, pythonXX.pyd, ...
    ├── torch/lib/              (CPU-only，无 CUDA 大 DLL — 见下)
    ├── ctranslate2/            (自带 cudnn64_9.dll —— faster-whisper GPU 路径)
    ├── silero_vad/data/        (JIT 模型，包内 data)
    ├── faster_whisper/         (tokenizer.json 等 data)
    ├── memory/migrations/*.sql
    ├── tzdata/                 (IANA tz db，Windows 用)
    └── … 其余依赖 …
```

P3-S5 会把上面这个目录挂进 Tauri 的 `resources`，最终安装后变成：

```
DeskPet/
├── deskpet.exe                 (Tauri launcher)
├── resources/backend/
│   ├── deskpet-backend.exe     ← supervisor 认的就是这条路径
│   └── _internal/
└── resources/models/           (P3-S6 打包)
```

### 构建步骤

```powershell
# 前置：backend venv 必须是 torch CPU-only wheel
G:\projects\deskpet\backend\.venv\Scripts\python.exe -m pip install `
    --index-url https://download.pytorch.org/whl/cpu `
    torch==2.6.0 torchaudio==2.6.0

# 打包 + 自检
powershell scripts\build_backend.ps1
python scripts\smoke_frozen_backend.py
```

`build_backend.ps1` 开头会校验 torch 版本，不是 `+cpu` 直接 fail
fast 并给出修复命令。

### Spec 设计要点

**hidden imports**（`collect_submodules` + 手写补丁）：

- `faster_whisper`, `ctranslate2`, `silero_vad`：自动 subpackage 全收
- `tzdata`（zoneinfo Windows 依赖）、`prometheus_client`、`aiosqlite`
- `uvicorn.logging` / `uvicorn.loops.auto` / `uvicorn.protocols.http.auto` /
  `.h11_impl` / `.websockets.auto` / `.websockets_impl` / `uvicorn.lifespan.on`
  （uvicorn 这些是运行期按字符串 `importlib` 出来的，静态分析抓不到）
- `edge_tts`
- **mypyc 伴生模块**：`tomli` 用 mypyc 编译，同级有个 hash 命名的
  `<hash>__mypyc.cp311-win_amd64.pyd`，spec 里 `glob.glob` 自动发现
  所有 `*__mypyc.*.pyd` 加进 `hiddenimports`——硬编码 hash 会在 wheel
  重建时失效。

**data files**：

- `collect_data_files("silero_vad")` — JIT 模型
- `collect_data_files("faster_whisper")` — tokenizer
- `collect_data_files("tzdata")` — IANA tz db
- `collect_data_files("ctranslate2")` — 内置 config
- `("memory/migrations", "memory/migrations")` — 我们自己的 SQL 迁移

**excludes**（保守）：

```
tkinter, matplotlib, IPython, notebook, jupyter, pytest, _pytest
```

**bootloader 选项**：

- `console=True` — 关键，因为 supervisor 要从 stdout 读 `SHARED_SECRET=`
- `upx=False` — UPX 会破坏 CUDA DLL 的签名校验
- `exclude_binaries=True` + `COLLECT` — 走 onedir 而不是 onefile，
  启动快（onefile 每次冷启要解压 ~600 MB 到 %TEMP%）

### CUDA DLL 过滤器（体积救命）

没过滤版 3.9 GB，过滤后 610 MB。砍掉的都是我们不用的 CUDA stack：

```python
_CUDA_DLL_PREFIXES = (
    "torch_cuda", "cudnn", "cublas", "cufft", "cusparse", "cusolver",
    "curand", "nvrtc", "nvjitlink", "cupti", "nvtoolsext",
    "c10_cuda", "caffe2_nvrtc", "cudart",
)
a.binaries = [b for b in a.binaries if not _is_torch_cuda_bloat(b)]
```

**为什么安全**：torch 在进程里只被 `observability/vram.py::detect_vram_gb`
调用一次，拿 `torch.cuda.is_available()` 的布尔值。该函数全程
try/except，失败返回 0.0（→ tier 降级到 "minimal" CPU 路径）。
faster-whisper 走 ctranslate2，那一套的 CUDA DLL 是 ctranslate2
自带的（`_internal/ctranslate2/cudnn64_9.dll`），不依赖 torch。

**tradeoff**：冻结 exe 内部 auto-tier 永远拿不到 GPU 档位
（`minimal`），要跑 GPU ASR 得在 `config.toml` 里显式写
`asr.device = "cuda"`，让 ctranslate2 自己找系统 CUDA。

### 体积 & 启动时间基线

- **bundle：** 610 MB（P3-G2 预算 3.5 GB **含** 模型；models ~900 MB
  在 P3-S6 会挂进来，届时总量 ~1.5 GB，仍在预算内）
- **冷启动（含全模型 preload）：** 5.2 s on NVMe SSD

### 已知 runtime 假设

- `DESKPET_MODEL_ROOT` 环境变量必须指向包含 `faster-whisper-large-v3-turbo/`
  和 `cosyvoice2/` 的目录。P3-S6 之前手动设；P3-S6 之后由 launcher
  自动指向 bundle 里的 `resources/models/`。
- 进程要能在自己 CWD 下创建 `crash_reports/` 和 `data/billing.db`。
  打包后 exe 的 CWD = `deskpet-backend.exe` 所在目录（Rust supervisor
  spawn 时也这么设的）。

## 5. 硬件前置检查 (P3-S2)

DeskPet 在 Tauri `setup()` 钩子里跑一次 **NVIDIA GPU 探测**，失败就弹窗 + 退出，
**根本不拉起 Python backend**。这样用户在不支持的机器上不会看到"能启动但 ASR
永远 500"的假象。

### 检查通过的前提

1. 机器有 NVIDIA 显卡
2. 已装最新版 NVIDIA 驱动（驱动自带 `nvml.dll`，无需 CUDA Toolkit）
3. `nvml_wrapper::Nvml::init()` 能加载并返回 ≥1 个设备

### 用户侧错误分类

| Rust 枚举 | 触发 | 用户弹窗文案要点 |
|---|---|---|
| `NvmlInitFailed` | `nvml.dll` 缺失 / 加载失败 / NVML_Init 出错 | "请安装最新 NVIDIA 驱动并重启" |
| `NoDevices` | NVML 能初始化但 `device_count() == 0` | "没有检测到 NVIDIA 显卡" |
| `DeviceQueryFailed` | 查询第 0 号设备名/显存失败 | "驱动可能已损坏，请重装" |

### Backend 第二道防线

Rust 前置检查挡不住所有情况（如 `nvml.dll` 存在但 CUDA runtime 依赖
`cudart64_*.dll` 不匹配），所以 backend lifespan 的 `engine.load()` 异常
**不再吞掉**，而是走 `observability/startup.py::StartupErrorRegistry`：

```python
for name in ("vad_engine", "asr_engine", "tts_engine"):
    try:
        await engine.load()
    except Exception as exc:
        startup_errors.record(name, exc)  # 分类为 CUDA_UNAVAILABLE / MODEL_DIR_MISSING / UNKNOWN
```

结构化错误通过两条通道暴露：

1. **`GET /health`** → `status: "degraded" | "ok"` + `startup_errors[]`
2. **`/ws/control` 握手后第一帧** → `{"type": "startup_status", "degraded": bool, "errors": [...]}`

前端渲染 "缺少 NVIDIA GPU" 气泡的 UI 留给 P3-S8 splash screen。

## 6. Backend 路径解析 (P3-S3)

Rust supervisor 是 backend 路径的**唯一权威源**。前端 `invoke("start_backend")`
无参，Rust 侧 `backend_launch::resolve(&app)` 按以下优先级定位：

| # | 条件 | 启动形式 |
|---|---|---|
| 1 | `<resource_dir>/backend/deskpet-backend.exe` 存在 | `Bundled` — 直接跑冻结 exe (cwd = exe 目录) |
| 2 | `DESKPET_BACKEND_DIR` 环境变量非空 | `Dev` — `<DESKPET_PYTHON 或 <dir>/.venv/Scripts/python.exe> main.py` |
| 3 | 编译期注入 `DESKPET_DEV_ROOT/backend/main.py` 存在 | `Dev` — 默认 venv 解释器 |
| 4 | 都不中 | `ResolveError::NoBackendFound{ tried }` → 中文弹窗 |

**Dev 工作流**（无 env 变量）：`build.rs` 在编译时把
`CARGO_MANIFEST_DIR/../..` 注入为 `DESKPET_DEV_ROOT`，因此
`npm run tauri:dev` 在源码检出里开箱即用。

**Dev 想指向别处**：临时设 `DESKPET_BACKEND_DIR=D:\alt\backend`，
可选 `DESKPET_PYTHON=E:\py\python.exe` 覆盖解释器。空字符串视作 unset。

**打包 release**：P3-S5 会把 `deskpet-backend.exe` 放进 bundle
resources，届时优先级 1 自动生效，后续 fallback 永远走不到。

错误弹窗复用 P3-S2 的 `tauri-plugin-dialog` 通道（中文文案来自
`backend_launch::format_user_message`）。

## 7. Troubleshooting (skeleton)

- `FileNotFoundError: .../models/faster-whisper-large-v3-turbo` in dev:
  the `backend/models/` directory is missing or still named `assets/`.
  Run `mv backend/assets backend/models` (legacy repos) or re-download
  weights.
- CUDA OOM on boot: lower `[asr].compute_type` from `float16` to `int8_float16`.
- Env override for debugging frozen paths: set
  `DESKPET_MODEL_ROOT=D:\deskpet-debug-models` to point the backend at
  an external model cache without rebuilding the installer.
