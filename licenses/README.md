# Third-Party Licenses — DeskPet

This directory tracks third-party software bundled or required by DeskPet, with
emphasis on dependencies whose license is **not** the project's default
permissive baseline.

DeskPet itself is licensed under [BUSL-1.1](../LICENSE) (auto-converts to
Apache 2.0 on 2030-05-27). All third-party components listed here retain their
own original licenses.

---

## Components requiring explicit attribution (proprietary / restricted)

| Component | License | Attribution file |
|---|---|---|
| Live2D Cubism Core (`live2dcubismcore.min.js`) | Live2D Proprietary Software License Agreement | [`LIVE2D-CUBISM.md`](./LIVE2D-CUBISM.md) |
| Hiyori sample model | Live2D Free Material License Agreement | [`LIVE2D-HIYORI.md`](./LIVE2D-HIYORI.md) |

⚠️ **Downstream forks ship these under Live2D Inc.'s separate terms.** Read the
two files above before redistributing DeskPet commercially. The BUSL-1.1
license on DeskPet **does not** relieve you of Live2D's separate obligations.

---

## Direct dependencies — frontend (`tauri-app/package.json`)

Permissive licenses unless flagged otherwise. Versions are pinned in
`tauri-app/package.json`; transitive deps resolve through pnpm.

| Package | License | Notes |
|---|---|---|
| `pixi-live2d-display` | MIT | Copyright (c) 2020 Guan. The pure-JS Cubism renderer used to drive the Hiyori model. |
| `pixi.js` | MIT | WebGL renderer. |
| `react`, `react-dom` | MIT | © Meta Platforms, Inc. |
| `react-markdown` | MIT | |
| `react-syntax-highlighter` | MIT | |
| `react-virtuoso` | MIT | |
| `zustand` | MIT | |
| `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities` | MIT | Drag-and-drop primitives. |
| `@tauri-apps/plugin-autostart`, `@tauri-apps/plugin-updater` | MIT / Apache-2.0 | |
| `@chenglou/pretext` | (verify on install) | Small utility; confirm license against the published package. |
| `live2dcubismcore` | **Live2D Proprietary EULA** (npm metadata says ISC — that is incorrect) | See [`LIVE2D-CUBISM.md`](./LIVE2D-CUBISM.md). |

Dev-only deps (`vite`, `eslint`, `vitest`, `typescript`, etc.) are all MIT or
ISC. They are not shipped in the production binary.

---

## Direct dependencies — Rust shell (`tauri-app/src-tauri/Cargo.toml`)

All listed crates are dual-licensed MIT OR Apache-2.0 unless noted.

| Crate | License | Purpose |
|---|---|---|
| `tauri`, `tauri-build`, `tauri-plugin-opener`, `tauri-plugin-updater`, `tauri-plugin-autostart`, `tauri-plugin-dialog`, `tauri-plugin-clipboard-manager` | MIT / Apache-2.0 | Tauri framework + first-party plugins. |
| `serde`, `serde_json` | MIT / Apache-2.0 | |
| `tokio` | MIT | Async runtime. |
| `reqwest` | MIT / Apache-2.0 | HTTP client (rustls-tls feature, no OpenSSL). |
| `uuid` | MIT / Apache-2.0 | |
| `keyring` | MIT / Apache-2.0 | OS keychain (Windows DPAPI / macOS Keychain). |
| `window-vibrancy` | MIT / Apache-2.0 | |
| `nvml-wrapper` | MIT | NVIDIA Management Library binding. |
| `webview2-com` | MIT / Apache-2.0 | Windows-only WebView2 integration. |

The full transitive crate license inventory can be regenerated with
`cargo about generate` (recommended for release artifacts) or
`cargo license` once configured.

---

## Direct dependencies — Python backend (`backend/pyproject.toml`)

Mix of permissive licenses. The two flagged rows below should be re-verified
against current upstream metadata before release.

