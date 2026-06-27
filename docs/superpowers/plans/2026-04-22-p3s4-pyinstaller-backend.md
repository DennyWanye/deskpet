# P3-S4 — PyInstaller 冻结 backend 产物

- **Slice**: Phase 3 / S4
- **上游**: P3-S1 (paths), P3-S2 (gpu_check), P3-S3 (backend_launch) —— 全部 merged
- **估工**: 4 天
- **目标分支**: `p3-s4-pyinstaller-backend`（worktree）
- **合并目标**: `master`

---

## 1. 背景 / Why

P3-S3 让 Rust supervisor 自己按 `bundle → env → dev-root` 三级解析 backend
路径。优先级 1 指向 `<resource_dir>/backend/deskpet-backend.exe`，**但这个
文件目前不存在**。P3-S4 负责生产这个文件。

产出不依赖用户装 Python：安装包里塞一份冻结的 CPython + backend 字节码
+ 所有 wheel 二进制依赖（torch / ctranslate2 / faster-whisper / …）。
用户双击 `deskpet.exe`，Rust 走 Bundled 分支直接 spawn 子进程。

**本 slice 的阻塞点**（提前认识到避免踩坑）：

1. **`torch.hub.load()` 在冻结环境失败** —— `providers/silero_vad.py`
   现在用 `torch.hub.load("snakers4/silero-vad", ...)`，该 API 会 clone
   GitHub repo 到 `~/.cache/torch/hub/`。用户首启可能没网，即便有网也
   是不可控的运行时依赖。**必须切到 `silero-vad` PyPI 包**，它把 JIT
   模型作为 package data 发布，PyInstaller 自动搜集。
2. **`ctranslate2` 动态加载 CUDA plugin** —— hidden imports 列白名单
3. **`faster-whisper` 的 tokenizer / configs** —— datas 显式拷
4. **CUDA runtime DLL 依赖** —— D3-S4a 决定了 bundle CUDA DLL，但
   torch 2.6 wheel 自带 `torch/lib/*.dll`（cudart / cublas / cudnn），
   PyInstaller 默认会搜集到 `_internal/torch/lib/`，验证是否够用即可
5. **模型文件不在本 slice** —— P3-S6 才 bundle 模型，本 slice 的冻结
   产物通过 `DESKPET_MODEL_ROOT` env 指向 dev 仓库的 `backend/models/`
   做 smoke 测

## 2. 范围

### In Scope
- [ ] 替换 `providers/silero_vad.py`：`torch.hub.load` → `from silero_vad import load_silero_vad, get_speech_timestamps`（PyPI `silero-vad>=5.1.2`）
- [ ] 在 `backend/pyproject.toml` 加 `silero-vad>=5.1.2` 依赖
- [ ] 删除代码里对 `torch.hub` 的依赖（grep `torch.hub` 确认 0 残留）
- [ ] 新建 `scripts/build_backend.ps1`：调用 PyInstaller，输出到 `backend/dist/deskpet-backend/`
- [ ] 新建 `backend/deskpet-backend.spec`：PyInstaller spec 文件（比命令行参数更可维护）
  - `hiddenimports`: `ctranslate2`, `faster_whisper`, `silero_vad`, `tzdata`, `prometheus_client`, `aiosqlite`, `uvicorn.workers`（按需）
  - `datas`: `memory/migrations/*.sql`, `faster_whisper` tokenizer files（collect_data_files）
  - `collect_submodules`: `faster_whisper`, `ctranslate2`, `silero_vad`
  - `console=True`（要能读 stdout 拿 SHARED_SECRET）
  - `name='deskpet-backend'`（生成 `deskpet-backend.exe`）
- [ ] 新建 `scripts/smoke_frozen_backend.py`：
  - 启动 `backend/dist/deskpet-backend/deskpet-backend.exe`
  - 读 stdout 拿 `SHARED_SECRET=...`
  - GET `/health`，校验 `status="ok"`
  - 传一段 wav 测 ASR（可选；要依赖模型，或跳过）
  - kill child，exit code = 0 代表 green
- [ ] `.gitignore`: `backend/dist/`, `backend/build/`, `*.spec.bak`
- [ ] `docs/PACKAGING.md` §7: "PyInstaller 产物结构"（目录树 + 体积基线）

### Out of Scope
- PyInstaller 产物放进 Tauri `bundle.resources`（P3-S5）
- 模型塞 installer（P3-S6）
- AppData 路径迁移（P3-S7）
- Splash dialog（P3-S8）
- 真实 MSI 打包（P3-S10）

---

## 3. 技术方案

### 3.1 silero-vad 重构

**Before**（`backend/providers/silero_vad.py`）：

```python
model, _ = torch.hub.load(
    "snakers4/silero-vad", "silero_vad", trust_repo=True
)
```

**After**：

