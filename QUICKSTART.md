# QUICKSTART — 5 分钟跑起 DeskPet

最短路径让 DeskPet 在你机器上跑起来。第一次跑预计 15-30 分钟（含依赖下载 + 模型下载）。

---

## 你需要先有

| 依赖 | 版本 | 检查命令 |
|---|---|---|
| Python | 3.11+ | `python --version` |
| Node.js | 20+ | `node --version` |
| Rust + Cargo | stable (2021 edition) | `cargo --version` |
| Git | 任意现代版本 | `git --version` |
| NVIDIA GPU | ≥ 8GB VRAM（CUDA-only，目前不支持 AMD / Intel / CPU-only） | `nvidia-smi` |

**Windows 用户额外需要**：
- Visual Studio Build Tools 2022（C++ workload，编译 Rust 用）
- WebView2 Runtime（Win11 自带，Win10 需手装）

**LLM 选项任选其一**（至少一个）：
- 本地 [Ollama](https://ollama.ai/) — 0 成本，模型权重要自己拉
- 远程 OpenAI / Anthropic / 其他兼容 API 的 provider — 自带 API key

---

## Step 1: 克隆 + 装依赖

```bash
git clone https://github.com/DennyWanye/deskpet.git
cd deskpet

# 后端
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"

# PyTorch CUDA wheel（默认 PyPI 是 CPU-only）
pip install --index-url https://download.pytorch.org/whl/cu124 torch torchvision torchaudio

cd ..

# 前端
cd tauri-app
npm install
cd ..
```

---

## Step 2: 下载模型（首次必须）

DeskPet 需要本地 ASR（faster-whisper）+ embedding（BGE-M3）+ VAD（Silero）三个模型。

```bash
python scripts/setup_models.py
```

脚本会引导你下载这三个模型到 `%APPData%\deskpet\models\`（Win）或 `~/Library/Application Support/deskpet/models/`（Mac）。总计约 1.5GB。

---

## Step 3: 配置 LLM Provider

打开 `config.toml`：

```toml
[llm]
# 选一：本地 Ollama
model = "qwen2:7b"
base_url = "http://localhost:11434/v1"
api_key = ""

# 或：远程 OpenAI 兼容 provider（运行后在 Settings 面板配置更方便）
# model = "gpt-4o-mini"
# base_url = "https://api.openai.com/v1"
# api_key = ""  # ← 留空！打开 app 后在 Settings → LLM Providers 填，会进 OS keychain
```

**关键：`api_key` 留空**。app 启动后到 Settings 面板手填，会安全写入 OS keychain，不会进入 config.toml 文件。

---

## Step 4: 启动！

### 单条命令（推荐）

```bash
cd tauri-app
npm run tauri:dev
```

这会同时启动：
- Python 后端（端口 8100）
- Vite dev server（端口 5173）
- Tauri 桌宠窗口

首次启动 30~60 秒（加载模型）。窗口出来你就看到 Hiyori 桌宠了。

### 分开启动（debug 用）

```bash
# Terminal 1: backend
cd backend
.venv\Scripts\activate    # 或 source .venv/bin/activate
python -m uvicorn main:app --host 127.0.0.1 --port 8100

# Terminal 2: tauri
cd tauri-app
npm run tauri:dev
```

---

## Step 5: 试用

1. **打字聊天**：右上角对话框输入文字
2. **语音聊天**：点麦克风按钮（会请求权限）
3. **拖动桌宠**：长按拖到任意位置（按住右下角调整尺寸）
4. **调出设置**：右上角齿轮图标 → Settings 面板

---

## 常见首次启动问题

| 现象 | 解决 |
|---|---|
| `nvidia-smi: not found` | 装 NVIDIA 显卡驱动；DeskPet 当前只跑 CUDA |
| `Error loading torch_cuda_dll` | PyTorch CUDA wheel 没装好；按 Step 1 重装 |
| 桌宠窗口空白 / 黑 | Tauri WebView2 没装好（Win10）；从微软官方装一下 |
| 后端 100% CPU 但前端转圈 | 模型还在首次加载；等 30-60 秒 |
| `Address already in use: 8100` | 上次 dev 留的孤儿后端进程没杀干净。Windows: `taskkill /F /IM python.exe` |
| 麦克风没权限 | macOS 系统设置 → 隐私 → 麦克风 / Windows 隐私设置勾上 |

---

## 接下来

- 想理解架构？看 [ARCHITECTURE.md](./ARCHITECTURE.md)
- 想贡献代码？看 [CONTRIBUTING.md](./CONTRIBUTING.md)
- 想加新功能？开 [GitHub issue](https://github.com/DennyWanye/deskpet/issues)
- 想换 Live2D 模型？把你的 `.moc3` 放到 `tauri-app/public/assets/live2d/<你的名字>/`，
  然后改默认模型配置（详见 [`licenses/LIVE2D-HIYORI.md`](./licenses/LIVE2D-HIYORI.md) 最后一段）

---

*Last updated: 2026-05-27 for DeskPet `0.5.0-phase3-rc1`*