### Web / API stack
| Package | License |
|---|---|
| `fastapi` | MIT |
| `uvicorn[standard]` | BSD-3-Clause |
| `websockets` | BSD-3-Clause |
| `httpx` | BSD-3-Clause |
| `pydantic` | MIT |
| `prometheus-client` | Apache-2.0 |
| `structlog` | MIT / Apache-2.0 |
| `pyyaml` | MIT |
| `tomli` | MIT |
| `platformdirs` | MIT |
| `psutil` | BSD-3-Clause |
| `watchdog` | Apache-2.0 |

### LLM / agent stack
| Package | License |
|---|---|
| `anthropic` | MIT |
| `openai` | Apache-2.0 |
| `google-genai` | Apache-2.0 |
| `ollama` | MIT |
| `mcp` | MIT (Anthropic) |
| `keyring` | MIT |

### Memory / vector / embedding
| Package | License |
|---|---|
| `aiosqlite` | MIT |
| `sqlite-vec` | Apache-2.0 / MIT (dual) |
| `FlagEmbedding` | MIT |
| `transformers` | Apache-2.0 |
| `peft` | Apache-2.0 |
| `torch` | BSD-3-Clause (with attached PATENTS file) |
| `silero-vad` | MIT |

### Web / content extraction
| Package | License | Notes |
|---|---|---|
| `trafilatura` | ⚠️ **Verify** — has been GPL-3.0 historically; check the installed wheel's metadata. If GPL-3, that constrains how it can be linked from BUSL-1.1 code. | Used by `web_crawl` tool. |
| `selectolax` | MIT | |
| `browser-use` | MIT | LLM-driven browser automation. |
| `mss` | MIT | Screen capture. |
| `pyautogui` | BSD-3-Clause | |
| `rapidocr-onnxruntime` | Apache-2.0 | Bundles ONNX models with their own per-model licenses (mostly Apache-2.0). |

### Office / document
| Package | License |
|---|---|
| `python-pptx` | MIT |
| `openpyxl` | MIT |
| `python-docx` | MIT |
| `tzdata` | Apache-2.0 (Windows wheel of IANA tz data — IANA data is public domain) |

### Dev-only (not shipped in frozen builds)
`pytest`, `pytest-asyncio`, `pyinstaller`, `pyinstaller-hooks-contrib` — MIT.

---

## Items flagged for pre-release verification

Before publishing v1.0 or any version that is shipped to outside users:

- [ ] **`trafilatura` license** — confirm current version is Apache-2.0 (the
      author relicensed in v1.x). If still GPL-3 on the pinned version, either
      bump it or replace the dependency.
- [ ] **`@chenglou/pretext` license** — verify against published npm metadata.
- [ ] **`torch` PATENTS file** — bundle the PATENTS notice alongside torch
      attribution if shipping the wheel.
- [ ] **`rapidocr-onnxruntime` bundled ONNX models** — each model has its own
      license inside the wheel. Audit before shipping.
- [ ] **Hugging Face model weights** (BGE-M3 INT8 in `user_models_dir()`) —
      separate from the `FlagEmbedding` Python package. The downloaded weights
      are governed by the original BAAI model card license, not MIT.
- [ ] **Generate a full transitive attribution bundle** for the release:
      - Frontend: `pnpm licenses list --prod --json > frontend-licenses.json`
      - Rust: `cargo about generate` (configure `about.hbs` template first)
      - Python: `pip-licenses --format=markdown --with-license-file --output-file=backend-licenses.md`

---

## How to regenerate this index

This file is curated manually for clarity. The auto-generated transitive
attribution bundles (see the last checklist item above) should be produced as
release artifacts and shipped inside the installer or alongside downloads.

If you add a new direct dependency, please:
1. Note its license here under the appropriate section
2. Flag it for the pre-release verification checklist if its license is not
   one of MIT / Apache-2.0 / BSD-3 / ISC

---

*Index last updated: 2026-05-27 for DeskPet `0.5.0-phase3-rc1`.*