```python
from silero_vad import load_silero_vad  # PyPI 包，JIT 模型打包为 package data
self._model = load_silero_vad(onnx=False)  # 保持 PyTorch backend（CPU 足够）
```

API 表面：silero-vad PyPI 包的 `load_silero_vad()` 返回同一个 `torch.jit`
模型对象；调用点 `self._model(audio_tensor, 16000).item()` 不变。

**兼容性验证**：pypi v5.1.2 === 我们现在 hub 拿的 v5.x，模型权重一致。
跑 `pytest backend/tests/test_silero_vad.py` 应绿。

### 3.2 PyInstaller spec 文件

```python
# backend/deskpet-backend.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

hiddenimports = [
    *collect_submodules("faster_whisper"),
    *collect_submodules("ctranslate2"),
    *collect_submodules("silero_vad"),
    "tzdata",
    "prometheus_client",
    "aiosqlite",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

datas = [
    *collect_data_files("silero_vad"),            # silero JIT pt
    *collect_data_files("faster_whisper"),         # tokenizer.json 等
    *collect_data_files("tzdata"),
    ("memory/migrations", "memory/migrations"),    # SQL 迁移脚本
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 明确不需要：PyInstaller 不应该扫这些
        "tkinter", "matplotlib", "IPython", "notebook", "jupyter",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="deskpet-backend",
    debug=False,
    strip=False,
    upx=False,                                     # UPX 和 CUDA DLL 不兼容
    console=True,                                  # 必须 True — SHARED_SECRET 走 stdout
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    name="deskpet-backend",                        # 输出目录名
)
```

### 3.3 `scripts/build_backend.ps1`

```powershell
# Clean
Remove-Item -Recurse -Force backend/dist, backend/build -ErrorAction SilentlyContinue

# Build
Push-Location backend
& .\.venv\Scripts\python.exe -m PyInstaller deskpet-backend.spec --noconfirm --clean
Pop-Location

# Size report
$size = (Get-ChildItem backend/dist/deskpet-backend -Recurse | Measure-Object -Property Length -Sum).Sum
Write-Host "frozen size: $([math]::Round($size / 1MB, 1)) MB"
```

### 3.4 冷启动 smoke 脚本

```python
# scripts/smoke_frozen_backend.py
import subprocess, time, urllib.request, json, os, sys, pathlib

EXE = pathlib.Path("backend/dist/deskpet-backend/deskpet-backend.exe").resolve()
assert EXE.exists(), f"{EXE} not built yet — run scripts/build_backend.ps1 first"

env = {**os.environ, "DESKPET_MODEL_ROOT": str(pathlib.Path("backend/models").resolve())}
proc = subprocess.Popen(
    [str(EXE)], cwd=EXE.parent, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, bufsize=1,
)

secret = None
t0 = time.time()
for line in proc.stdout:
    print(line, end="")
    if line.startswith("SHARED_SECRET="):
        secret = line.split("=", 1)[1].strip()
        break
    if time.time() - t0 > 120:
        proc.kill()
        sys.exit("TIMEOUT waiting for SHARED_SECRET (>120s)")

assert secret, "backend exited without printing SHARED_SECRET"

# Health probe
import urllib.request
req = urllib.request.Request(f"http://127.0.0.1:8100/health?secret={secret}")
with urllib.request.urlopen(req, timeout=10) as resp:
    body = json.loads(resp.read())

print(f"[smoke] boot time: {time.time()-t0:.1f}s")
print(f"[smoke] /health: {body}")
assert body["status"] == "ok", f"degraded: {body}"

proc.terminate()
proc.wait(timeout=5)
print("[smoke] PASS")
```

### 3.5 PyInstaller hook 坑位清单（预防）

| 症状 | 原因 | 修 |
|---|---|---|
| `ImportError: libiomp5md.dll` 或 `torch\lib\*.dll not found` | torch wheel 的 lib/ 没被 COLLECT 搜到 | `pyinstaller-hooks-contrib` 已内置 torch hook；装 `pip install pyinstaller-hooks-contrib>=2024.8` |
| `ctranslate2: cannot load plugin` | ctranslate2 有 plugin DLL 运行时动态 dlopen | `collect_submodules("ctranslate2")` + 手动 `binaries` 里加 `ctranslate2/*.dll` |
| `silero_vad: model file not found` | JIT pt 没被 collect_data_files | pypi 包把 `silero_vad_lite_*.jit` 放在 `silero_vad/data/` → 必 collect |
| 冻结 exe 启动时 asyncio RuntimeError | Windows proactor loop 在 frozen 下偶发 | main.py 顶层显式 `asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())`（P2-2 已做，确认残留） |
| `No module named 'tzdata'` | PyInstaller 对 pypi `tzdata` 识别不完整 | `hiddenimports=["tzdata"]` + `collect_data_files("tzdata")` |
| `structlog` missing processors | structlog 有 lazy import | `collect_submodules("structlog")` |

---

## 4. 文件改动清单

