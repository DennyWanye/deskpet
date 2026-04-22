# Packaging DeskPet (Phase 3)

**Status**: skeleton — filled out slice-by-slice as Phase 3 lands.
**Last updated**: 2026-04-22 (P3-S2 CUDA precheck)

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

## 4. PyInstaller mode (placeholder — P3-S4)

```
DeskPet/
├── deskpet.exe                (Tauri launcher)
├── resources/
│   ├── backend/
│   │   ├── deskpet-backend.exe
│   │   ├── _internal/          (PyInstaller onedir)
│   │   └── models/             (pre-bundled, datas= in spec)
│   │       ├── faster-whisper-large-v3-turbo/
│   │       └── cosyvoice2/
│   └── frontend/
└── config.toml
```

At runtime the backend process has `sys._MEIPASS` set to
`...\resources\backend\_internal`, so `resolve_model_dir` returns
`...\resources\backend\_internal\models\<subdir>`.

Spec file details land in P3-S4.

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

前端渲染 "缺少 NVIDIA GPU" 气泡的 UI 留给 P3-S3。

## 6. Troubleshooting (skeleton)

- `FileNotFoundError: .../models/faster-whisper-large-v3-turbo` in dev:
  the `backend/models/` directory is missing or still named `assets/`.
  Run `mv backend/assets backend/models` (legacy repos) or re-download
  weights.
- CUDA OOM on boot: lower `[asr].compute_type` from `float16` to `int8_float16`.
- Env override for debugging frozen paths: set
  `DESKPET_MODEL_ROOT=D:\deskpet-debug-models` to point the backend at
  an external model cache without rebuilding the installer.
