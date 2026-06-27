# P3-S1 模型目录收拢 + Config 分离 — Slice Plan

**Date**: 2026-04-21
**Parent sprint**: Phase 3（`2026-04-21-phase3-roadmap.md`）
**Target branch**: `worktree-p3-s1-model-dir-config`
**Estimated**: 2 个工作日
**Status**: DRAFT → implementing

---

## 1. 问题陈述

Phase 3 需要把 backend 打包成 PyInstaller exe，但当前**模型路径是硬编码
在 Python 代码里**，打包后会抓瞎：

```python
# backend/main.py:202
local_dir=str(Path(__file__).parent / "assets" / "faster-whisper-large-v3-turbo"),

# backend/main.py:212
_cosy_dir = Path(__file__).parent / config.tts.model_dir.lstrip("./")
```

PyInstaller 冻结后：
- `__file__` 变成 `backend/main.py` 的临时解压位置（`onedir` 下仍可用但语义脆弱）
- `assets/` 目录必须显式在 spec 的 `datas=` 里列出，运行时路径和源码布局不一样
- `sys._MEIPASS` 是 onefile 的临时目录；onedir 下 `sys._MEIPASS = sys.executable 的目录`

**更根本的问题**：当前 **硬编码目录 + config 路径混用** 让"开发模式 vs
打包模式"路径解析不统一。P3-S3 要让 Rust supervisor 自己找 backend exe，
P3-S4/S5/S6 要把模型塞 bundle，这些 slice 都依赖本 slice 先把路径解析
集中到 **一个入口函数**。

## 2. 目标

### 2.1 硬性目标
1. **所有模型路径走 config**，不再有 `Path(__file__).parent / "assets"` 之类的硬编码
2. **单一路径解析函数** `resolve_model_dir(subdir: str) -> Path`，处理三种场景：
   - 开发模式（源码运行）：相对 `backend/` 目录
   - PyInstaller `--onedir`：相对 `sys.executable` 目录
   - 显式 env override：`DESKPET_MODEL_ROOT`（便于 CI / 测试）
3. **目录改名** `backend/assets/` → `backend/models/`（语义更准；`assets` 保留给前端含义）
4. **配置项命名统一**：所有模型都有一个 `model_dir` 或 `model_path` 字段

### 2.2 非目标（本 slice 刻意不做）
- ❌ 真正集成 PyInstaller（那是 P3-S4）
- ❌ CUDA 检测（P3-S2）
- ❌ Tauri bundle 配置改动（P3-S5）
- ❌ 模型文件本身的分发策略（P3-S6）
- ❌ 用户数据目录迁移到 `%AppData%`（P3-S7）

本 slice 只做 **代码层面的路径解析重构**，让后续 slice 有稳定地基。

## 3. 设计

### 3.1 新模块：`backend/paths.py`

```python
"""Model / asset directory resolution.

P3-S1: single source of truth for where bundled model files live, so
PyInstaller onedir packaging (P3-S4) and dev-mode execution both go
through one place.

Priority order:
  1. DESKPET_MODEL_ROOT env var (escape hatch for CI / debug)
  2. sys._MEIPASS if set (PyInstaller runtime marker)
  3. sys.executable directory if we look like a frozen build
  4. backend/ directory relative to this file (dev mode)
"""
from __future__ import annotations
import os
import sys
from pathlib import Path


def model_root() -> Path:
    """Root directory containing all model subfolders (whisper/silero/cosy)."""
    override = os.environ.get("DESKPET_MODEL_ROOT")
    if override:
        return Path(override)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "models"
    # dev-mode fallback: backend/models/ relative to this file
    return Path(__file__).resolve().parent / "models"


def resolve_model_dir(subdir: str) -> Path:
    """Return absolute path for a named model subfolder.

    ``subdir`` examples: ``"faster-whisper-large-v3-turbo"``, ``"cosyvoice2"``,
    ``"silero_vad"``. Does NOT verify the directory exists — callers decide
    whether missing models should be fatal or fallback.
    """
    return (model_root() / subdir).resolve()
```

### 3.2 Config 结构

**仅改两处**（向后兼容，老 config.toml 继续跑）：

```python
# backend/config.py

@dataclass
class ASRConfig:
    provider: str = "faster-whisper"
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    hotwords: list[str] = field(default_factory=list)
    # P3-S1: relative-to-model_root subfolder. Empty/None → use model_name
    # verbatim (HuggingFace cache lookup).
    model_dir: str = "faster-whisper-large-v3-turbo"


@dataclass
class TTSConfig:
    provider: str = "edge-tts"
    voice: str = "zh-CN-XiaoyiNeural"
    # P3-S1: relative subfolder under model_root. Was "./assets/cosyvoice2"
    # (relative-to-CWD, fragile under PyInstaller). Kept backwards-compat
    # by detecting "./" / "assets/" prefix in the loader.
    model_dir: str = "cosyvoice2"
```

**VADConfig** 不需要改 —— silero-vad 是 pip 包内置 jit 文件，不走 model_dir。

### 3.3 `backend/main.py` 改动

