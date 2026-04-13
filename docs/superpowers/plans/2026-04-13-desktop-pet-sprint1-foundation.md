# Desktop Pet Sprint 1: Foundation + Live2D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the two-process skeleton (Tauri 2 + Python Backend), render a Live2D character in a transparent overlay window, and achieve text-based chat through dual WebSocket channels connected to Ollama.

**Architecture:** Tauri 2 (Rust + React/TypeScript) renders a transparent always-on-top overlay with PixiJS v8 + untitled-pixi-live2d-engine for Live2D. A Python FastAPI backend runs as a managed subprocess, exposing dual WebSocket endpoints (control JSON + audio binary). ServiceContext DI container holds all swappable engine providers.

**Tech Stack:** Tauri 2, React 18, TypeScript, PixiJS v8, untitled-pixi-live2d-engine, Python 3.11, FastAPI, uvicorn, Ollama (Gemma 4 or Qwen 3.5), SQLite

---

## File Structure

```
desktop-pet/
├── tauri-app/
│   ├── package.json                    # React + Tauri deps
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx                    # React entry
│   │   ├── App.tsx                     # Root component, routes overlay vs config
│   │   ├── components/
│   │   │   ├── Live2DCanvas.tsx        # PixiJS + Live2D rendering
│   │   │   └── ChatBubble.tsx          # Chat message display
│   │   ├── ws/
│   │   │   ├── ControlChannel.ts       # Control WebSocket client
│   │   │   └── AudioChannel.ts         # Audio WebSocket client (Sprint 2)
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts         # Shared WS connection hook
│   │   └── types/
│   │       └── messages.ts             # Shared message type definitions
│   ├── src-tauri/
│   │   ├── Cargo.toml
│   │   ├── tauri.conf.json
│   │   ├── capabilities/
│   │   │   └── default.json            # Tauri 2 permissions
│   │   ├── src/
│   │   │   ├── main.rs                 # Tauri entry
│   │   │   ├── lib.rs                  # Plugin registration
│   │   │   ├── process_manager.rs      # Python subprocess lifecycle
│   │   │   └── click_through.rs        # Window click-through toggle
│   │   └── icons/                      # App icons
│   └── public/
│       └── assets/
│           └── live2d/                 # Live2D model files (.model3.json, .moc3, textures)
├── backend/
│   ├── pyproject.toml                  # Python project config (uv/pip)
│   ├── main.py                         # FastAPI app + dual WebSocket endpoints
│   ├── context.py                      # ServiceContext DI container
│   ├── config.py                       # Config loading (config.toml)
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py                     # Protocol classes (LLMProvider, ASRProvider, TTSProvider)
│   │   └── ollama_llm.py              # Ollama LLM provider
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── store.py                    # SQLite memory store
│   │   └── migrations/
│   │       └── 001_initial.sql         # Initial schema
│   └── tests/
│       ├── __init__.py
│       ├── test_context.py             # ServiceContext tests
│       ├── test_providers.py           # Provider protocol tests
│       ├── test_websocket.py           # WebSocket endpoint tests
│       └── test_memory.py              # Memory store tests
├── config.toml                         # Unified configuration
└── tests/
    └── e2e/
        └── test_chat_flow.py           # End-to-end chat test
```

---

## Prerequisites

Before starting, ensure these are installed:
- **Node.js** >= 18 (for React/Vite)
- **Rust** (rustup, latest stable) + Tauri CLI
- **Python** >= 3.11 + uv (or pip)
- **Ollama** running locally with a model pulled (e.g., `ollama pull qwen2.5:14b` as placeholder until Gemma 4 is available)

---

## Task 1: Initialize Tauri 2 + React Project

**Files:**
- Create: `tauri-app/package.json`
- Create: `tauri-app/src-tauri/tauri.conf.json`
- Create: `tauri-app/src-tauri/Cargo.toml`
- Create: `tauri-app/src-tauri/src/main.rs`
- Create: `tauri-app/src-tauri/src/lib.rs`
- Create: `tauri-app/src/main.tsx`
- Create: `tauri-app/src/App.tsx`
- Create: `tauri-app/index.html`
- Create: `tauri-app/vite.config.ts`
- Create: `tauri-app/tsconfig.json`

- [ ] **Step 1: Create Tauri 2 project with React template**

```bash
cd G:/projects/deskpet
npm create tauri-app@latest tauri-app -- --template react-ts
```

Select options when prompted:
- Package manager: npm
- UI template: React
- UI flavor: TypeScript

- [ ] **Step 2: Install Tauri dependencies and verify build**

```bash
cd G:/projects/deskpet/tauri-app
npm install
```

- [ ] **Step 3: Verify the Tauri dev server starts**

```bash
cd G:/projects/deskpet/tauri-app
npm run tauri dev
```

Expected: A window appears with the default Tauri + React template. Close it after verifying.

- [ ] **Step 4: Initialize git repo and commit**

```bash
cd G:/projects/deskpet
git init
cat > .gitignore << 'GITIGNORE'
node_modules/
target/
dist/
__pycache__/
*.pyc
.venv/
*.egg-info/
build/
.DS_Store
Thumbs.db
*.log
crash_reports/
logs/
GITIGNORE

git add .
git commit -m "chore: initialize Tauri 2 + React + TypeScript project"
```

---

## Task 2: Configure Transparent Overlay Window

**Files:**
- Modify: `tauri-app/src-tauri/tauri.conf.json`
- Modify: `tauri-app/src-tauri/Cargo.toml`
- Create: `tauri-app/src-tauri/src/click_through.rs`
- Modify: `tauri-app/src-tauri/src/lib.rs`
- Modify: `tauri-app/src-tauri/src/main.rs`
- Create: `tauri-app/src-tauri/capabilities/default.json`

- [ ] **Step 1: Configure tauri.conf.json for transparent overlay**

Replace the contents of `tauri-app/src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://raw.githubusercontent.com/tauri-apps/tauri/dev/crates/tauri-config-schema/schema.json",
  "productName": "desktop-pet",
  "version": "0.1.0",
  "identifier": "com.deskpet.app",
  "build": {
    "beforeDevCommand": "npm run dev",
    "devUrl": "http://localhost:1420",
    "beforeBuildCommand": "npm run build",
    "frontendDist": "../dist"
  },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "label": "main",
        "title": "Desktop Pet",
        "width": 400,
        "height": 600,
        "x": 1400,
        "y": 600,
        "decorations": false,
        "transparent": true,
        "alwaysOnTop": true,
        "resizable": false,
        "skipTaskbar": true
      }
    ],
    "security": {
      "csp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico"
    ]
  }
}
```

