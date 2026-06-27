# DeskPet 架构总览

> 高层结构 + 模块边界 + 数据流。读完这份对项目目录在哪、改什么放哪一目了然。

---

## 三层架构

```
┌─────────────────────────────────────────────────────────────┐
│              Tauri Shell (Rust, src-tauri/)                 │
│  • 原生窗口（透明 / always-on-top / 可拖动）                  │
│  • OS keychain 适配（DPAPI / Keychain / libsecret）          │
│  • 系统托盘 + 自启 + 自动更新                                 │
│  • 子进程：拉起 Python 后端                                   │
└────────────────────────┬────────────────────────────────────┘
                         │ IPC：localhost HTTP + WebSocket
                         ▼
┌─────────────────────────────────────────────────────────────┐
│             Frontend (React + Vite, tauri-app/src/)         │
│  • Live2D 渲染（PixiJS v7 + pixi-live2d-display）            │
│  • 对话 UI / 设置面板 / Code Panel                            │
│  • Zustand stores：sessions / providers / pet state          │
│  • WebSocket 连后端 control channel                          │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP / WS (端口 8100)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            Backend (Python FastAPI, backend/)               │
│                                                              │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────────┐   │
│   │  ASR    │  │  VAD    │  │  LLM    │  │     TTS      │   │
│   │ whisper │  │ silero  │  │ adapters│  │   edge-tts   │   │
│   └────┬────┘  └────┬────┘  └────┬────┘  └──────┬───────┘   │
│        └────────────┴───────────┬┴──────────────┘           │
│                                 │                            │
│                     ┌───────────▼────────────┐               │
│                     │  Agent Loop + Memory   │               │
│                     │  • 短期 / 情景 / 实体  │               │
│                     │  • BGE-M3 向量检索     │               │
│                     │  • Skills 调度         │               │
│                     └────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
              外部 LLM Provider（用户配置的）
              OpenAI / Anthropic / Ollama / 兼容代理
```

---

## 目录布局

```
deskpet/
├── backend/                  Python FastAPI 后端
│   ├── main.py               FastAPI app + ws routes
│   ├── pyproject.toml        Python 依赖
│   ├── agent/                旧 P3 代码 (agent loop, supervisor)
│   ├── deskpet/              P4+ 新架构 (Poseidon)
│   │   ├── agent/            assembler + classifier + verify gate
│   │   ├── memory/           三层记忆 + sqlite-vec
│   │   ├── tools/            工具调用 (ppt / web / image / OCR / browser-use)
│   │   ├── skills/           skill 加载器 + builtin skills
│   │   └── mcp/              MCP client
│   ├── llm/                  Provider 适配 + 路由 + fallback
│   ├── providers/            ASR / TTS 引擎 provider 实现
│   ├── pipeline/             语音管线编排
│   ├── memory/               L1 短期记忆 (旧路径，将迁入 deskpet.memory)
│   ├── billing/              token budget + recharge hint
│   ├── observability/        metrics / structured log
│   └── tests/                pytest 套件
│
├── tauri-app/                Tauri + React 桌面前端
│   ├── package.json          npm 依赖
│   ├── vite.config.ts        Vite 配置（HMR 端口 / 后端代理）
│   ├── src/                  TypeScript / React 源码
│   │   ├── App.tsx           顶层布局
│   │   ├── components/       UI 组件（设置、对话、Live2D）
│   │   ├── auth/             登录适配（manual / relay 两套）
│   │   ├── code-panel/       Code Panel（多 session 代码模式）
│   │   ├── message-panel/    宠物左侧留言板
│   │   ├── pet-anim/         动画状态机
│   │   ├── pet-state/        宠物状态 store
│   │   └── stores/           Zustand stores
│   ├── public/
│   │   ├── lib/              live2dcubismcore.min.js（第三方专有）
│   │   └── assets/live2d/    Hiyori / 其他模型
│   └── src-tauri/            Rust 原生层
│       ├── Cargo.toml        Rust 依赖
│       ├── tauri.conf.json   Tauri 配置
│       └── src/              Rust 源码（IPC commands, secrets, device）
│
├── scripts/                  开发 / OPS 脚本
│   ├── oss/                  开源准备脚本（SPDX header）
│   ├── perf/                 性能基准
│   ├── acceptance/           验收脚本
│   ├── e2e_*.py              端到端 smoke
│   ├── dev-start.ps1         Win 启动脚本
│   └── setup_models.py       OSS 用户首次模型下载
│
├── docs/                     技术 / 架构文档
├── openspec/                 OpenSpec 工程规范 + change proposals
├── plans/                    历史 + 进行中的规划文档
│   └── archive/              pre-beta 老规划归档
├── licenses/                 第三方组件许可归属
├── evidence/                 关键 milestone 的真实测试证据
├── config.toml               全局配置（模型 / budget / 行为开关）
├── LICENSE                   BUSL-1.1 主体协议
├── LICENSE.FAQ.md            中文 BUSL 8-Q&A
└── README.md                 项目首页
```

---

## 关键设计决策

### 1. 三层而不是 monolith

