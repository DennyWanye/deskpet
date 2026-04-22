# P3-S4 — PyInstaller 冻结 backend（handoff）

**Branch:** `worktree-p3-s4-pyinstaller-backend`
**Plan:** `docs/superpowers/plans/2026-04-22-p3s4-pyinstaller-backend.md`
**Status:** code done + smoke PASS，待 merge 到 `master`

---

## 目标

让 `backend/` 可以被打包成单一可执行文件 `deskpet-backend.exe`，为
P3-S5（Tauri bundle 集成）铺路。目标机上**不需要**装 Python 解释器
或任何 backend 依赖。

Rust 侧 `backend_launch::resolve` 在 P3-S3 已留好 `Bundled` 分支，
只等一个能真的运行的 exe 放进 resource 目录。

## 交付物

| 路径 | 作用 |
|------|------|
| `backend/deskpet-backend.spec` | PyInstaller spec（onedir + console + no-UPX + CUDA DLL 过滤 + mypyc 伴生模块自动发现） |
| `scripts/build_backend.ps1` | 打包脚本：清 dist/build → 跑 PyInstaller → 统计体积 |
| `scripts/smoke_frozen_backend.py` | 冻结 exe 端到端自检：spawn → 等 `SHARED_SECRET=` → GET /health → 断言 status==ok + startup_errors 空 |
| `backend/providers/silero_vad.py` | 改用 PyPI `silero-vad` 包（带 JIT 模型），弃用 `torch.hub.load`（离线不可用） |
| `backend/pyproject.toml` | 加 `silero-vad>=5.1.2,<6` 运行时依赖；dev 加 `pyinstaller>=6.11` + `pyinstaller-hooks-contrib>=2024.8` |

## 验收数字

- **Bundle 体积：610 MB**（最初一版 3.9 GB，砍掉 3.3 GB CUDA 冗余）
- **冷启动（含全模型 preload）：5.2 s**（target < 30 s ✅）
- **smoke test：PASS** — `/health` 200、`status: ok`、`startup_errors: []`、
  silero-vad + faster-whisper + edge-tts 全部 preload 成功
- **单元测试**：298/298 pytest 全绿（+0 new，本 slice 没新加 python 测试）

## 踩过的坑（给未来的自己）

### 1. torch+cu124 = 3.5 GB 纯浪费

pip 默认拉的 torch 是 CUDA build，`torch/lib/` 一个目录就 3.6 GB：

| DLL | MB |
|-----|----|
| torch_cuda.dll | 957 |
| cudnn_engines_precompiled64_9.dll | 589 |
| cublasLt64_12.dll | 473 |
| cufft64_11.dll | 292 |
| cusparse64_12.dll | 276 |
| cudnn_adv64_9.dll | 242 |
| …（剩余 ~700 MB） | |

我们只用 torch 做一件事：`observability/vram.py::detect_vram_gb()` 调
`torch.cuda.is_available()` + `get_device_properties()`，给 tier
auto-selection 一个数字。这个函数全程 try/except，失败返回 0.0
（→ CPU tier）。所以 CUDA stack 整个冗余。

**解决：venv 装 torch CPU-only wheel：**

```powershell
pip install --index-url https://download.pytorch.org/whl/cpu `
    torch==2.6.0 torchaudio==2.6.0