### 新增
- `backend/deskpet-backend.spec`（~60 行）
- `scripts/build_backend.ps1`（~30 行）
- `scripts/smoke_frozen_backend.py`（~50 行）

### 修改
- `backend/providers/silero_vad.py` — torch.hub → PyPI `silero_vad.load_silero_vad`
- `backend/pyproject.toml` — 加 `silero-vad>=5.1.2` 依赖
- `backend/pyproject.toml` — 加 `pyinstaller>=6.11` + `pyinstaller-hooks-contrib>=2024.8` 到 dev extras
- `docs/PACKAGING.md` — §7 "PyInstaller 冻结产物"
- `.gitignore` — `backend/dist/`, `backend/build/`

### 不改
- `tauri-app/**` — 完全不碰，P3-S5 才 wire bundle

---

## 5. 测试计划

### 5.1 Python 单测（回归）

- `pytest backend/tests/` 仍 298/4 passed（silero-vad 切 pypi 后 vad 相关测试应无变化，权重一致）
- 新增 `backend/tests/test_silero_vad.py` 如果不存在则加一条确认 `load_silero_vad()` 能返回可调用的模型

### 5.2 冻结产物 smoke（本 slice 验收的核心）

```bash
# 1. Build
powershell scripts/build_backend.ps1
# 期望：backend/dist/deskpet-backend/deskpet-backend.exe 产出，无 error

# 2. Smoke
python scripts/smoke_frozen_backend.py
# 期望：
# - "SHARED_SECRET=..." 在 < 30s 内打印
# - /health 返回 status=ok
# - 脚本退出 0
```

### 5.3 真机 Dev 集成

在跑通 smoke 之后，临时改 `tauri-app/src-tauri/src/backend_launch.rs::resolve`
让优先级 1 指向 `backend/dist/deskpet-backend/deskpet-backend.exe`，然后
`npm run tauri:dev` —— 对话往返 PASS。本步不 commit（是 P3-S5 的活），
但做一次手动确认 supervisor 路径 + bundle 分支能 spawn。

### 5.4 体积基线

`scripts/build_backend.ps1` 末尾的 `Write-Host "frozen size: ..."`
记录一个 baseline（预期 ~800MB—1.2GB，torch+cuda runtime 是大头）。
高于 1.5GB 需要排查 excludes。

### 5.5 冷启动时长

smoke 脚本报 `boot time: Xs`。目标 < 30s；若 > 60s 本 slice 不阻塞
但标 FOLLOWUP 给 P3-S8（lazy import 优化）。

---

## 6. 风险

| 风险 | 缓解 |
|---|---|
| silero-vad PyPI 版本语义和 hub 不一致 | v5.1.2 vs repo master `v5` — 跑 vad 测试前后 prob 对比，差 < 1e-3 视为等价 |
| PyInstaller 漏 ctranslate2 plugin DLL | spec 明确 `collect_submodules` + smoke 脚本真走 `WhisperModel(device="cuda")` 初始化（会 dlopen plugin） |
| 体积超标（> 1.5GB） | `excludes` 显式排 `matplotlib/tkinter/IPython`；必要时 `--strip` torch libs（但可能破坏 CUDA 链接） |
| 冷启动 > 60s | 本 slice 先记录不优化；P3-S8 再做 lazy import |
| 冻结环境下 `structlog` 输出 encoding 问题 | P2-2 已在 main.py 顶层 `PYTHONIOENCODING=utf-8`；spec 里环境一致 |
| CI 跑不起来 PyInstaller（无 GPU runner） | 本 slice 只要本机 smoke green；CI 集成留 P3-S10 |

---

## 7. 验收标准

- [ ] `pytest backend/tests/` 全绿（298+ passed，silero-vad 改用 PyPI 后）
- [ ] `powershell scripts/build_backend.ps1` 无 error，产出 `backend/dist/deskpet-backend/deskpet-backend.exe`
- [ ] `python scripts/smoke_frozen_backend.py` exit 0（SHARED_SECRET + `/health` ok）
- [ ] 冷启动时长写入 HANDOFF（基线数字）
- [ ] 产物体积写入 HANDOFF（基线数字）
- [ ] 真机 Dev 临时重定向到 frozen exe 跑一次对话 PASS（手测，不 commit）
- [ ] HANDOFF `docs/superpowers/handoffs/p3-s4-pyinstaller-backend.md` 就位
- [ ] `STATE.md` 更新

---

## 8. 提交策略

5 个原子 commit：
1. `docs(plan): P3-S4 PyInstaller backend freeze slice plan`（本文）
2. `refactor(backend): silero_vad uses PyPI silero-vad package (drop torch.hub)`
3. `build(backend): PyInstaller spec + build_backend.ps1`
4. `test(scripts): smoke_frozen_backend.py`
5. `docs(P3-S4): PACKAGING §7 frozen layout + HANDOFF + STATE`

Merge 用 `--no-ff`，push 到 origin。