- **Tauri 层**：只做 native 该做的（窗口 / keychain / 系统 API）
- **前端 React**：所有 UI 都在 webview，复用浏览器生态
- **后端 Python**：ML 重活全部下沉，Tauri 不绑 Python ABI
- **好处**：前后端可独立替换；后端可以单独跑当 server

### 2. localhost-only IPC

- 后端只听 127.0.0.1:8100，外部不可达
- WS 控制通道用 `DESKPET_SHARED_SECRET` 鉴权
- LLM API key 走 OS keychain，**绝不**入 config.toml

### 3. 两套 build edition：`manual` vs `relay`

| 维度 | `manual`（OSS 主路径） | `relay`（带登录的私有版） |
|---|---|---|
| 触发 | `npm run dev` / `npm run build` | `npm run dev:relay` / `npm run build:relay` |
| 登录窗 | ❌ 没有 | ✅ 强制 |
| LLM key 来源 | 用户在 Settings 手填 | relay 登录下发 device key（复用，见下） |
| LLM provider | 用户手填，进 `LLMProviderRegistry`（`config.toml [[llm.endpoints]]`）| **登录后自动收编进同一 registry**，作为 `source="relay"` 的 `relay-cloud` 行，设置面板可统一管理 |

OSS 用户走 `manual`；维护者带 relay 服务的用 `relay`。两套共享主体代码。

> **relay 收编（2026-06-25，`plans/2026-06-25-relay-local-apikey-provider/`）**：relay 登录后由前端
> `relayProviderRegistration` 把 device key 镜像进 `LLMProviderRegistry` 的 `relay-cloud` 条目
> （key 存 keychain `deskpet/provider.relay-cloud`，账号指纹 `account_ref`），聊天经 `resolve_provider_for_session`
> → registry chain **按需读 key**，绕开 spawn 期固定的 `DESKPET_CLOUD_API_KEY` env 与 `deskpet-cloud-llm`
> keychain slot（后两者**自此仅服务 manual/legacy 手填路径**）。device key 复用三态见
> [`02-relay-handoff-device-key-reuse.md`](./plans/2026-06-25-relay-local-apikey-provider/02-relay-handoff-device-key-reuse.md)。
> 门控：前端 `relayConfig.RELAY_MANAGED_PROVIDER` + 后端 `[features].relay_managed_provider`（默认 ON；
> OFF 回退旧 `relayProviderBridge` 旁路）。`X-Device-Id` 持久化在 `<user_data>/device_id`（`device.rs`，跨重启稳定）。

### 4. 三层记忆

- **L1 短期**：当前对话 buffer（in-memory + sqlite WAL）
- **L2 情景**：跨对话事件回忆（sqlite + BGE-M3 embedding via sqlite-vec）
- **L3 实体**：长期人物 / 偏好画像（结构化 + 半结构化）

### 5. Skill 系统

- 内置 skills 在 `backend/deskpet/skills/builtin/`（office / web / image / OCR）
- 用户 skills 在 `%APPData%\deskpet\skills\user\`（watchdog 监控热加载）
- 每个 skill = `SKILL.md` (元数据 + prompt) + 可选 `script.py`

### 6. CUDA-only（当前）

- ASR / embedding / TTS 都需要 GPU
- 长期支持 CPU / AMD / Intel，但当前不在 baseline
- 详见 [`HARDWARE_COMPROMISES.md`](./HARDWARE_COMPROMISES.md)

---

## 数据流：一次对话

```
用户讲话
  │
  ▼ (麦克风 / Tauri permissions)
前端采音 ─────▶ 后端 /pipeline/listen
  │              │
  │              ▼ VAD 检测 speech boundary
  │              ▼ ASR 转文字
  │              ▼ LLM agent loop（带记忆 + 工具）
  │              ▼ TTS 合成
  │              ▼ Live2D motion / expression 派发
  ▼ (ws control channel)
前端：渲染对话气泡 + 触发 Live2D lipsync + 播放音频
```

---

## 关键扩展点

| 想干什么 | 改哪 |
|---|---|
| 加新 LLM Provider | `backend/llm/<provider>_adapter.py` |
| 加新工具（被 LLM 调用） | `backend/deskpet/tools/<tool>.py` + 注册到 registry |
| 加新内置 skill | `backend/deskpet/skills/builtin/<skill>/SKILL.md` |
| 加新 Live2D 模型 | `tauri-app/public/assets/live2d/<model>/` + 改默认 |
| 加新 UI 面板 | `tauri-app/src/components/<Panel>.tsx` + 接 store |
| 加新 IPC command (Tauri) | `tauri-app/src-tauri/src/<feature>.rs` + 注册到 invoke handler |

---

## 进一步阅读

- 工程规范：[`openspec/AGENTS.md`](./openspec/AGENTS.md)
- 项目 CLAUDE 笔记：[`CLAUDE.md`](./CLAUDE.md)
- 历史架构决策：`docs/P6-migration-decisions.md`、`docs/superpowers/STATE.md`
- 第三方依赖：[`licenses/README.md`](./licenses/README.md)

---

*Last updated: 2026-05-27*