- [ ] **Step 2: Add Tauri 2 window plugin to Cargo.toml**

In `tauri-app/src-tauri/Cargo.toml`, ensure these dependencies:

```toml
[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
tauri-plugin-shell = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

- [ ] **Step 3: Create click-through module**

Create `tauri-app/src-tauri/src/click_through.rs`:

```rust
use tauri::{command, AppHandle, Manager};

/// Toggle click-through mode on the main window.
/// When enabled, mouse events pass through to underlying windows.
#[command]
pub fn set_click_through(app: AppHandle, enabled: bool) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    window
        .set_ignore_cursor_events(enabled)
        .map_err(|e| e.to_string())
}
```

- [ ] **Step 4: Wire up lib.rs with the command**

Replace `tauri-app/src-tauri/src/lib.rs`:

```rust
mod click_through;
mod process_manager;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            click_through::set_click_through,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

Note: `process_manager` module is created in Task 5. For now, create a placeholder:

Create `tauri-app/src-tauri/src/process_manager.rs`:
```rust
// Process manager - implemented in Task 5
```

- [ ] **Step 5: Ensure main.rs calls lib::run()**

Verify `tauri-app/src-tauri/src/main.rs` contains:

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri_app_lib::run();
}
```

Note: The lib crate name comes from the `[lib]` section of Cargo.toml. If the crate is named differently (e.g., `tauri_app_lib`), match accordingly. Check `Cargo.toml` `[lib] name = "..."`.

- [ ] **Step 6: Make the React app transparent**

Replace `tauri-app/src/App.tsx`:

```tsx
import { useState } from "react";

function App() {
  const [message, setMessage] = useState("Hello, I'm your desktop pet!");

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        backgroundColor: "transparent",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "flex-end",
        padding: "20px",
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(255, 255, 255, 0.9)",
          borderRadius: "12px",
          padding: "12px 16px",
          maxWidth: "300px",
          fontSize: "14px",
          color: "#333",
          boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
        }}
      >
        {message}
      </div>
    </div>
  );
}

export default App;
```

Update `tauri-app/src/main.tsx`:
```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

// Transparent background for the HTML body
document.body.style.backgroundColor = "transparent";
document.documentElement.style.backgroundColor = "transparent";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

