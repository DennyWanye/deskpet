# DeskPet

本地部署的桌面语音宠物：Live2D 桌宠 + 全本地语音交互管线（VAD → ASR → LLM → TTS）。

**技术栈：** Tauri 2 + React + PixiJS v7 + pixi-live2d-display（前端）· Python FastAPI + faster-whisper + Silero VAD + edge-tts + Ollama（后端）。

---

## 目录结构

```
deskpet/
├── backend/          # FastAPI 后端 (ASR/VAD/TTS/LLM/pipeline)
│   ├── providers/    # 各引擎 provider 实现
│   ├── pipeline/     # 语音管线编排
│   ├── assets/       # ⚠️ 模型权重 (gitignored，见下方"模型获取")
│   └── tests/
├── tauri-app/        # Tauri + React 桌面前端
│   └── src-tauri/    # Rust 原生层 (窗口透明、麦克风权限)
├── docs/superpowers/plans/  # 设计文档 (OpenSpec plans)
├── config.toml       # 全局配置
└── plans/            # 历史规划文档 (docx)
```

---

## Quick Start

### 后端
```bash
cd backend
uv sync

# 本地开发建议开启 dev 模式（跳过 WebSocket 共享密钥校验）
export DESKPET_DEV_MODE=1   # Windows: set DESKPET_DEV_MODE=1
uv run python main.py
# 默认监听 127.0.0.1:8100
```

依赖 Ollama 本地服务（默认 `http://localhost:11434`，模型 `gemma4:e4b`），可在 `config.toml` 中修改。

> **生产部署：** 不要设 `DESKPET_DEV_MODE`。启动时会打印 `SHARED_SECRET=...`，客户端 WebSocket 连接需带上 `x-shared-secret` header 或 `?secret=` 查询参数。

### 前端
```bash
cd tauri-app
pnpm install
pnpm tauri dev
```

---

## 模型获取 ⚠️

`backend/assets/` 下的模型权重**不进 git**（单目录 GB 级）。新机器 clone 后需手动补齐，详见：

👉 **[Sprint 2 Plan — 模型资产获取](./docs/superpowers/plans/2026-04-13-desktop-pet-sprint2-voice-pipeline.md#模型资产获取)**

简要：
- **Silero VAD** — 首次运行自动从 torch.hub 下载（~2MB）
- **faster-whisper-large-v3-turbo** — HuggingFace 或 ModelScope 拉取到 `backend/assets/faster-whisper-large-v3-turbo/`（~2.7GB）
- **TTS** — 当前默认 `edge-tts`（在线，无需权重）；如需切回本地 CosyVoice 2 参见 plan

---

## 开发说明

- Windows 下 Tauri dev 结束后可能残留 `deskpet.exe` / Vite 进程，重启前 `taskkill /F /IM deskpet.exe`
- 提交前跑后端测试：`cd backend && uv run pytest`
- Live2D 运行时说明与性能调优见 `docs/superpowers/plans/` 下历次 plan