```

`build_backend.ps1` 开头会校验 `torch.__version__` 是否以 `+cpu` 结尾，
装错了直接 fail fast 给出修复命令。

spec 里还留了一层**防御性的 CUDA DLL 过滤器**，万一有人在 venv 里又
装回了 CUDA torch，也会在打包时把 `torch/lib/{torch_cuda, cudnn, cublas,
cufft, cusparse, cusolver, curand, nvrtc, nvjitlink, cupti, nvtoolsext,
c10_cuda, caffe2_nvrtc, cudart}*.dll` 过滤掉。

**tradeoff：** 冻结 exe 内部 `torch.cuda.is_available() == False`，
auto-tier 降级到 `minimal`（CPU）。实际 ASR 推理**不受影响**——
faster-whisper 走的是 ctranslate2，后者自带独立的 `cudnn64_9.dll`
（在 `_internal/ctranslate2/`），和 torch 无关。用户可以在 config.toml
显式设 `asr.device = "cuda"`，ctranslate2 会去找系统 CUDA runtime（如
果装了）或优雅降级。

### 2. `c10_cuda.dll` / `caffe2_nvrtc.dll` / `cudart64_12.dll` 也得剥

第一次剥完大 CUDA DLL 后，torch 仍然 import 失败：

```
OSError: Error loading "caffe2_nvrtc.dll" or one of its dependencies.
```

原因：`torch/__init__.py::_load_dll_libraries` 用
`glob.glob("torch/lib/*.dll")` 把每个 DLL 都 LoadLibrary 一遍。
`caffe2_nvrtc.dll` 依赖 `nvrtc64_120_0.dll`，后者刚被剥掉了，所以
caffe2_nvrtc 加载失败 → torch import 失败。

**解决：把 `c10_cuda` / `caffe2_nvrtc` / `cudart` 三个 stub 也从
`torch/lib/` 剥掉。** glob 找不到它们就不会尝试 load，torch 顺利
降级为 CPU-only。（之后发现 `shm.dll` 也连带挂掉——最终直接改装
CPU wheel 彻底解决，不再和 CUDA 绑定文件层面纠缠。）

### 3. `tomli` 用 mypyc 编译，伴生模块 hash 名 PyInstaller 不认

`config.py` 第一行 `import tomli` 直接炸：

```
ModuleNotFoundError: No module named '3c22db458360489351e4__mypyc'
```

`tomli` 是 mypyc 编译出来的 C 扩展，需要一个**同级**的 hash 命名的
shared library 作伴（`<hash>__mypyc.cp311-win_amd64.pyd`，在
site-packages 根目录）。PyInstaller 默认不会把这种奇怪名字的 pyd
挂进 import 图。

**解决：spec 开头 `glob.glob(site-packages/*__mypyc.*.pyd)` 自动
发现所有 mypyc 伴生模块，把模块名加入 `hiddenimports`。** hash 变了
也不用改代码（只要升 wheel 重 build 一次）。

### 4. `silero-vad` 不能走 torch.hub

原来的 `providers/silero_vad.py`：

```python
model, _ = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True)
```

`torch.hub.load` 要求：
- 能联网 clone GitHub repo
- 有 `~/.cache/torch/hub/` 可写

冻结 exe 上俩都做不到。**解决：装 PyPI `silero-vad` 包，它把 JIT
模型 (`silero_vad/data/silero_vad.jit`) 作为 package data 出货；**
`collect_data_files("silero_vad")` 让 PyInstaller 把它打进 bundle。

```python
from silero_vad import load_silero_vad
self._model = load_silero_vad(onnx=False).eval()
```

模型权重和 hub v5 版本完全一致，行为不变。

### 5. 冷启动 5.2s 拆解

大头是 faster-whisper 模型加载（int8 large-v3-turbo ≈ 1.5 GB，HDD
环境会更慢）。SSD 上 5s 左右。Tauri 前端的 Loading 遮罩已经够用——
P3-S5 之后可以考虑把 preload 异步化，让 `/health` 先返回再后台 warm。

## 不在本 slice 里的事（留给 P3-S5）

- **Tauri `tauri.conf.json` resources**：目前 `backend/dist/deskpet-backend/`
  没被 bundle 进 Tauri 安装包。P3-S5 会把它挂到 Tauri 的
  `resources` 或 `externalBin`，让 `resource_dir()/backend/deskpet-backend.exe`
  解析到真实路径。
- **UI E2E against frozen exe**：完整走一遍 "点麦 → 讲话 → ASR → LLM →
  TTS"，但以冻结 exe 而不是 dev python 为 backend。smoke test 已经证明
  backend 本身能起、能 /health，但 UI 层调用要等 bundle 挂载完成后
  才能跑。P3-S5 的验收标准里会有这条。
- **Code signing / notarization**：也留给 P3-S5 或后续 slice。

## Manual E2E 做了什么（本 slice 范围内）

- `powershell scripts/build_backend.ps1` → 成功，输出 610 MB
- `python scripts/smoke_frozen_backend.py` → PASS，boot 5.2s
- spawn 过程中观察 stdout：所有 provider（silero-vad、faster-whisper、
  edge-tts）都正常 preload；`crash_reports` 目录正确创建；
  `SHARED_SECRET` 按期输出
- 故意诱导过两次失败（CUDA DLL 缺失 / mypyc 伴生模块缺失），确认
  crash_reporter 的 `uncaught_exception` 日志正常抓到栈

## 如果要 rebuild

```powershell
# 0. 确保 venv 是 torch CPU
G:\projects\deskpet\backend\.venv\Scripts\python.exe -c "import torch; print(torch.__version__)"
# 应该看到 2.6.0+cpu；不是的话：
# pip install --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 torchaudio==2.6.0

# 1. 打包
powershell scripts/build_backend.ps1

# 2. 自检
python scripts/smoke_frozen_backend.py
```

smoke test 通过 → 可以交给 P3-S5 做 Tauri bundle 集成。