**删除** 所有 `Path(__file__).parent / "assets" / ...` 用法，替换为：

```python
from paths import resolve_model_dir

asr = FasterWhisperASR(
    model=config.asr.model,
    device=_asr_device,
    compute_type=_asr_compute,
    local_dir=str(resolve_model_dir(config.asr.model_dir)),
    hotwords=config.asr.hotwords,
)

# TTS 分支
if config.tts.provider == "cosyvoice2":
    tts = CosyVoice2Provider(
        model_dir=str(resolve_model_dir(config.tts.model_dir)),
        fallback_voice=config.tts.voice,
    )
```

### 3.4 `config.toml` 改动

```toml
[asr]
provider = "faster-whisper"
model = "large-v3-turbo"
model_dir = "faster-whisper-large-v3-turbo"  # relative to model_root()
device = "cuda"
compute_type = "float16"
hotwords = [...]

[tts]
provider = "edge-tts"
voice = "zh-CN-XiaoyiNeural"
model_dir = "cosyvoice2"  # relative to model_root() (was "./assets/cosyvoice2")
```

### 3.5 目录重命名

```
backend/assets/faster-whisper-large-v3-turbo/  →  backend/models/faster-whisper-large-v3-turbo/
backend/assets/cosyvoice2/                     →  backend/models/cosyvoice2/
```

`.gitignore` 从 `backend/assets/` 改成 `backend/models/`。

**兼容性**：本地开发者若已有 `backend/assets/`，手工移动一次即可（文档说明）。
CI 没动静，因为 CI 从 release asset 拉模型到 `backend/models/`（P3-S6 时再落地）。

### 3.6 向后兼容策略

```python
# backend/config.py 里加一段 load-time migration：
if config.tts.model_dir.startswith(("./assets/", "assets/", "./")):
    logger.warning(
        "config [tts].model_dir uses legacy relative path %r; "
        "normalizing to bare subfolder (P3-S1)",
        config.tts.model_dir,
    )
    # 剥掉前缀：./assets/cosyvoice2 → cosyvoice2
    config.tts.model_dir = config.tts.model_dir.split("/")[-1]
```

`[asr].model_dir` 是本 slice 新加的，没有历史值要迁移。

## 4. 文件改动清单

### 新增

| 路径 | 用途 |
|---|---|
| `backend/paths.py` | `model_root()` + `resolve_model_dir()` |
| `backend/tests/test_paths.py` | 4 个路径解析场景 |
| `docs/PACKAGING.md` | PyInstaller 打包 / 路径约定文档（骨架，后续 slice 补肉） |

### 修改

| 路径 | 变更 |
|---|---|
| `backend/config.py` | `ASRConfig.model_dir` 新字段；`TTSConfig.model_dir` 默认值改 + 兼容迁移；loader warn |
| `backend/main.py` | 两处 `Path(__file__).parent / "assets" / ...` → `resolve_model_dir(...)` |
| `config.toml` | `[asr].model_dir` 新增；`[tts].model_dir = "cosyvoice2"` |
| `.gitignore` | `backend/assets/` → `backend/models/` |
| `backend/tests/test_config.py` | 如果测了老值需要更新 |
| `backend/tests/test_config_asr.py` | 加 `model_dir` 字段断言 |
| `README.md` / `docs/CLAUDE.md` | 如果提到 `backend/assets/` 要改 |

### 重命名（filesystem 操作，非 git track）

| 旧路径 | 新路径 |
|---|---|
| `backend/assets/faster-whisper-large-v3-turbo/` | `backend/models/faster-whisper-large-v3-turbo/` |
| `backend/assets/cosyvoice2/` | `backend/models/cosyvoice2/` |

**注**：这些目录都是 `.gitignored`，git 感知不到重命名；开发者只需手工 `mv`。
CI 也不受影响（CI 重新 fetch 到新路径）。

## 5. TDD 任务拆解

### Task 1 — `backend/paths.py` 基础实现

**Red** `test_paths.py::test_model_root_dev_mode`:
```python
def test_model_root_dev_mode(monkeypatch):
    monkeypatch.delenv("DESKPET_MODEL_ROOT", raising=False)
    # No _MEIPASS in normal pytest run
    root = paths.model_root()
    assert root.name == "models"
    assert root.parent.name == "backend"
```

**Red** `test_paths.py::test_model_root_env_override`:
```python
def test_model_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    assert paths.model_root() == tmp_path
```

**Red** `test_paths.py::test_model_root_meipass`:
```python
def test_model_root_meipass(monkeypatch, tmp_path):
    monkeypatch.delenv("DESKPET_MODEL_ROOT", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.model_root() == tmp_path / "models"
```

**Red** `test_paths.py::test_resolve_model_dir`:
```python
def test_resolve_model_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(tmp_path))
    assert paths.resolve_model_dir("foo") == (tmp_path / "foo").resolve()
```

**Green**: 写 `paths.py`（§3.1 代码）

### Task 2 — Config 字段扩展