Also update `tauri-app/index.html` — ensure the body has no background:
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Desktop Pet</title>
    <style>
      html, body {
        margin: 0;
        padding: 0;
        background: transparent;
        overflow: hidden;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: Run and verify transparent overlay**

```bash
cd G:/projects/deskpet/tauri-app
npm run tauri dev
```

Expected: A frameless, transparent window appears in the bottom-right of the screen. Only the white chat bubble is visible. The desktop behind it shows through. If the window background is white/opaque instead of transparent, check WebView2 version and `transparent: true` in tauri.conf.json.

**Known issue on Windows:** Some GPU drivers cause a white flash on window creation. This is a known Tauri issue (#14515). It does not affect steady-state rendering.

- [ ] **Step 8: Commit**

```bash
cd G:/projects/deskpet
git add tauri-app/
git commit -m "feat: configure transparent always-on-top overlay window with click-through support"
```

---

## Task 3: Initialize Python Backend Skeleton

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/main.py`
- Create: `backend/config.py`
- Create: `backend/providers/__init__.py`
- Create: `backend/providers/base.py`
- Create: `backend/memory/__init__.py`
- Create: `backend/tests/__init__.py`
- Create: `config.toml`

- [ ] **Step 1: Create pyproject.toml**

Create `backend/pyproject.toml`:

```toml
[project]
name = "deskpet-backend"
version = "0.1.0"
description = "Desktop Pet AI Backend"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "websockets>=13.0",
    "tomli>=2.0",
    "structlog>=24.0",
    "httpx>=0.27.0",
    "ollama>=0.4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
]
```

- [ ] **Step 2: Create virtual environment and install deps**

```bash
cd G:/projects/deskpet/backend
python -m venv .venv
.venv/Scripts/activate && pip install -e ".[dev]"
```

- [ ] **Step 3: Create unified config.toml**

Create `config.toml` at project root:

```toml
schema_version = 1

[backend]
host = "127.0.0.1"
port = 8100
log_level = "INFO"

[llm]
provider = "ollama"
model = "qwen2.5:14b"
base_url = "http://localhost:11434"
temperature = 0.7
max_tokens = 2048

[asr]
provider = "faster-whisper"
model = "large-v3-turbo"
device = "cuda"
compute_type = "float16"

[tts]
provider = "cosyvoice2"
model_dir = "./assets/cosyvoice2"

[memory]
db_path = "./data/memory.db"
embedding_model = "bge-m3"

[live2d]
model_path = "./assets/live2d/default/model.model3.json"

[security]
shared_secret_length = 32
```

- [ ] **Step 4: Create config loader**

Create `backend/config.py`:

```python
from __future__ import annotations

import tomli
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class BackendConfig:
    host: str = "127.0.0.1"
    port: int = 8100
    log_level: str = "INFO"


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "qwen2.5:14b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    max_tokens: int = 2048


@dataclass
class ASRConfig:
    provider: str = "faster-whisper"
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"


@dataclass
class TTSConfig:
    provider: str = "cosyvoice2"
    model_dir: str = "./assets/cosyvoice2"


@dataclass
class MemoryConfig:
    db_path: str = "./data/memory.db"
    embedding_model: str = "bge-m3"


@dataclass
class AppConfig:
    backend: BackendConfig = field(default_factory=BackendConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


def load_config(path: str | Path = "config.toml") -> AppConfig:
    """Load configuration from TOML file, falling back to defaults."""
    path = Path(path)
    if not path.exists():
        return AppConfig()

    with open(path, "rb") as f:
        raw = tomli.load(f)

    config = AppConfig()
    if "backend" in raw:
        config.backend = BackendConfig(**raw["backend"])
    if "llm" in raw:
        config.llm = LLMConfig(**raw["llm"])
    if "asr" in raw:
        config.asr = ASRConfig(**raw["asr"])
    if "tts" in raw:
        config.tts = TTSConfig(**raw["tts"])
    if "memory" in raw:
        config.memory = MemoryConfig(**raw["memory"])
    return config
```

- [ ] **Step 5: Create provider protocol classes**

Create `backend/providers/__init__.py`:
```python
```

Create `backend/providers/base.py`:

```python
from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM chat providers."""

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens."""
        ...

    async def health_check(self) -> bool:
        """Check if the provider is healthy."""
        ...


@runtime_checkable
class ASRProvider(Protocol):
    """Protocol for speech recognition providers."""

    async def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe audio bytes to text."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Protocol for text-to-speech providers."""

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes (WAV format)."""
        ...

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream synthesized audio chunks."""
        ...
```

- [ ] **Step 6: Create backend/main.py skeleton (no WebSocket yet)**

Create `backend/main.py`:

```python
from __future__ import annotations

import secrets
import structlog
import uvicorn
from fastapi import FastAPI
from pathlib import Path

from config import load_config

logger = structlog.get_logger()

# Load config relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
config = load_config(PROJECT_ROOT / "config.toml")

# Generate shared secret for this session
SHARED_SECRET = secrets.token_hex(config.backend.host and 16 or 16)

app = FastAPI(title="Desktop Pet Backend", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "secret_hint": SHARED_SECRET[:4] + "..."}


def main():
    logger.info(
        "starting backend",
        host=config.backend.host,
        port=config.backend.port,
        secret_hint=SHARED_SECRET[:4],
    )
    uvicorn.run(
        app,
        host=config.backend.host,
        port=config.backend.port,
        log_level=config.backend.log_level.lower(),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the backend and verify health endpoint**

```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python main.py
```

In another terminal:
```bash
curl http://127.0.0.1:8100/health
```

Expected: `{"status":"ok","secret_hint":"xxxx..."}`

- [ ] **Step 8: Commit**

```bash
cd G:/projects/deskpet
git add backend/ config.toml
git commit -m "feat: initialize Python backend with FastAPI, config loader, and provider protocols"
```

---

## Task 4: Implement ServiceContext DI Container

**Files:**
- Create: `backend/context.py`
- Create: `backend/tests/test_context.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Write failing test for ServiceContext**

Create `backend/tests/__init__.py`:
```python
```

Create `backend/tests/test_context.py`:

```python
import copy
import pytest
from context import ServiceContext
from providers.base import LLMProvider


class FakeLLM:
    """Fake LLM provider for testing."""

    def __init__(self, name: str = "fake"):
        self.name = name

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        yield "hello"

    async def health_check(self) -> bool:
        return True


def test_service_context_creation():
    ctx = ServiceContext()
    assert ctx.llm_engine is None
    assert ctx.asr_engine is None
    assert ctx.tts_engine is None


def test_service_context_register_and_get():
    ctx = ServiceContext()
    fake_llm = FakeLLM("test")
    ctx.register("llm_engine", fake_llm)
    assert ctx.llm_engine is fake_llm
    assert ctx.llm_engine.name == "test"


def test_service_context_deep_copy_isolation():
    ctx = ServiceContext()
    fake_llm = FakeLLM("original")
    ctx.register("llm_engine", fake_llm)

    ctx_copy = ctx.create_session()
    ctx_copy.llm_engine.name = "modified"

    assert ctx.llm_engine.name == "original"
    assert ctx_copy.llm_engine.name == "modified"


def test_service_context_register_unknown_raises():
    ctx = ServiceContext()
    with pytest.raises(ValueError, match="Unknown service"):
        ctx.register("unknown_engine", object())
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python -m pytest tests/test_context.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'context'`

- [ ] **Step 3: Implement ServiceContext**

Create `backend/context.py`:

```python
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from providers.base import LLMProvider, ASRProvider, TTSProvider


# List of valid service slot names
_VALID_SERVICES = frozenset({
    "llm_engine",
    "asr_engine",
    "tts_engine",
    "agent_engine",
    "memory_store",
    "tool_router",
})


@dataclass
class ServiceContext:
    """
    Dependency injection container for all swappable engine providers.
    Borrowed from Open-LLM-VTuber's architecture pattern.

    Usage:
        ctx = ServiceContext()
        ctx.register("llm_engine", OllamaLLM(...))
        ctx.register("tts_engine", CosyVoice2(...))

        # Per-session isolation via deep copy
        session_ctx = ctx.create_session()
    """

    llm_engine: Any | None = None
    asr_engine: Any | None = None
    tts_engine: Any | None = None
    agent_engine: Any | None = None
    memory_store: Any | None = None
    tool_router: Any | None = None

    def register(self, name: str, provider: Any) -> None:
        """Register a provider into a named service slot."""
        if name not in _VALID_SERVICES:
            raise ValueError(
                f"Unknown service '{name}'. Valid: {sorted(_VALID_SERVICES)}"
            )
        setattr(self, name, provider)

    def create_session(self) -> ServiceContext:
        """Create an isolated deep copy for a single session."""
        return copy.deepcopy(self)

    def get(self, name: str) -> Any | None:
        """Get a registered provider by name."""
        if name not in _VALID_SERVICES:
            raise ValueError(
                f"Unknown service '{name}'. Valid: {sorted(_VALID_SERVICES)}"
            )
        return getattr(self, name, None)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python -m pytest tests/test_context.py -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd G:/projects/deskpet
git add backend/context.py backend/tests/
git commit -m "feat: implement ServiceContext DI container with session isolation"
```

---

## Task 5: Implement Dual WebSocket Server

**Files:**
- Modify: `backend/main.py`
- Create: `backend/tests/test_websocket.py`

- [ ] **Step 1: Write failing test for WebSocket endpoints**

Create `backend/tests/test_websocket.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi.testclient import TestClient

from main import app, SHARED_SECRET


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_control_ws_rejects_without_secret():
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/control") as ws:
            data = ws.receive_json()
            # Should be rejected


def test_control_ws_accepts_with_secret():
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/control", headers={"X-Shared-Secret": SHARED_SECRET}
    ) as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"


def test_control_ws_echo_chat():
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/control", headers={"X-Shared-Secret": SHARED_SECRET}
    ) as ws:
        ws.send_json({
            "type": "chat",
            "payload": {"text": "hello"}
        })
        data = ws.receive_json()
        assert data["type"] == "chat_response"
        assert "text" in data["payload"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python -m pytest tests/test_websocket.py -v
```

Expected: FAIL — WebSocket endpoints don't exist yet

- [ ] **Step 3: Implement dual WebSocket endpoints in main.py**

Replace `backend/main.py`:

```python
from __future__ import annotations

import asyncio
import json
import secrets
import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from pathlib import Path

from config import load_config
from context import ServiceContext

logger = structlog.get_logger()

# Load config
PROJECT_ROOT = Path(__file__).parent.parent
config = load_config(PROJECT_ROOT / "config.toml")

# Generate shared secret for this session
SHARED_SECRET = secrets.token_hex(16)

# Global service context (providers registered at startup)
service_context = ServiceContext()

app = FastAPI(title="Desktop Pet Backend", version="0.1.0")


def _validate_secret(ws: WebSocket) -> bool:
    """Validate the shared secret from WebSocket headers."""
    secret = ws.headers.get("x-shared-secret", "")
    return secrets.compare_digest(secret, SHARED_SECRET)


@app.get("/health")
async def health():
    return {"status": "ok", "secret_hint": SHARED_SECRET[:4] + "..."}


@app.websocket("/ws/control")
async def control_channel(ws: WebSocket):
    """Control channel: JSON messages for chat, emotions, state, tool calls."""
    await ws.accept()

    if not _validate_secret(ws):
        await ws.close(code=4001, reason="invalid secret")
        return

    logger.info("control channel connected")
    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "chat":
                text = raw.get("payload", {}).get("text", "")
                logger.info("chat message received", text=text[:50])

                # For now, echo back. Task 7 will connect Ollama.
                response_text = f"[echo] {text}"

                llm = service_context.llm_engine
                if llm:
                    # Stream from LLM provider
                    response_text = ""
                    async for token in llm.chat_stream(
                        [{"role": "user", "content": text}]
                    ):
                        response_text += token

                await ws.send_json({
                    "type": "chat_response",
                    "payload": {"text": response_text},
                })

            elif msg_type == "interrupt":
                logger.info("interrupt received")
                await ws.send_json({"type": "interrupt_ack"})

            else:
                await ws.send_json({
                    "type": "error",
                    "payload": {"message": f"unknown type: {msg_type}"},
                })

    except WebSocketDisconnect:
        logger.info("control channel disconnected")


@app.websocket("/ws/audio")
async def audio_channel(ws: WebSocket):
    """Audio channel: binary stream for recording upload and TTS playback."""
    await ws.accept()

    if not _validate_secret(ws):
        await ws.close(code=4001, reason="invalid secret")
        return

    logger.info("audio channel connected")
    try:
        while True:
            # Receive binary audio data
            data = await ws.receive_bytes()
            logger.info("audio received", size=len(data))

            # Sprint 2: VAD + ASR processing
            # For now, acknowledge receipt
            await ws.send_json({
                "type": "audio_ack",
                "payload": {"received_bytes": len(data)},
            })

    except WebSocketDisconnect:
        logger.info("audio channel disconnected")


def main():
    logger.info(
        "starting backend",
        host=config.backend.host,
        port=config.backend.port,
        secret_hint=SHARED_SECRET[:4],
    )
    print(f"SHARED_SECRET={SHARED_SECRET}")  # Tauri process manager reads this
    uvicorn.run(
        app,
        host=config.backend.host,
        port=config.backend.port,
        log_level=config.backend.log_level.lower(),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python -m pytest tests/test_websocket.py -v
```

Expected: 4 tests PASS (health, reject without secret, accept with secret, echo chat)

- [ ] **Step 5: Commit**

```bash
cd G:/projects/deskpet
git add backend/main.py backend/tests/test_websocket.py
git commit -m "feat: implement dual WebSocket server with shared secret auth"
```

---

## Task 6: Implement Tauri Process Manager

**Files:**
- Modify: `tauri-app/src-tauri/src/process_manager.rs`
- Modify: `tauri-app/src-tauri/src/lib.rs`
- Modify: `tauri-app/src-tauri/Cargo.toml`

- [ ] **Step 1: Implement process manager**

Replace `tauri-app/src-tauri/src/process_manager.rs`:

```rust
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{command, AppHandle, Manager, State};

pub struct BackendProcess {
    child: Mutex<Option<Child>>,
    shared_secret: Mutex<Option<String>>,
}

impl BackendProcess {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            shared_secret: Mutex::new(None),
        }
    }
}

/// Spawn the Python backend as a subprocess.
/// Reads the SHARED_SECRET from stdout.
#[command]
pub fn start_backend(
    state: State<'_, BackendProcess>,
    python_path: String,
    backend_dir: String,
) -> Result<String, String> {
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;

    if child_guard.is_some() {
        return Err("Backend already running".into());
    }

    let mut child = Command::new(&python_path)
        .arg("main.py")
        .current_dir(&backend_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Failed to spawn backend: {e}"))?;

    // Read the first line of stdout to get the shared secret
    let stdout = child.stdout.take().ok_or("No stdout")?;
    let reader = BufReader::new(stdout);

    let mut secret = String::new();
    for line in reader.lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.starts_with("SHARED_SECRET=") {
            secret = line.trim_start_matches("SHARED_SECRET=").to_string();
            break;
        }
    }

    if secret.is_empty() {
        let _ = child.kill();
        return Err("Failed to read shared secret from backend".into());
    }

    *state.shared_secret.lock().map_err(|e| e.to_string())? = Some(secret.clone());
    *child_guard = Some(child);

    Ok(secret)
}

/// Stop the Python backend subprocess.
#[command]
pub fn stop_backend(state: State<'_, BackendProcess>) -> Result<(), String> {
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = child_guard.take() {
        child.kill().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Check if the backend process is still running.
#[command]
pub fn is_backend_running(state: State<'_, BackendProcess>) -> Result<bool, String> {
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;
    match child_guard.as_mut() {
        Some(child) => match child.try_wait() {
            Ok(Some(_)) => {
                *child_guard = None;
                Ok(false)
            }
            Ok(None) => Ok(true),
            Err(e) => Err(e.to_string()),
        },
        None => Ok(false),
    }
}

/// Get the shared secret for WebSocket authentication.
#[command]
pub fn get_shared_secret(state: State<'_, BackendProcess>) -> Result<String, String> {
    state
        .shared_secret
        .lock()
        .map_err(|e| e.to_string())?
        .clone()
        .ok_or("No secret available (backend not started?)".into())
}
```

- [ ] **Step 2: Update lib.rs to register all commands and state**

Replace `tauri-app/src-tauri/src/lib.rs`:

```rust
mod click_through;
mod process_manager;

use process_manager::BackendProcess;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess::new())
        .invoke_handler(tauri::generate_handler![
            click_through::set_click_through,
            process_manager::start_backend,
            process_manager::stop_backend,
            process_manager::is_backend_running,
            process_manager::get_shared_secret,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

- [ ] **Step 3: Build and verify compilation**

```bash
cd G:/projects/deskpet/tauri-app
npm run tauri build -- --debug 2>&1 | tail -5
```

Expected: Build succeeds (or `cargo check` passes at minimum):

```bash
cd G:/projects/deskpet/tauri-app/src-tauri
cargo check
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd G:/projects/deskpet
git add tauri-app/src-tauri/
git commit -m "feat: implement Tauri process manager for Python backend lifecycle"
```

---

## Task 7: Implement Ollama LLM Provider

**Files:**
- Create: `backend/providers/ollama_llm.py`
- Create: `backend/tests/test_providers.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Write failing test for Ollama provider**

Create `backend/tests/test_providers.py`:

```python
import pytest
import pytest_asyncio
from providers.base import LLMProvider
from providers.ollama_llm import OllamaLLM


def test_ollama_llm_implements_protocol():
    """OllamaLLM must satisfy the LLMProvider protocol."""
    provider = OllamaLLM(model="qwen2.5:14b")
    assert isinstance(provider, LLMProvider)


@pytest.mark.asyncio
async def test_ollama_llm_health_check_fails_when_offline():
    """Health check should return False if Ollama is not running on the test port."""
    provider = OllamaLLM(
        model="nonexistent",
        base_url="http://localhost:19999"  # Port where nothing is running
    )
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_ollama_llm_chat_stream_integration():
    """Integration test: requires Ollama running locally with a model."""
    provider = OllamaLLM(model="qwen2.5:14b")

    if not await provider.health_check():
        pytest.skip("Ollama not running or model not available")

    tokens = []
    async for token in provider.chat_stream(
        [{"role": "user", "content": "Say 'hello' and nothing else."}],
        max_tokens=20,
    ):
        tokens.append(token)

    result = "".join(tokens).lower()
    assert "hello" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python -m pytest tests/test_providers.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'providers.ollama_llm'`

- [ ] **Step 3: Implement OllamaLLM provider**

Create `backend/providers/ollama_llm.py`:

```python
from __future__ import annotations

from typing import AsyncIterator
import httpx
import structlog

logger = structlog.get_logger()


class OllamaLLM:
    """LLM provider using Ollama's REST API for streaming chat."""

    def __init__(
        self,
        model: str = "qwen2.5:14b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens from Ollama."""
        temp = temperature if temperature is not None else self.temperature

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temp,
                "num_predict": max_tokens,
            },
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    import json
                    data = json.loads(line)
                    if "message" in data and "content" in data["message"]:
                        token = data["message"]["content"]
                        if token:
                            yield token
                    if data.get("done", False):
                        break

    async def health_check(self) -> bool:
        """Check if Ollama is running and the model is available."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return False
                models = resp.json().get("models", [])
                return any(m["name"].startswith(self.model) for m in models)
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python -m pytest tests/test_providers.py -v
```

Expected: protocol test PASS, offline test PASS, integration test PASS (or SKIP if Ollama not running)

- [ ] **Step 5: Wire Ollama provider into main.py startup**

In `backend/main.py`, add after the `service_context` creation:

```python
from providers.ollama_llm import OllamaLLM

# ... (after service_context = ServiceContext())

# Register LLM provider
ollama_llm = OllamaLLM(
    model=config.llm.model,
    base_url=config.llm.base_url,
    temperature=config.llm.temperature,
)
service_context.register("llm_engine", ollama_llm)
```

- [ ] **Step 6: Test end-to-end: start backend, send chat via WebSocket**

Start backend:
```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python main.py
```

In another terminal, test with a Python one-liner:
```bash
cd G:/projects/deskpet/backend && .venv/Scripts/python -c "
import asyncio, websockets, json
async def test():
    secret = input('Enter SHARED_SECRET: ')
    async with websockets.connect(
        'ws://127.0.0.1:8100/ws/control',
        additional_headers={'X-Shared-Secret': secret}
    ) as ws:
        await ws.send(json.dumps({'type': 'chat', 'payload': {'text': 'Hello! Say hi back.'}}))
        resp = json.loads(await ws.recv())
        print(f'Response: {resp}')
asyncio.run(test())
"
```

Expected: Response contains LLM-generated text (or echo if Ollama not running)

- [ ] **Step 7: Commit**

```bash
cd G:/projects/deskpet
git add backend/providers/ backend/tests/test_providers.py backend/main.py
git commit -m "feat: implement Ollama LLM provider with streaming chat"
```

---

## Task 8: Integrate Live2D Rendering (POC-A Validation)

**Files:**
- Modify: `tauri-app/package.json`
- Create: `tauri-app/src/components/Live2DCanvas.tsx`
- Modify: `tauri-app/src/App.tsx`
- Create: `tauri-app/public/assets/live2d/` (model files)

**This is the P0 POC.** If Live2D doesn't render at >= 30fps in a transparent WebView2 window, we need to re-evaluate the architecture.

- [ ] **Step 1: Install PixiJS v8 and Live2D engine**

```bash
cd G:/projects/deskpet/tauri-app
npm install pixi.js@^8
npm install pixi-live2d-display-lipsyncpatch
```

Note: `untitled-pixi-live2d-engine` may not yet be published on npm. Check availability:
```bash
npm view untitled-pixi-live2d-engine 2>/dev/null || echo "Not on npm yet"
```

If not available, use `pixi-live2d-display-lipsyncpatch` (fork with lip sync support for PixiJS v7) as the Sprint 1 fallback. The ServiceContext pattern means we can swap the engine later without changing other code.

If `pixi-live2d-display-lipsyncpatch` also has compatibility issues with PixiJS v8, fall back to `pixi-live2d-display@^0.4.0` with PixiJS v7:
```bash
npm install pixi.js@^7 pixi-live2d-display@^0.4.0
```

- [ ] **Step 2: Add a Live2D model for testing**

Download a free Live2D sample model (e.g., Hiyori from the official Cubism SDK samples):

```bash
mkdir -p G:/projects/deskpet/tauri-app/public/assets/live2d/hiyori
```

Place these files in the directory:
- `hiyori_pro_t10.model3.json` (model definition)
- `hiyori_pro_t10.moc3` (model data)
- `hiyori_pro_t10.physics3.json` (physics)
- `textures/` directory with texture PNGs

You can download from: https://www.live2d.com/en/learn/sample/

**Important:** This step is manual. Download the Hiyori sample model from the Live2D website and extract to the directory above.

- [ ] **Step 3: Create Live2DCanvas component**

Create `tauri-app/src/components/Live2DCanvas.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import * as PIXI from "pixi.js";
import { Live2DModel } from "pixi-live2d-display-lipsyncpatch";

// Register PIXI to the Live2D framework
(window as any).PIXI = PIXI;

interface Live2DCanvasProps {
  modelPath: string;
  width?: number;
  height?: number;
  onFpsUpdate?: (fps: number) => void;
}

export function Live2DCanvas({
  modelPath,
  width = 400,
  height = 600,
  onFpsUpdate,
}: Live2DCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const appRef = useRef<PIXI.Application | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!canvasRef.current) return;

    let destroyed = false;

    async function init() {
      try {
        const app = new PIXI.Application();
        await app.init({
          canvas: canvasRef.current!,
          width,
          height,
          backgroundAlpha: 0,  // Transparent background
          antialias: true,
          resolution: window.devicePixelRatio || 1,
          autoDensity: true,
        });

        if (destroyed) {
          app.destroy();
          return;
        }
        appRef.current = app;

        // Load Live2D model
        const model = await Live2DModel.from(modelPath, {
          autoInteract: false,
        });

        if (destroyed) {
          app.destroy();
          return;
        }

        // Scale model to fit canvas
        const scale = Math.min(
          width / model.width,
          height / model.height
        ) * 0.8;
        model.scale.set(scale);
        model.x = (width - model.width * scale) / 2;
        model.y = (height - model.height * scale) / 2;

        app.stage.addChild(model as any);

        // FPS monitoring
        if (onFpsUpdate) {
          app.ticker.add(() => {
            onFpsUpdate(Math.round(app.ticker.FPS));
          });
        }

        setLoading(false);
      } catch (err) {
        if (!destroyed) {
          setError(String(err));
          setLoading(false);
        }
      }
    }

    init();

    return () => {
      destroyed = true;
      if (appRef.current) {
        appRef.current.destroy(true);
        appRef.current = null;
      }
    };
  }, [modelPath, width, height]);

  if (error) {
    return (
      <div style={{ color: "red", padding: 20 }}>
        Live2D Error: {error}
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      style={{
        width: `${width}px`,
        height: `${height}px`,
        position: "absolute",
        top: 0,
        left: 0,
      }}
    />
  );
}
```

- [ ] **Step 4: Update App.tsx to render Live2D**

Replace `tauri-app/src/App.tsx`:

```tsx
import { useState, useCallback } from "react";
import { Live2DCanvas } from "./components/Live2DCanvas";

function App() {
  const [fps, setFps] = useState(0);
  const [message, setMessage] = useState("Hello! I'm your desktop pet!");

  const handleFpsUpdate = useCallback((newFps: number) => {
    setFps(newFps);
  }, []);

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        backgroundColor: "transparent",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* Live2D Character */}
      <Live2DCanvas
        modelPath="/assets/live2d/hiyori/hiyori_pro_t10.model3.json"
        width={400}
        height={600}
        onFpsUpdate={handleFpsUpdate}
      />

      {/* Chat Bubble */}
      <div
        style={{
          position: "absolute",
          bottom: "20px",
          left: "50%",
          transform: "translateX(-50%)",
          backgroundColor: "rgba(255, 255, 255, 0.9)",
          borderRadius: "12px",
          padding: "12px 16px",
          maxWidth: "300px",
          fontSize: "14px",
          color: "#333",
          boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
          zIndex: 10,
        }}
      >
        {message}
      </div>

      {/* Debug: FPS Counter (remove in production) */}
      <div
        style={{
          position: "absolute",
          top: "4px",
          right: "4px",
          fontSize: "10px",
          color: fps >= 30 ? "lime" : "red",
          backgroundColor: "rgba(0,0,0,0.5)",
          padding: "2px 6px",
          borderRadius: "4px",
          zIndex: 20,
        }}
      >
        {fps} FPS
      </div>
    </div>
  );
}

export default App;
```

- [ ] **Step 5: Run and validate POC-A**

```bash
cd G:/projects/deskpet/tauri-app
npm run tauri dev
```

**POC-A Validation Criteria:**
1. ✅ Transparent window — desktop visible behind the character
2. ✅ Live2D model renders and shows idle animation
3. ✅ FPS counter shows >= 30fps
4. ✅ Chat bubble visible at bottom

**If FPS < 30:** Check GPU driver, disable antialias, reduce resolution. If still failing, consider using a simpler model or dropping to PixiJS v7.

**If transparent background doesn't work:** This is a WebView2 limitation. Check Windows build number (requires 10.0.22621+). As fallback, use a colored background that matches desktop.

- [ ] **Step 6: Commit**

```bash
cd G:/projects/deskpet
git add tauri-app/
git commit -m "feat: integrate Live2D rendering in transparent overlay (POC-A validated)"
```

---

## Task 9: WebSocket Client in Frontend

**Files:**
- Create: `tauri-app/src/types/messages.ts`
- Create: `tauri-app/src/ws/ControlChannel.ts`
- Create: `tauri-app/src/hooks/useWebSocket.ts`
- Modify: `tauri-app/src/App.tsx`

- [ ] **Step 1: Define message types**

Create `tauri-app/src/types/messages.ts`:

```typescript
// Control channel message types

export interface ControlMessage {
  type: string;
  payload?: Record<string, unknown>;
}

export interface ChatMessage extends ControlMessage {
  type: "chat";
  payload: { text: string };
}

export interface ChatResponse extends ControlMessage {
  type: "chat_response";
  payload: { text: string };
}

export interface PingMessage extends ControlMessage {
  type: "ping";
}

export interface PongMessage extends ControlMessage {
  type: "pong";
}

export interface InterruptMessage extends ControlMessage {
  type: "interrupt";
}

export interface ErrorMessage extends ControlMessage {
  type: "error";
  payload: { message: string };
}

export type IncomingMessage = ChatResponse | PongMessage | ErrorMessage;
```

- [ ] **Step 2: Implement ControlChannel class**

Create `tauri-app/src/ws/ControlChannel.ts`:

```typescript
import type { ControlMessage, IncomingMessage } from "../types/messages";

export type ConnectionState = "disconnected" | "connecting" | "connected";

export class ControlChannel {
  private ws: WebSocket | null = null;
  private url: string;
  private secret: string;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners: Set<(msg: IncomingMessage) => void> = new Set();
  private stateListeners: Set<(state: ConnectionState) => void> = new Set();
  private _state: ConnectionState = "disconnected";

  constructor(port: number = 8100, secret: string = "") {
    this.url = `ws://127.0.0.1:${port}/ws/control`;
    this.secret = secret;
  }

  get state(): ConnectionState {
    return this._state;
  }

  private setState(state: ConnectionState) {
    this._state = state;
    this.stateListeners.forEach((fn) => fn(state));
  }

  connect() {
    if (this.ws) return;
    this.setState("connecting");

    // WebSocket in browser doesn't support custom headers directly.
    // Pass secret as a query parameter instead.
    const wsUrl = `${this.url}?secret=${encodeURIComponent(this.secret)}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.setState("connected");
      // Start heartbeat
      this.sendPing();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg: IncomingMessage = JSON.parse(event.data);
        this.listeners.forEach((fn) => fn(msg));
      } catch {
        console.error("Failed to parse message:", event.data);
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.setState("disconnected");
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.setState("disconnected");
  }

  send(msg: ControlMessage) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  sendChat(text: string) {
    this.send({ type: "chat", payload: { text } });
  }

  sendInterrupt() {
    this.send({ type: "interrupt" });
  }

  private sendPing() {
    this.send({ type: "ping" });
  }

  onMessage(fn: (msg: IncomingMessage) => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onStateChange(fn: (state: ConnectionState) => void) {
    this.stateListeners.add(fn);
    return () => this.stateListeners.delete(fn);
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }
}
```

- [ ] **Step 3: Update backend to accept secret via query parameter**

Browser WebSocket doesn't support custom headers. Update `backend/main.py` `_validate_secret`:

```python
def _validate_secret(ws: WebSocket) -> bool:
    """Validate the shared secret from headers or query params."""
    secret = ws.headers.get("x-shared-secret", "")
    if not secret:
        secret = ws.query_params.get("secret", "")
    return secrets.compare_digest(secret, SHARED_SECRET)
```

Also update `backend/tests/test_websocket.py` to test query param auth:

```python
def test_control_ws_accepts_with_secret_query_param():
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}"
    ) as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"
```

- [ ] **Step 4: Create useWebSocket hook**

Create `tauri-app/src/hooks/useWebSocket.ts`:

```typescript
import { useEffect, useRef, useState, useCallback } from "react";
import {
  ControlChannel,
  type ConnectionState,
} from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";

export function useControlChannel(port: number = 8100, secret: string = "") {
  const channelRef = useRef<ControlChannel | null>(null);
  const [state, setState] = useState<ConnectionState>("disconnected");
  const [lastMessage, setLastMessage] = useState<IncomingMessage | null>(null);

  useEffect(() => {
    const channel = new ControlChannel(port, secret);
    channelRef.current = channel;

    const unsubState = channel.onStateChange(setState);
    const unsubMsg = channel.onMessage(setLastMessage);

    channel.connect();

    return () => {
      unsubState();
      unsubMsg();
      channel.disconnect();
      channelRef.current = null;
    };
  }, [port, secret]);

  const sendChat = useCallback((text: string) => {
    channelRef.current?.sendChat(text);
  }, []);

  const sendInterrupt = useCallback(() => {
    channelRef.current?.sendInterrupt();
  }, []);

  return { state, lastMessage, sendChat, sendInterrupt };
}
```

- [ ] **Step 5: Update App.tsx with chat input + WebSocket**

Replace `tauri-app/src/App.tsx`:

```tsx
import { useState, useCallback, useEffect } from "react";
import { Live2DCanvas } from "./components/Live2DCanvas";
import { useControlChannel } from "./hooks/useWebSocket";

function App() {
  const [fps, setFps] = useState(0);
  const [chatText, setChatText] = useState("");
  const [messages, setMessages] = useState<
    { role: "user" | "assistant"; text: string }[]
  >([]);

  // TODO: Get secret from Tauri process manager (Task 10)
  // For now, hardcode or pass via environment
  const { state, lastMessage, sendChat } = useControlChannel(8100, "");

  useEffect(() => {
    if (lastMessage?.type === "chat_response") {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: (lastMessage.payload as any).text },
      ]);
    }
  }, [lastMessage]);

  const handleSend = () => {
    if (!chatText.trim()) return;
    setMessages((prev) => [...prev, { role: "user", text: chatText }]);
    sendChat(chatText);
    setChatText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFpsUpdate = useCallback((newFps: number) => {
    setFps(newFps);
  }, []);

  const lastMsg = messages[messages.length - 1];

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        backgroundColor: "transparent",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* Live2D Character */}
      <Live2DCanvas
        modelPath="/assets/live2d/hiyori/hiyori_pro_t10.model3.json"
        width={400}
        height={500}
        onFpsUpdate={handleFpsUpdate}
      />

      {/* Chat Bubble - shows last message */}
      {lastMsg && (
        <div
          style={{
            position: "absolute",
            bottom: "80px",
            left: "50%",
            transform: "translateX(-50%)",
            backgroundColor:
              lastMsg.role === "user"
                ? "rgba(59, 130, 246, 0.9)"
                : "rgba(255, 255, 255, 0.9)",
            color: lastMsg.role === "user" ? "white" : "#333",
            borderRadius: "12px",
            padding: "10px 14px",
            maxWidth: "300px",
            fontSize: "13px",
            boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
            zIndex: 10,
          }}
        >
          {lastMsg.text}
        </div>
      )}

      {/* Chat Input */}
      <div
        style={{
          position: "absolute",
          bottom: "10px",
          left: "10px",
          right: "10px",
          display: "flex",
          gap: "8px",
          zIndex: 20,
        }}
      >
        <input
          type="text"
          value={chatText}
          onChange={(e) => setChatText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            state === "connected" ? "Say something..." : "Connecting..."
          }
          disabled={state !== "connected"}
          style={{
            flex: 1,
            padding: "8px 12px",
            borderRadius: "20px",
            border: "1px solid #ddd",
            fontSize: "13px",
            backgroundColor: "rgba(255,255,255,0.95)",
            outline: "none",
          }}
        />
        <button
          onClick={handleSend}
          disabled={state !== "connected" || !chatText.trim()}
          style={{
            padding: "8px 16px",
            borderRadius: "20px",
            border: "none",
            backgroundColor:
              state === "connected" ? "#3b82f6" : "#ccc",
            color: "white",
            fontSize: "13px",
            cursor: state === "connected" ? "pointer" : "default",
          }}
        >
          Send
        </button>
      </div>

      {/* Status indicators */}
      <div
        style={{
          position: "absolute",
          top: "4px",
          right: "4px",
          display: "flex",
          gap: "6px",
          zIndex: 20,
        }}
      >
        <span
          style={{
            fontSize: "10px",
            color: fps >= 30 ? "lime" : "red",
            backgroundColor: "rgba(0,0,0,0.5)",
            padding: "2px 6px",
            borderRadius: "4px",
          }}
        >
          {fps} FPS
        </span>
        <span
          style={{
            fontSize: "10px",
            color: state === "connected" ? "lime" : "orange",
            backgroundColor: "rgba(0,0,0,0.5)",
            padding: "2px 6px",
            borderRadius: "4px",
          }}
        >
          {state}
        </span>
      </div>
    </div>
  );
}

export default App;
```

- [ ] **Step 6: Run and verify WebSocket chat works**

Terminal 1 — Start backend:
```bash
cd G:/projects/deskpet/backend
.venv/Scripts/python main.py
```
Note the SHARED_SECRET printed.

Terminal 2 — Start frontend (temporarily hardcode secret in App.tsx or disable auth for testing):
```bash
cd G:/projects/deskpet/tauri-app
npm run tauri dev
```

Type in the chat input and press Enter. You should see:
1. Your message appears as a blue bubble
2. A response appears as a white bubble (either echo or LLM response if Ollama is running)
3. Status shows "connected" in green

- [ ] **Step 7: Commit**

```bash
cd G:/projects/deskpet
git add tauri-app/src/ backend/main.py backend/tests/
git commit -m "feat: implement WebSocket control channel with chat UI"
```

---

## Task 10: End-to-End Integration

**Files:**
- Modify: `tauri-app/src-tauri/src/lib.rs` (auto-start backend)
- Modify: `tauri-app/src/App.tsx` (connect secret from process manager)
- Create: `tests/e2e/test_chat_flow.py`

- [ ] **Step 1: Add Tauri setup hook to auto-start backend**

Update `tauri-app/src-tauri/src/lib.rs` to start backend on app launch:

```rust
mod click_through;
mod process_manager;

use process_manager::BackendProcess;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess::new())
        .invoke_handler(tauri::generate_handler![
            click_through::set_click_through,
            process_manager::start_backend,
            process_manager::stop_backend,
            process_manager::is_backend_running,
            process_manager::get_shared_secret,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // Stop backend when window closes
                if let Some(state) = window.try_state::<BackendProcess>() {
                    let _ = state.child.lock().map(|mut guard| {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                        }
                    });
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

- [ ] **Step 2: Update App.tsx to invoke process manager**

Add to the top of `tauri-app/src/App.tsx`:

```tsx
import { invoke } from "@tauri-apps/api/core";

// ... inside App component, add:
const [secret, setSecret] = useState("");

useEffect(() => {
  async function startBackend() {
    try {
      // Try to get existing secret first
      const s = await invoke<string>("get_shared_secret");
      setSecret(s);
    } catch {
      // Backend not started, start it
      try {
        const backendDir = "G:/projects/deskpet/backend"; // TODO: resolve relative to app
        const pythonPath = "G:/projects/deskpet/backend/.venv/Scripts/python.exe";
        const s = await invoke<string>("start_backend", {
          pythonPath,
          backendDir,
        });
        setSecret(s);
      } catch (err) {
        console.error("Failed to start backend:", err);
      }
    }
  }
  startBackend();
}, []);
```

Update the `useControlChannel` call to use the dynamic secret:
```tsx
const { state, lastMessage, sendChat } = useControlChannel(8100, secret);
```

- [ ] **Step 3: Write end-to-end test script**

Create `tests/e2e/test_chat_flow.py`:

```python
"""
End-to-end test: verifies the full chat flow works.
Requires: backend running on port 8100.
Run manually: python tests/e2e/test_chat_flow.py
"""
import asyncio
import json
import sys
import httpx
import websockets


async def test_health():
    """Test health endpoint responds."""
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://127.0.0.1:8100/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        print("[PASS] Health check")
        return data["secret_hint"]


async def test_chat_flow(secret: str):
    """Test full chat flow through WebSocket."""
    uri = f"ws://127.0.0.1:8100/ws/control?secret={secret}"
    async with websockets.connect(uri) as ws:
        # Test ping
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await ws.recv())
        assert pong["type"] == "pong"
        print("[PASS] Ping/pong")

        # Test chat
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"text": "Hello!"},
        }))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "chat_response"
        assert "text" in resp["payload"]
        print(f"[PASS] Chat response: {resp['payload']['text'][:50]}...")


async def main():
    print("=== Desktop Pet E2E Test ===")
    print("Requires: backend running on port 8100\n")

    try:
        await test_health()
    except Exception as e:
        print(f"[FAIL] Health check: {e}")
        print("Is the backend running? Start with: cd backend && python main.py")
        sys.exit(1)

    # For full e2e, we need the actual secret. Use health hint + manual input.
    secret = input("Enter SHARED_SECRET from backend stdout: ").strip()
    await test_chat_flow(secret)

    print("\n=== All E2E tests passed! ===")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the full stack and verify**

1. Start the Tauri app (which should auto-start the backend):
```bash
cd G:/projects/deskpet/tauri-app
npm run tauri dev
```

2. Verify:
   - Transparent window appears with Live2D character
   - Status shows "connected" 
   - Type a message and get a response
   - FPS >= 30

If auto-start doesn't work (path issues), start backend manually and test the frontend separately. Fix path resolution before committing.

- [ ] **Step 5: Final Sprint 1 commit**

```bash
cd G:/projects/deskpet
git add .
git commit -m "feat: Sprint 1 complete - foundation with Live2D, WebSocket chat, process manager"
```

---

## Sprint 1 Completion Checklist

After completing all tasks, verify these Sprint 1 deliverables:

| # | Deliverable | How to verify |
|---|-------------|---------------|
| 1 | Transparent overlay window | Desktop visible behind character |
| 2 | Live2D character renders | Idle animation plays at >= 30fps |
| 3 | Python backend starts/stops cleanly | Process manager start/stop commands |
| 4 | ServiceContext DI works | `pytest tests/test_context.py` passes |
| 5 | Dual WebSocket channels | Control channel connected (audio channel wired) |
| 6 | Shared secret auth | Unauthenticated connections rejected |
| 7 | Text chat works E2E | Type message → get LLM response via Ollama |
| 8 | Click-through toggle | `set_click_through(true)` makes window passthrough |

## Next Sprint Preview

**Sprint 2: Voice + Expression (Week 2)** will cover:
- faster-whisper ASR integration
- CosyVoice 2 TTS integration
- Audio WebSocket channel (binary streaming)
- VAD (silero-vad) for voice activity detection
- Lip sync + emotion mapping to Live2D expressions
- Interrupt/barge-in mechanism

Sprint 2 plan will be generated after Sprint 1 is validated.
