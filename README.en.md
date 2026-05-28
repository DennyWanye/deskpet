# DeskPet

A local-first desktop AI companion: Live2D pet character + end-to-end voice
pipeline (VAD → ASR → LLM → TTS), running on your own machine.

> 中文版 README 见 [README.md](./README.md)

---

## What it is

- **Live2D desktop pet** — transparent, always-on-top, draggable; runs an
  animated character (Hiyori by default) that lip-syncs with TTS output.
- **Voice + text chat** — talk to it or type; auto-detects speech via
  Silero VAD, transcribes via faster-whisper, replies through your chosen
  LLM, speaks back through edge-tts.
- **Three-tier memory** — short-term buffer, episodic recall across
  sessions (BGE-M3 + sqlite-vec), and structured entity profile.
- **Tool calling + skills** — built-in office (PPT / Excel / Word), web
  fetch, OCR, image generation, browser automation, MCP integration.
- **Bring-your-own LLM** — works with local Ollama, OpenAI, Anthropic,
  Google Gemini, or any OpenAI-compatible relay.

---

## License

DeskPet uses **Business Source License 1.1 (BUSL-1.1)**.

- ✅ Free for personal, internal-company, research, and non-competing
  commercial use
- ❌ Cannot be offered as a hosted or embedded service that directly
  competes with the maintainer's paid versions
- ⏱ Auto-converts to **Apache License 2.0** on **2030-05-27**

See [LICENSE](./LICENSE) and [LICENSE.FAQ.md](./LICENSE.FAQ.md) (Chinese FAQ).

### Third-party assets (important)

This repo bundles two Live2D Inc. proprietary assets that are **NOT**
covered by DeskPet's BUSL-1.1 license:

| Component | License | Attribution |
|---|---|---|
| Live2D Cubism Core runtime | Live2D Proprietary EULA | [`licenses/LIVE2D-CUBISM.md`](./licenses/LIVE2D-CUBISM.md) |
| Hiyori sample model | Live2D Free Material License | [`licenses/LIVE2D-HIYORI.md`](./licenses/LIVE2D-HIYORI.md) |

Downstream commercial users above 10M JPY annual revenue may need a Live2D
Publication License. Read the two files above before commercial release.

---

## Quick start

```bash
# 1. Requirements: Python 3.11+, Node 20+, Rust stable, NVIDIA GPU 8GB+
git clone https://github.com/DennyWanye/deskpet.git
cd deskpet

# 2. Install backend
cd backend
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pip install --index-url https://download.pytorch.org/whl/cu124 torch torchvision torchaudio
cd ..

# 3. Install frontend
cd tauri-app && npm install && cd ..

# 4. Download required models (~1.5GB)
python scripts/setup_models.py

# 5. Launch
cd tauri-app && npm run tauri:dev
```

First launch takes 30-60s while models load. Then configure your LLM
provider in Settings → LLM Providers.

Detailed walkthrough: [QUICKSTART.md](./QUICKSTART.md)

---

## Tech stack

| Layer | Stack |
|---|---|
| Native shell | Tauri 2 + Rust |
| Frontend | React 19 + Vite 8 + PixiJS v7 + pixi-live2d-display + Zustand |
| Backend | Python 3.11 + FastAPI + uvicorn + asyncio |
| ML stack | PyTorch 2.6 (CUDA) + faster-whisper + silero-vad + BGE-M3 + edge-tts |
| Storage | sqlite + sqlite-vec (vector) + OS keychain (secrets) |
| LLM adapters | anthropic / openai / google-genai / ollama (+ MCP client) |

---

## Architecture

```
Tauri Rust shell  ──→  React webview  ──→  Python FastAPI backend
   (window/keychain)    (UI/Live2D)         (ASR/VAD/LLM/TTS/memory/skills)
```

Full diagram: [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## Hardware support

Currently **NVIDIA GPU (CUDA) only**. AMD / Intel / CPU-only paths are
long-term backlog. See [HARDWARE_COMPROMISES.md](./HARDWARE_COMPROMISES.md).

---

## Contributing

Issues, PRs, and discussions welcome. Both English and Chinese accepted.
See [CONTRIBUTING.md](./CONTRIBUTING.md) for the workflow, code style, and
license implications of contributing.

For security issues, please use [GitHub Security Advisories](https://github.com/DennyWanye/deskpet/security/advisories/new)
rather than public issues. See [SECURITY.md](./SECURITY.md).

---

## Links

- [Chinese README](./README.md) — full project docs (中文)
- [Quick start](./QUICKSTART.md) — 5-minute setup walkthrough
- [Architecture](./ARCHITECTURE.md) — module layout and data flow
- [Contributing](./CONTRIBUTING.md) — PR workflow and code style
- [Code of conduct](./CODE_OF_CONDUCT.md) — Contributor Covenant 2.1
- [Security policy](./SECURITY.md) — vulnerability disclosure
- [License FAQ](./LICENSE.FAQ.md) — BUSL-1.1 explained (Chinese)
- [Third-party licenses](./licenses/README.md) — dependency attribution

---

*Last updated: 2026-05-27*
