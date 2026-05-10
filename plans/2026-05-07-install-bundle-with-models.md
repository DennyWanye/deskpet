# Install Bundle with Bundled Models — 工作计划

**日期**: 2026-05-07
**状态**: 进行中（已开始 step 1）

## 目标

打一个 **开箱即用** 的 NSIS 安装包，所有模型权重内置，用户下载安装即可使用，无需任何额外下载。

## 现状盘点（compact 前调查得到）

| 项 | 大小 | 当前在哪 |
|----|------|---------|
| Backend dist (Python + 依赖 + silero VAD) | 1.5 GB | `backend/dist/deskpet-backend/` |
| 现有 NSIS 安装包 | **682 MB** | `tauri-app/src-tauri/target/release/bundle/nsis/DeskPet_0.5.0-phase3-rc1_x64-setup.exe` |
| BGE-M3 (向量, INT8) | **2.3 GB** | `~/AppData/Local/deskpet/models/bge-m3-int8/` (用户机本地) |
| faster-whisper-large-v3-turbo (ASR) | ~2.7 GB | **未下载**（需从 HuggingFace / ModelScope 拉） |
| Silero VAD | ~2 MB | 已自带在 `backend/dist/deskpet-backend/_internal/silero_vad/` |
| edge-tts | 0 (在线) | 网络服务，无需打包 |

**目标安装包大小**: ~ **4 GB**（682MB + 2.3GB BGE-M3 + 2.7GB faster-whisper - 重叠部分）

## 任务清单

- [x] **Step 1**: 复制 BGE-M3 到 `backend/assets/bge-m3-int8/` (2.3GB) — **已完成 robocopy**
- [ ] **Step 2**: 下载 faster-whisper-large-v3-turbo 到 `backend/assets/faster-whisper-large-v3-turbo/` (~2.7GB)
  - 用 `huggingface-hub` Python 库或 `huggingface-cli download Systran/faster-whisper-large-v3-turbo`
  - 或者 ModelScope 镜像（国内更快）
- [ ] **Step 3**: 改 `backend/deskpet-backend.spec` — 加 `datas` 把两个模型目录打进 PyInstaller dist
  - 形式：`datas=[('assets/bge-m3-int8', 'assets/bge-m3-int8'), ('assets/faster-whisper-large-v3-turbo', 'assets/faster-whisper-large-v3-turbo')]`
  - 注意 spec 里可能已有部分 datas，要追加而非替换
- [ ] **Step 4**: 改 backend 启动逻辑 — 模型路径解析优先级
  - 当前：硬编码到 `~/AppData/Local/deskpet/models/`
  - 改后：先查 install dir (`<bundle_root>/assets/`)，找不到再查 user dir
  - 涉及文件：
    - `backend/deskpet/memory/embedder.py` - BGE-M3 加载（spawn 子进程时传 model-path）
    - `backend/main.py` - embedder 初始化时 model_path 参数
    - `backend/providers/faster_whisper_asr.py` - ASR model dir
    - `backend/paths.py` - `model_root()` / `resolve_model_dir()` 工具函数
- [ ] **Step 5**: 重 build PyInstaller → `cd backend && pyinstaller deskpet-backend.spec`
- [ ] **Step 6**: 重 build Tauri NSIS → `cd tauri-app && npm run tauri build` (或 `pnpm tauri build`)
- [ ] **Step 7**: 验证 — 卸载现有 deskpet → 装新 .exe → 完全干净的环境（删 `~/AppData/Local/deskpet/`）测启动 → 看 BGE-M3 + faster-whisper 都能从 install dir 加载

## 关键决策（已和用户确认）

- **方案 A 完全离线包**: 用户明确要求"用户下载了，安装了，就可以用了"——选 A，模型都内嵌
- **不接受首启网络下载** —— 所以方案 B/C 都被排除
- **预期最终安装包 ~4 GB** —— 用户接受这个体积

## 当前 git 状态

最近 commits（已全部 push）：
```
c6ed551  docs(p4-s20-D): README 记录严格 E2E + 3 bug 修复
39119c9  test(p4-s20-D): 严格 E2E + 3 bug 修复
8b58d49  chore: gitignore pytest tmp + screenshot + llm_runtime
794e0c1  feat(p4-s20-D): 自动总结 + 归档老对话 (schema v10)
```

backend tests: **786 passed / 10 skipped / 0 failed**

## Compact 后立即恢复的命令

```bash
# 1. 验证 BGE-M3 已复制 (上次 robocopy 完成: 2343 MB)
ls -la G:/projects/deskpet/backend/assets/bge-m3-int8/

# 2. 启动 faster-whisper 下载 (Step 2)
# 方案 A: huggingface-cli
huggingface-cli download Systran/faster-whisper-large-v3-turbo \
  --local-dir G:/projects/deskpet/backend/assets/faster-whisper-large-v3-turbo

# 方案 B: ModelScope (国内快)
modelscope download --model 'iic/SenseVoiceSmall' \
  --local_dir G:/projects/deskpet/backend/assets/faster-whisper-large-v3-turbo

# 3. 看 PyInstaller spec
cat G:/projects/deskpet/backend/deskpet-backend.spec

# 4. 看路径解析工具
cat G:/projects/deskpet/backend/paths.py | head -50
```

## 风险 / 已知问题

1. **PyInstaller dist 体积** — 4GB exe 在 NSIS 里可能压成 1.5-2GB（NSIS 内置 LZMA 压缩）。仍然大但可分发。
2. **首次解压慢** — 用户安装时 NSIS 解压 4GB 到 Program Files 需要几分钟。考虑加进度条提示。
3. **磁盘空间检查** — NSIS 应在安装前检查目标盘 ≥ 6GB（解压临时空间 + 最终 4GB）。
4. **路径解析 fallback 顺序很关键** — install dir 优先，user dir fallback。如果用户在 user dir 有自定义模型（比如不同量化版本），install dir 不应覆盖；要给用户控制。
5. **CUDA 依赖** — 用户机器没 NVIDIA GPU 时 BGE-M3 + faster-whisper 都会 fallback 到 CPU，速度慢但能用。这个已经在代码里 handled。