**Red** `test_config_asr.py::test_asr_config_model_dir_default`:
```python
def test_asr_config_model_dir_default():
    cfg = ASRConfig()
    assert cfg.model_dir == "faster-whisper-large-v3-turbo"
```

**Red** `test_config.py::test_tts_config_model_dir_default`:
```python
def test_tts_config_model_dir_default():
    cfg = TTSConfig()
    assert cfg.model_dir == "cosyvoice2"
```

**Red** `test_config.py::test_legacy_tts_model_dir_normalized` (迁移行为):
```python
def test_legacy_tts_model_dir_normalized(tmp_path, caplog):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[tts]\nmodel_dir = "./assets/cosyvoice2"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = load_config(cfg_path)
    assert cfg.tts.model_dir == "cosyvoice2"
    assert any("legacy" in r.message.lower() for r in caplog.records)
```

**Green**: 改 `config.py`（§3.2 + §3.6）

### Task 3 — `main.py` 切换到 `resolve_model_dir`

**Red**: 手测（或集成测试 —— 起 backend，确认 ASR 加载成功）

**Green**: §3.3 的两处替换

### Task 4 — 目录重命名 + .gitignore + config.toml

**Red**: 无单测（文件系统操作）。通过 `scripts/check_no_hardcoded_assets.py` 兜底：
```python
"""CI sanity check: no backend code should hardcode 'backend/assets/' paths."""
import pathlib, re, sys
root = pathlib.Path(__file__).resolve().parents[1]
offenders = []
for p in (root / "backend").rglob("*.py"):
    if p.name.startswith("test_"): continue
    text = p.read_text(encoding="utf-8")
    for m in re.finditer(r'assets["\']|/ ?"assets"', text):
        offenders.append(f"{p}:{text[:m.start()].count(chr(10))+1}")
if offenders:
    print("hardcoded 'assets' path:", *offenders, sep="\n  ")
    sys.exit(1)
```

**Green**: `mv backend/assets backend/models` + edit .gitignore + edit config.toml

### Task 5 — 文档

- `docs/PACKAGING.md` 新建（骨架，5 段：打包概览 / 路径约定 / 开发模式 /
  PyInstaller 模式 / 故障排查占位）
- README.md 里"manual setup"段把 `backend/assets/` 改成 `backend/models/`

## 6. 验收

**硬性**：
1. `cd backend && pytest` 全绿（当前 267 → 274+ 含新增 7 个左右测试）
2. 本地起 backend：`python backend/main.py`，确认 `preloading models...`
   后 Whisper 实际加载（日志里 `loading faster-whisper model=G:\...\backend\models\faster-whisper-large-v3-turbo`）
3. 手测 1 次对话链路不退化
4. `scripts/check_no_hardcoded_assets.py` exit 0

**软性**：
- `ripgrep "assets"` 在 backend/ 里只剩注释 / 旧 README 提示 / test 描述，
  没有可执行路径
- 设置 `DESKPET_MODEL_ROOT=/tmp/fake` 启动 backend → 日志显示尝试从
  `/tmp/fake/faster-whisper-large-v3-turbo` 加载（并 fail，证明 env 生效）

## 7. 风险

| 风险 | 缓解 |
|---|---|
| 漏改某处 `Path(__file__).parent / "assets"` 导致打包后才暴露 | `scripts/check_no_hardcoded_assets.py` 进 pre-commit + CI |
| 用户本地 `backend/assets/` 没迁移，启动即 fail | main.py 在 model_dir 不存在时给明确错误 + 迁移提示 |
| `config.tts.model_dir` 老值 `"./assets/cosyvoice2"` 在旧安装 base 的 `config.toml` 里 | loader 做前缀剥离 + warn（§3.6）|
| silero-vad 不走 model_dir，打包时 torch.hub 缓存丢失 | **不在本 slice 范围**，留给 P3-S4 的 PyInstaller spec 处理 |

## 8. Commit 策略

按 task 拆，最后一个 commit 文档 + `.gitignore`：

1. `test(P3-S1): paths.model_root + resolve_model_dir 单测` (red)
2. `feat(P3-S1): backend/paths.py 模型路径解析单点` (green)
3. `test(P3-S1): ASRConfig/TTSConfig.model_dir + 迁移测试` (red)
4. `feat(P3-S1): config model_dir 字段 + 兼容迁移` (green)
5. `refactor(P3-S1): main.py 改用 resolve_model_dir` (green)
6. `chore(P3-S1): backend/assets → backend/models 重命名 + config.toml + .gitignore`
7. `chore(P3-S1): scripts/check_no_hardcoded_assets.py CI 守门`
8. `docs(P3-S1): PACKAGING.md 骨架 + handoff + STATE 更新`

## 9. 参考

- `2026-04-21-phase3-roadmap.md` —— 父 sprint 路线图
- `2026-04-21-p2-2-f1-whisper-short-audio.md` —— 前一个 slice，可作格式参考
- PyInstaller `sys._MEIPASS` 文档: https://pyinstaller.org/en/stable/runtime-information.html

---

**Plan status**: SIGNED-OFF for implementation（2026-04-21）
