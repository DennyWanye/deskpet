# DeskPet

本地部署的桌面语音宠物：Live2D 桌宠 + 全本地语音交互管线（VAD → ASR → LLM → TTS）。

---

> ## 🔑 开发期登录测试凭据
>
> 启动桌宠 → onboarding 登录流程 → relay 中转站会下发 LLM API key（写入 OS keychain）→
> 后端通过 `DESKPET_CLOUD_API_KEY` env 拿到 key 调真实云端 LLM（默认 gpt-5.5 via the relay）。
>
> **本仓库不包含测试凭据**。要走 relay edition 端到端 E2E，请自己注册一个中转站账号，
> 把账号密码写到 `LOCAL-DEV-CREDENTIALS.md`（已在 `.gitignore` 里）。模板见
> [`LOCAL-DEV-CREDENTIALS.md.example`](./LOCAL-DEV-CREDENTIALS.md.example)。
>
> OSS 用户跑 manual edition 不需要任何中转站凭据 —— 在 Settings → LLM Providers
> 里手填自己的 OpenAI / Anthropic / 本地 Ollama 即可。

---

> ## ⚙️ 两套 Build Edition：`manual` vs `relay`
>
> DeskPet 前端构建有**两套 edition**，由 Vite 的 `VITE_AUTH_EDITION` 切换；选哪套决定了"是否有登录 UI / LLM key 哪里来"。
>
> | 维度 | `manual`（OSS 默认） | `relay`（付费版，本仓库默认 dev 用） |
> |---|---|---|
> | 触发命令 | `npm run dev` / `npm run build` | `npm run dev:relay` / `npm run build:relay`（加载 `tauri-app/.env.relay`） |
> | Adapter | `ManualAuthAdapter` | `RelayAuthAdapter` |
> | 左上角 👤 账户菜单 | ❌ 不渲染 | ✅ 渲染（点击打开 `AccountSettingsPanel`） |
> | 强制登录窗 | ❌ 没有 | ✅ 没 token 就弹 `<RelayAuthModal>`，**无法关闭** |
> | LLM key 来源 | 用户自己在「设置面板 → 模型」手填 | relay 自动下发 `tsk_xxx` device key → keychain |
> | 适用场景 | OSS 自托管 / 自带 OpenAI key | 用自己注册的中转站账号走 the relay（凭据见 `LOCAL-DEV-CREDENTIALS.md`） |
>
> **`tauri.conf.json` 的 `beforeDevCommand` / `beforeBuildCommand` 决定 `tauri dev` / `tauri build` 默认走哪套**。本仓库当前为 `dev:relay` / `build:relay`，因此 `cargo tauri dev` 起来就有左上角登录 pill + 强制登录窗。要回到 OSS manual 默认，把那两行改回 `npm run dev` / `npm run build`。

> **Relay 模式下的账户按钮位置**：登录后右上角工具栏**最左边**那个用户图标即"账户设置"（点击 → 看登录邮箱 / device key 状态 / 登出）。2026-05-26 起从独立 fixed pill 改成 Toolbar 第一个 IconButton，视觉跟其它面板入口统一。

---

> ## 🪟 桌宠窗尺寸（2026-05-26）
>
> 主窗默认 360×600，**可自由拖动右下角调整**（min 240×360 / max 1200×1600）。Rust 侧防抖 800ms 把 logical (w,h) 写到 `<user_data>/window_geometry.json`，下次启动自动恢复。前端 Toolbar / DialogBar / Live2DCanvas 都用 absolute + flex + ResizeObserver 响应窗口尺寸，无需额外配置。
>
> - 重置默认尺寸：删 `%AppData%\deskpet\window_geometry.json` 即可
> - 程序化设置：`invoke("set_window_geometry", { width: 480, height: 720 })`
> - 查询当前：`invoke("get_saved_window_geometry")` → `{width, height} | null`

---

**技术栈：** Tauri 2 + React + PixiJS v7 + pixi-live2d-display（前端）· Python FastAPI + faster-whisper + Silero VAD + edge-tts + Ollama（后端）。

---

> ## 📜 许可证 & 第三方资产
>
> **DeskPet 主体代码** 采用 [BUSL-1.1](./LICENSE)（Business Source License 1.1），
> 2030-05-27 自动转 Apache License 2.0。个人 / 公司内部 / 研究 / 非竞争性产品都可商业使用；
> 受限的只有"把 DeskPet 作为托管/嵌入服务卖给第三方跟原作付费版本竞争"这一种场景。
> 详见中文 FAQ：[`LICENSE.FAQ.md`](./LICENSE.FAQ.md)。
>
> **⚠️ Live2D 第三方资产**（重要）：本仓库为了开箱即用，bundle 了 Live2D Inc. 的两项专有内容，
> **它们不受 DeskPet 的 BUSL-1.1 覆盖**，下游 fork / 商业发布时**你**需要遵守 Live2D Inc. 的独立条款：
>
> | 组件 | 文件 | 许可证 | 归属 |
> |---|---|---|---|
> | Live2D Cubism Core 运行时 | `tauri-app/public/lib/live2dcubismcore.min.js` | Live2D Proprietary Software License Agreement | [`licenses/LIVE2D-CUBISM.md`](./licenses/LIVE2D-CUBISM.md) |
> | Hiyori 示例模型 | `tauri-app/public/assets/live2d/hiyori/` | Live2D Free Material License Agreement | [`licenses/LIVE2D-HIYORI.md`](./licenses/LIVE2D-HIYORI.md) |
>
> 关键 caveats：
> - npm 包 `live2dcubismcore@1.0.2` 的 `package.json` 标了 `"license": "ISC"`，**这是错的** —— 实际是 Live2D 专有 EULA
> - 个人 / 小企业（年营收 < 10M JPY）使用 Hiyori 商业 / 非商业都允许，超阈值需要 Live2D Publication License
> - 不能把 Hiyori 模型文件作为独立内容重新发布（典型 SaaS 模型站行为）
> - DeskPet **不是** Live2D Inc. 出品 / 认证 / 推荐的产品；Live2D® 是 Live2D Inc. 注册商标
>
> 其他三方依赖（PixiJS / React / FastAPI / PyTorch / transformers / ...）索引见
> [`licenses/README.md`](./licenses/README.md)。

---

> **硬件兼容性：** 当前仅支持 NVIDIA GPU（CUDA-only）。AMD / Intel / CPU-only 路径作为长期 backlog，详见 [`HARDWARE_COMPROMISES.md`](./HARDWARE_COMPROMISES.md)。

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

# BGE-M3 语义嵌入需要 CUDA torch（PyPI 默认是 CPU-only 轮子）。
# 2026-05-15 verified 组合（其余版本踩过 segfault 坑）：
pip install --index-url https://download.pytorch.org/whl/cu124 \
    torch torchvision torchaudio
pip install "transformers>=4.45,<5" "FlagEmbedding>=1.2,<1.4" "peft<0.15"
# 验证：python -c "from FlagEmbedding import BGEM3FlagModel; import torch; print(torch.cuda.is_available())"
# 必须输出 True 才能脱离 Embedder mock 模式；详见 pyproject.toml 的 ABI pinning 注释。

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

---

## 已知问题（Known Issues）

### chat 偶发红框 `LLM HTTP 400 Bad Request: — llm_error`

**典型场景**：Code mode 跑工具调用链，agent 完成一轮 tool 后准备让 LLM 看结果继续下一步，
此时 中转站偶发返回 HTTP 400。

**真因**：上游 LLM 中转站（your-llm-relay.example.com）问题。诊断证据（2026-05-10 实际抓到的）：

| 证据 | 含义 |
|------|------|
| `body_len=0`，response body 完全空白 | the relay 没说为什么 400，连错误描述都不给 |
| 同一 messages 重发常常就 200 OK | 不是确定性 bug，是间歇性 |
| 触发模式：`last_role=tool` 后继续 | OpenAI 标准的 tool calling 流程，messages 结构合法 |
| 重试链 (stream → non-stream → 3 次 backoff) 大多能救回来 | <5% 才会终极失败到 chat_v2_error |

**不是项目侧的 bug**：messages 是合法的 OpenAI 标准格式；同样的请求在 OpenAI 官方
API / DashScope 上不会 400。the relay 作为多模型路由代理，对 deepseek-v4-pro 思维模式
+ tool_calls 的某些组合**转发不稳**。

**已实现的缓解**：
- streaming 失败 → 自动 fallback 非流式（独立重试预算）
- 非流式自带 3 次 backoff (0s / 1s / 3s) 重试
- P4-S24 reasoning_content 400 自动 strip + retry
- 终极失败时 P5-S1 supervisor 接管，桌宠头顶冒气泡 + [重试] / [中断] 按钮

**用户体验**：偶尔看到红框是正常的，**点桌宠气泡的"重试"按钮会真的重新跑一次**
（带 supervisor 的 hint）。绝大多数情况第二次就能成功。

**根治方案**（需要切上游）：把 `[llm].base_url` 换成 OpenAI 官方 / DashScope /
本地 Ollama 等更稳定的端点。

---

## 桌宠 supervisor（P5-S1）

桌宠会**主动监督**你的 Code 模式任务。如果某个 session 卡住了——LLM 反复调同一个失败工具、permission 弹窗没人理、15 分钟无活动、或者直接报错——桌宠的 supervisor 后端会用 LLM 自检判断要不要干预，然后通过桌宠头顶的气泡告诉你最危险的那个 session。

### 工作流程

```
主 agent ── 每个事件 ──▶ session_activity 表
                              │
                每 60s 扫描   ▼
                          watchdog（独立 task）
                              │
              触发: chat_v2_error  或  15min 无活动
                              ▼
                  build_snapshot(sid) ── 不含原对话
                              │（结构化快照）
                              ▼
                supervisor LLM call (30s 硬超时)
                              │
                  ┌──── action ────┐
                  │                │
              wait（不打扰）    nudge / ask_user
                                   │
                            broadcast supervisor_alert
                                   │
                  ┌────────────────┴────────────────┐
                  ▼                                  ▼
           桌宠气泡 + motion           code panel tile 边框变色
              (yellow/red)
                  │
            点气泡背景 → 跳到该 session
            点按钮 → supervisor_user_choice ws
```

### 关键阈值（`config.toml [supervisor]`）

| 字段 | 默认 | 含义 |
|------|------|------|
| `enabled` | `true` | 总开关；关 = 不启动 watchdog 任务 |
| `scan_interval_seconds` | 60 | 多久扫一次活动 session |
| `stuck_threshold_seconds` | 900 | 多久没活动算"卡住" |
| `dedup_seconds` | 720 | 同 sid 多久内不重扫 |
| `max_hints_per_session` | 3 | nudge 队列每 session 上限（防 context 爆炸） |
| `llm_timeout_seconds` | 30 | supervisor LLM 调用硬超时 |

settings 面板 → "桌宠 supervisor (P5-S1)" toggle 也能现场关开。

### 桌宠视觉反馈（5 状态）

| state | 触发 score | motion 节奏 | 眨眼 | 气泡 |
|-------|-----------|-----------|------|------|
| `idle` | <30 | 默认 Idle | 0.2 Hz | 无 |
| `working` | 30–60 | 节奏快子集 | 0.3 Hz | 无 |
| `worried` | 60–100 | 节奏慢子集 | 0.5 Hz | 黄 |
| `alert` | ≥100 | 慢 + TapBody | 0.6 Hz | 红（脉冲） |
| `intervening` | nudge 触发瞬间 | TapBody | 0.3 Hz | 蓝（3 秒） |

滞后阈值 +10 / -10、最短驻留 10s 防抖。`severity_score` 公式见 `src/stores/sessionsStore.ts: severity_score_breakdown()`。

### 审计

每次 supervisor 决策（不含 wait）写一行到 SessionDB `supervisor_hints` 表，含 `sid / alert_id / hint_text / action / severity / diagnosis / user_button / ts`。可在 settings 看累计干预次数（待实装 UI），也可手动查：

```bash
sqlite3 %APPDATA%/deskpet/data/state.db \
  "SELECT ts, session_id, action, severity, diagnosis FROM supervisor_hints ORDER BY ts DESC LIMIT 20"
```

### 运行成本估算

每次 supervisor 调用约 800 tokens 输入 + 200 tokens 输出。一个 session 因 12 分钟去重最多每小时 5 次扫描；按 2 个并发 session 估算上限 = 10 次/小时。便宜小模型（gpt-5-mini 类）月成本 < $1。

### 实测证据

- 单测：`backend/tests/test_p5s1_*.py`（38 项 PASS）
- 集成：`scripts/e2e_p5s1_supervisor.py`（7/7 PASS — 包含 ws toggle ack、user_choice 持久化、in-process supervisor 完整流、watchdog inactivity 触发）
- 视觉：浏览器 E2E 已验黄色 / 红色气泡 + 按钮点击 → ws → DB 行 `user_button='中断'`

### 已知能力上限

Hiyori 没有 Expression 资源，桌宠"困惑/紧张"只能靠 motion 节奏 + 眨眼频率 + 头部角度凑。气泡承担"具体在说什么"的语义。换 Live2D 模型才能突破。

---

## 长期记忆 + 自动总结归档（P4-S20-D）

deskpet 的对话历史用三层存储 + 自动归档维持容量与召回精度：

```
新对话         消息进 messages 主表，BGE-M3 向量化进 messages_vec
   ↓
召回时         retriever 4 路 fan-out（vec + FTS + recency + salience）
   ↓
30 天后        summarizer 把整段老 session 压成 1-3 句话
   ↓ ┌─────────────────────────────────────┐
     │  原文搬到 messages_archive (永久保留) │
     │  summary 入 messages 主表 + 向量化   │
     └─────────────────────────────────────┘
   ↓
长期           messages 主表只剩 summary，召回精度不退化但占用骤降
```

### 关键参数（默认）

| 参数 | 默认 | 含义 |
|------|------|------|
| `age_days` | 30 | session 内全部消息都早于 N 天才归档 |
| `min_messages` | 20 | session 至少这么多条原文才值得归档 |
| `max_per_run` | 10 | 一次最多处理几个 session（防 LLM 打爆） |
| `max_input_chars` | 8000 | 塞给 LLM 的 conversation 字符上限（超出截头尾） |

可在 `memory_summarize_now` IPC 调用里 override。

### 触发方式

**自动**：backend 启动 8 秒后后台 fire-and-forget 一次（不阻塞 startup）。
**手动**：通过 control WebSocket 发：

```jsonc
{
  "type": "memory_summarize_now",
  "payload": { "age_days": 7, "min_messages": 10, "max_per_run": 3 }
}
```

返回：

```jsonc
{
  "type": "memory_summarize_response",
  "payload": {
    "ok": true,
    "sessions_scanned": 1,
    "sessions_summarized": 1,
    "messages_archived": 36,
    "summary_ids": [4958],
    "errors": []
  }
}
```

### 查看 archive

```jsonc
{ "type": "memory_archive_list", "payload": { "limit": 100 } }
// 可选: "session_id": "default"
```

返回所有归档原文，含 `archived_at` 和指向其 summary 的 `archived_into_id`。

### 安全保证

- **事务原子性**：归档 + 删除 + summary 入库要么全成要么全不变（连原文都没动）
- **可恢复**：`messages_archive` 永久保留原文，没有 GC 删除路径
- **可逆设计**：summary message 用 `is_summary=1 + summary_of=[原id列表]` 标记，可通过 archive 找回完整原文
- **失败兜底**：单 session 的 LLM 调用失败 → 跳过，不影响其它 session；错误进 `result.errors` 报告
- **幂等**：已经存在 summary 的 session 不会被重复总结
- **永远不硬删**：只归档，不真的丢消息

### 实测验证

测过的端到端例子（gemma4:e4b 真跑出的输出）：

```
e2e_chat session 36 条 ping 消息 → summary:
"对话内容仅为用户重复发送"ping"指令，系统进行了重复的测试回复。
没有讨论任何实质性的信息、偏好或决策。"
```

详细测试覆盖见 `backend/tests/test_p4s20_summarizer.py`（10 个单测，覆盖候选筛选、事务、失败兜底、超长输入截断、vec enqueue 等）。

### 严格 E2E 测试（5 个维度，真 backend + 真 Ollama）

`backend/scripts/e2e_summarizer_full.py` — 不依赖单测桩，纯生产 backend + 真实 Ollama LLM。任何后续改动只要 5/5 PASS 就基本放心。

```bash
# 要求：backend 跑在 8100，Ollama 跑在 11434
cd backend && python -m scripts.e2e_summarizer_full
```

5 个测试维度：

| 测试 | 验证什么 | 通过标准 |
|------|---------|----------|
| **L3** 真实有意义内容 | 30 条偏好对话 → LLM 摘要 → 保留 ≥2 个关键事实 | summary 含 Rust/咖啡/猫/京都等 |
| **L3.3** vec 入库 | summary 入 messages_vec 才能召回 | 3 秒 flush 后 vec 命中 |
| **L5a** max_per_run 上限 | 3 个候选 + cap=2 → 只处理 2 | scanned=2 |
| **L5b** 幂等 | 已总结 session 再跑不会重复处理 | before/after 状态一致 |
| **L5c** 异常路径 | 切到坏 endpoint → errors[] + 原文完整 | 25 原文不变 |
| **L5d** archive_list IPC | 列出归档行 + schema 字段齐全 | count=30, 6 个键全有 |

**最近一次实测**：5/5 PASS。Ollama gemma4:e4b 给 30 条偏好对话生成的总结：
> "用户偏好信息包括：最喜欢的编程语言是内存安全的 Rust；每天早上饮用冰美式咖啡，不加糖；养了一只名叫小橘的 3 岁橘猫；下个月计划去日本京都游览寺庙；喜欢阅读刘慈欣等作家的科幻小说。"

### 严格测试发现并修复的 3 个 bug

设计阶段的单测全 PASS，但 E2E 暴露了真链路问题。透明记录：

1. **summary 没入向量库** — summarize 后只 INSERT 进 `messages` 主表，**漏了 enqueue 给 vector_worker** → BGE-M3 召回时找不到 summary → 长期记忆链路其实是断的。修：`summarize_old_sessions(vector_worker=...)` 参数 + main.py 在两处调用都传 vector_worker。
2. **embedder RPC stale response** — `_encode_subprocess` 上次调用 timeout 后 stdout 里残留旧响应，下次调用读到旧 id 抛错，整 batch 被 skip。修：drain stale 最多 5 次直到拿到匹配 id。
3. **测试污染 `llm_runtime.json`** — `/config/cloud` 端点的旧测试把 `api.example.com` 永久写到运行时配置文件，下次启动 backend 加载就坏。修：autouse fixture 给该测试文件，每个测试前 snapshot、测试后还原。

### Schema

迁移文件：`backend/deskpet/memory/migrations/002_p4s20_summarize_archive.sql`

新增字段：
- `messages.is_summary` — INTEGER, 1 表示这条本身是 LLM 总结
- `messages.summary_of` — TEXT, JSON 数组，列出被总结的原 message id

新表 `messages_archive`：原文备份 + `archived_at` + `archived_into_id`。

Schema 版本从 v9 升级到 v10。

---

## 文档索引

项目的设计文档、调研报告、内测就绪材料的速查表。代码改动遵循
spec-first：3+ 文件的改动先有 plan/spec，再有实现。

### Companion + Code 模式升级 v1（2026-05-25）

给 DeskPet 加 3 个 **superpowers 级**能力：`/<skill_name>` 命令触发 skill、
`/goal <text>` 长期目标持续工作、`agent_parallel` 多子代理并行 — 把 Claude
Code 多 agent 工作流的思想搬进桌宠产品。

| 文档 | 作用 |
|------|------|
| [`plans/2026-05-25-companion-code-skill-upgrade/00-PRD.md`](./plans/2026-05-25-companion-code-skill-upgrade/00-PRD.md) | PRD v1 — 6 项决策 + 3 Stage WI + 5 项风险 |
| [`plans/2026-05-25-companion-code-skill-upgrade/01-implementation-plan.md`](./plans/2026-05-25-companion-code-skill-upgrade/01-implementation-plan.md) | 实施 plan — Stage A/B/C 子代理派单 |
| [`plans/2026-05-25-companion-code-skill-upgrade/02-manual-test-cases.md`](./plans/2026-05-25-companion-code-skill-upgrade/02-manual-test-cases.md) | 人工测试 MR-S-0~10 + ★ 三大用例 |
| [`plans/2026-05-25-companion-code-skill-upgrade/03-final-report.md`](./plans/2026-05-25-companion-code-skill-upgrade/03-final-report.md) | 最终实施报告（用户简单易懂版） |

**核心新增**（commits TBD）：

- **WI-A Slash Command 框架**（主线程 Stage A）
  - 前端 `tauri-app/src/code-panel/InputBar.tsx` 解析 `/<cmd> [args]`
    → 发 `slash_command` WS 消息（绕过 chat_v2 → LLM 路径）
  - 后端 `backend/main.py` 加 `slash_command` handler + `/api/skills/list`
    REST endpoint（前端 autocomplete 拉候选）
  - 新建 `backend/deskpet/commands/__init__.py` — dispatch_slash_command 路由器
    （help / goal / skill 3 路）
- **WI-B `/goal` 长期目标**（子代理 1 Stage B）
  - 新建 `backend/deskpet/agent/goal_store.py` — SessionGoalStore（per-sid
    内存字典 + max_iterations 计数）
  - 新建 `backend/deskpet/agent/goal_checker.py` — GoalChecker.check(goal, msgs)
    → (done, hint)；3 级 JSON parse fallback + safe-fail
  - `backend/agent/agent_loop.py` 末轮接电（verify_gate 同模式），未达成
    回灌 system "[goal] 未达成: <hint>" + continue
  - 35 测试全 PASS
- **WI-C `agent_parallel` 多子代理并行**（子代理 2 Stage C）
  - 新建 `backend/deskpet/tools/code_tools/agent_parallel_tool.py` —
    并发派 2-4 个 ephemeral 子代理（hub-and-spoke 模式）
  - 自动注入 **Sprint Contract JSON**（input_files / output_files /
    forbidden_files / success_criteria）到子代理 prompt
  - WS event `subagent_progress` 流式反馈（starting / completed / failed）
  - 复用现有 `agent_tool` 工厂模式（15 iter cap + recursion guard）
  - 17 测试全 PASS
- **WI-D FeaturesConfig 守护**
  - `backend/config.py:FeaturesConfig` 3 flag（默认 OFF）：
    `slash_commands` / `goal_mode` / `agent_parallel`
  - flag OFF 时全套新代码 short-circuit（BC）
- **Observability**
  - `metrics_sink.VALID_EVENTS` 加 `goal_checker_invoked` + `subagent_progress`
  - `_ALLOWED_DETAIL_KEYS` 加 `task_id` + `status`

**回归门控**（最终）：
- backend pytest **2061 → 2132 passed**（+71 新增：A 12 + B 35 + C 17 + 余 7）
- 0 failed, 11 skipped, 4 deselected
- 2 个 boot smoke 真路径硬证据脚本：
  - `manual_slash_smoke.py` — `/help` 真返 12 个 skill
  - `manual_goal_smoke.py` — `/goal` 真接电 + `metrics.jsonl` 真增 `goal_checker_invoked`

**Deferred（v2）**：
- UI 反馈层 GoalBar / SubagentProgressCard 组件（后端 wiring 完成，前端组件待加）
- SessionGoalStore SQLite persistence（v1 in-memory）
- Tauri 真 GUI E2E 手测（需启 Tauri + 真 LLM + 登录账号）

### 工具层优化 v3（2026-05-24）

修复 last-mile 升级遗留的 **P0 接电缺口**（VerifyGate 没接进 AgentLoop 导致
fake-completion 生产抓获率 0%）+ 替换 5 个 stub 工具为真实现 + 配置面扩展。

| 文档 | 作用 |
|------|------|
| [`plans/2026-05-24-tool-layer-optimization-v3/00-PRD.md`](./plans/2026-05-24-tool-layer-optimization-v3/00-PRD.md) | **PRD v3** — 17 项决策 (D1-D17) + 15 个工作项 (WI-T2.1~T6.2) + 风险登记 + 排期 |
| [`plans/2026-05-24-tool-layer-optimization-v3/01-TDD.md`](./plans/2026-05-24-tool-layer-optimization-v3/01-TDD.md) | **TDD v3** — 代码骨架 + 测试规格（含 build_agent 工厂签名 / 翻译表 / ToolNameConflictError 设计） |
| [`plans/2026-05-24-tool-layer-optimization-v3/02-manual-test-cases.md`](./plans/2026-05-24-tool-layer-optimization-v3/02-manual-test-cases.md) | **人工测试** — MR-T-0~16，含 ★ 三大一票否决用例 |
| [`plans/2026-05-24-tool-layer-optimization-v3/03-architect-review-round1.md`](./plans/2026-05-24-tool-layer-optimization-v3/03-architect-review-round1.md) | round1 架构评审（opus 4.7 资深视角）— 6 P0 + 5 P1 + 5 missing risks |
| [`plans/2026-05-24-tool-layer-optimization-v3/04-architect-review-round2.md`](./plans/2026-05-24-tool-layer-optimization-v3/04-architect-review-round2.md) | round2 评审 — 6 新 P0（翻译表语义反 / 字典序反 / 工厂签名漏参 等） |
| [`plans/2026-05-24-tool-layer-optimization-v3/06-implementation-progress.md`](./plans/2026-05-24-tool-layer-optimization-v3/06-implementation-progress.md) | M0~M7 实施进度记录 + 测试统计 |
| [`plans/2026-05-24-tool-layer-optimization-v3/08-manual-test-report-round2-real-e2e.md`](./plans/2026-05-24-tool-layer-optimization-v3/08-manual-test-report-round2-real-e2e.md) | **windows-mcp 实机 E2E 报告** — round2 暴露并修复 2 个真接电缺口 |

**核心修复 / 新增能力**（commits `322448b` → `48eaea8`）：

- **WI-T2.1 build_agent 工厂接电 VerifyGate（核心）** — `backend/main.py` 新增
  `build_agent(cfg, ...) -> _AgentLoop` 工厂，把 last-mile 已写好但漏接的
  `verify_gate` / `receipt_store` / `max_verify_nudges` 三个 kwargs 真接到
  `AgentLoop` ctor；fake-completion 生产抓获率从 **0% 升级到可测量**。
  shadow 模式 metrics.jsonl 真出现 `verify_gate_init` event 作为硬证据。
- **WI-T2.2 retention 截断修复** — `_AgentLoop` ReceiptStore 构造点
  `min(retention, 7)` 删掉，用户配 30 天就真按 30 天 cutoff 跑。
- **WI-T2.3 emit_receipt duration_ms 失真修复** — `registry.execute_tool`
  顶部捕真 `_started_at`，duration 反映 dispatch 真实时长（原两次 `now()`
  间隔仅微秒，p95 监控失效）。
- **WI-T3.1 memory_* stub → 真实现** — `memory_tools.py` append
  `memory_write` / `memory_read` / `memory_search`，旧 schema (tier l1/l2/l3)
  透明翻译到 `facts.py:upsert/get_by_id/search`；`facts.py` 新加 `get_by_id`。
- **WI-T3.2 skill_invoke 真实现** — 新建 `deskpet/tools/skill_tools.py`
  接 `SkillLoader.invoke_script`（`bind(skill_loader)` 在 lifespan 注入）。
- **WI-T3.3 mcp_call / delegate 直接 unregister** — 无真 caller，0-release
  删（PRD D10）；真 MCP `mcp_<server>_<tool>` qualified 名走 manager 路径不受影响。
- **WI-T4.1 ToolNameConflictError + replace_allowed opt-in** — 同名重复注册
  且双方都未 opt-in `replace_allowed=True` 直接 raise，防 stubs 静默覆盖真
  实现（last-mile / memory-stage2 教训）；`stubs.py` 改守卫模式。
- **WI-T4.2 plugin/mcp source 自动加前缀** — `registry.register` 检测
  `source="plugin:notion"` 自动改 name 为 `plugin_notion_<tool>`，防 0+ plugin
  装载时同名冲突。
- **WI-T5.1 `[tools]` 配置面扩展 5 字段** — `disabled_toolsets` 双层挡
  (schemas + execute_tool) / `disabled_toolsets_schema_only` 仅 schemas 挡
  / `dangerous_tools_allowlist` / `default_timeout_seconds` / `strict_unknown_toolset`；
  并加 `load_config` mtime 失效缓存（process-wide 单例）。
- **WI-T2.5 frontend CI workflow** — 新建 `.github/workflows/frontend-tests.yml`，
  把 306 个 vitest 用例从"本地自觉跑"提升到 PR required check 级别。
- **Observability 接电** — `metrics_sink.VALID_EVENTS` 加 `verify_gate_init`
  + `verify_gate_nudge_injected`，`agent_loop` nudge 处真 emit metric（不只
  `logger.info`），fake-completion 拦截事件计数化进 metrics.jsonl。

**回归门控**（M7 终态）：
- backend pytest **2051 → 2061 passed**（+38 新增 v3 用例）, 0 failed, 11 skipped
- frontend vitest **306/306 passed**
- cargo test **64/64 passed**
- `scripts/acceptance/last_mile_smoke.py` → **SHIP**（4 一票否决全过）
- windows-mcp 实机 E2E：`%APPDATA%/deskpet/metrics.jsonl` 真增 **14 条
  `verify_*` event** + VerifyGate strict 真拦下伪 "已生成 fake.pptx" claim

**Deferred**（PRD 自己标 / 工程价值低）：
- WI-T2.4 cargo 新增覆盖（现有 64 PASS 已覆盖 artifact_ops.rs 路径）
- WI-T2.6 session_iteration TTL（70KB/周非 leak）
- WI-T2.7 metrics dashboard CLI（metrics.jsonl 已 emit，`cat | grep verify_` 等价）
- WI-T6.1/T6.2 OpenSpec tasks 回填（开发文档已写实施进度）

### 100 人内测就绪（beta-100, 2026-05-22）

| 文档 | 作用 |
|------|------|
| [`plans/2026-05-22-beta-100-readiness.md`](./plans/2026-05-22-beta-100-readiness.md) | 内测就绪**主计划** — 12 个工作项 (WI-01~WI-12) 的 PRD + 技术设计 + TDD + 排期 + Go/No-Go checklist |
| [`plans/2026-05-22-beta-100-manual-test.md`](./plans/2026-05-22-beta-100-manual-test.md) | **人工点击测试脚本** — 6 条测试路径，逐步可勾选，含全新安装 / 升级 / 卸载 / updater 全流程 |
| [`plans/2026-05-22-beta-100-manual-test-results.md`](./plans/2026-05-22-beta-100-manual-test-results.md) | **实机测试执行记录** — windows-mcp 实机跑 WI-01/WI-02，结果 13/13 通过、0 功能 bug |
| [`RELEASE.md`](./RELEASE.md) | **内测发布流程 SOP** — tag → 签名 → `latest.json` → 灰度 → 全量；含回滚预案 |
| [`docs/signing.md`](./docs/signing.md) | Windows 代码签名指南 — OV/EV 证书、`signtool`、SmartScreen 信誉、CI 集成 |
| [`docs/beta-feature-flags.md`](./docs/beta-feature-flags.md) | feature flag 审计表 — memory-v2 / ppt / deep-research 等新功能的默认开关态 |
| [`docs/beta/内测协议.md`](./docs/beta/内测协议.md) | 面向内测用户的协议 — 性质、保密、反馈义务、无 SLA |
| [`docs/beta/隐私说明.md`](./docs/beta/隐私说明.md) | 隐私说明 — 数据存储位置、API key 凭据库、诊断包脱敏（与代码逐条核对） |
| [`docs/beta/已知问题.md`](./docs/beta/已知问题.md) | 用户版已知问题清单 — 从 MSI 已知问题提炼，去掉构建侧、保留用户可见项 |
| [`docs/beta/安装包瘦身评估.md`](./docs/beta/安装包瘦身评估.md) | 安装包瘦身评估 — 5.4GB MSI 体积构成 + 模型按需下载方案 |

**beta-100 新增能力**（commit `4ee869c`，全部 strangler-fig、feature-flag 默认 OFF）：
- **WI-01 onboarding 向导** — 首次启动 3 步配置引导（`tauri-app/src/components/OnboardingWizard.tsx` + `src-tauri/src/onboarding.rs`）
- **WI-02 应用内反馈** — Toolbar 🐞 按钮一键打包脱敏诊断 zip（`FeedbackPanel.tsx` + `src-tauri/src/diagnostics.rs`，**绝不含 api_key**）
- **WI-04 成本护栏** — 每日预算 80% 早期警告（`backend/billing/ledger.py`）
- **WI-05 进程生命周期** — 启动/退出残留检测脚本（`scripts/e2e_process_lifecycle.ps1`）
- **WI-12 最小可观测** — 匿名使用计数 `metrics.jsonl`，key 白名单隐私墙（`backend/observability/metrics_sink.py`）

### 记忆系统第二代（memory-v2, Phase A-E）

| 文档 | 作用 |
|------|------|
| [`plans/2026-05-21-memory-system-survey.md`](./plans/2026-05-21-memory-system-survey.md) | 记忆系统**调研 + 改造路线图** — 现状盘点、6 大痛点、mem0/Letta 对标、Phase A-E 设计 |

**memory-v2 新增模块**（commit `e3e090b`，全部默认 OFF）：
- **Phase A 评估底座** — `deskpet/memory/eval/`：hit@k/MRR 回测 + thumbs-up 反馈
- **Phase B 事实抽取** — `deskpet/memory/facts.py`：mem0 风格 LLM 事实抽取 + 冲突合并
- **Phase C 召回精度** — `reranker.py` / `chunker.py` / `query_rewriter.py`
- **Phase D 工作记忆** — `deskpet/memory/workspace.py`：per-session 文件操作快照
- **Phase E 反思 + 程序记忆** — `deskpet/memory/reflection.py`
- 非侵入包装器 `enhanced_retriever.py` — 所有 plug-in 为空时与原 Retriever 字节一致

---

### 📝 开发日志：记忆系统 Stage 2 UI 层（2026-05-24）

> 关联：[`plans/2026-05-23-memory-system-stage2/`](./plans/2026-05-23-memory-system-stage2/) — PRD v2 + TDD v2 + 手测 v2 + 三轮人工测试报告
> 关联 commit：`9fe628d` (M1b 后端 + UI) → `8ab1b76` (round 2 真 LLM fix) → `28a93a7` (round 3 真 GUI fix) → `3b1415d` (补 vitest)

#### 新增 UI 能力（`tauri-app/src/components/MemoryPanel.tsx` + `backend/p4_ipc.py`）

| # | 功能 | 入口 | 触发 |
|---|---|---|---|
| 1 | **"事实" tab（第 5 个 view）** | 桌宠 toolbar → 📁 记忆管理 → 切到 **事实** | 自动拉 `memory_facts_list` ws，按 `updated_at` 倒序显示 active facts |
| 2 | **fact 卡片渲染** | 事实 tab 列表项 | `category 徽章 + key: value + subject + 更新时间` |
| 3 | **🗑 删除按钮** | 每条 fact 卡片右上角 | 单击 → ws `memory_forget {fact_id}` → 后端 `is_active=0 + forgotten_at=now()` |
| 4 | **5 秒 undo 浮窗** | 删除后 panel 底部弹出 | "已忘记 X: Y，撤销？" + 实时倒计时；点撤销 → ws `memory_forget_undo {op_id}` → restore；超时自动消失 |
| 5 | **tab 自适应换行** | 5 个 view tab | panel ~200px 装不下 5 个中文 tab，用 `flex-wrap` 让第二行显示"技能 / 事实" |
| 6 | **后端 ws 桥** | `backend/p4_ipc.py` | 新增 3 个 message type：`memory_facts_list / memory_forget / memory_forget_undo` |

#### 测试覆盖

- **18 个 vitest 用例**（`tauri-app/src/components/__tests__/MemoryPanel.facts.test.tsx`）：
  - ws builder 纯函数（5）
  - forget reducer 主路径 + 错误分支（6）
  - undo state 转移 + 5 秒窗口 fakeTimers（7）
- 项目 vitest 总数：**21 files / 297 tests** 全绿；`tsc --noEmit` 0 error

#### 真测试发现并修的 4 个 bug（round 2 in-process 真 LLM + round 3 windows-mcp 真 GUI）

| Bug | 症状 | 修复 |
|---|---|---|
| #1 cross_key prompt 缺 evidence | 真 LLM 漏判"我搞错了，不是花生是海鲜"类修正信号 | `_CROSS_KEY_CONFLICT_PROMPT` 加 `new_evidence` 字段 |
| #2 4 个 parser 不剥 `<think>` 块 | DeepSeek-V4 / Claude thinking 输出含 reasoning 块 → `json.loads` 失败 | 新增 `_strip_reasoning_blocks` helper，4 处接入 |
| #3 facts tab 被 CSS overflow 截掉 | 5 个中文 tab 撑爆 panel 宽度，第 5 个静默消失 | `segGroup` 加 `flexWrap: wrap` |
| #4 `os_tools/read_file` 没 workspace hook | `workspace_state` 表永远空 → workspace_recall 失效 | os_tools `_notify_workspace` + `file_tools.rebind_loop` + main.py lifespan 调用 |

#### 验证指标

- **真 LLM cross_key 误判率（N=30 真模型）**：召回 **80%** (target ≥70%) / 误判 **0%** (target ≤15%)
- backend pytest：**1883 passed / 10 skipped / 0 failed**
- workspace_state 表真填充验证：1 row (path / last_action / byte_size 全有)

#### Stage 2 后端配套（4 个新 flag，全部默认 OFF，Strangler-Fig 第一代行为字节级一致）

- **`cross_key_merge`** — 跨 key 矛盾治理：D3 v2 混合视野（最近 20 ∪ 语义最近 10）
- **`memory_forget`** + `[memory.v2.forget] enable_natural_language=false`（默认禁用自然语言模式，防提示注入）
- **`entity_path`** — entity 索引检索路：LIKE only value + 三档 NER（LLM → Regex+停用词 → Noop）+ `entity_weight=0.10`
- **`episodic_to_semantic`** — summarizer 完成后异步抽 facts，落 `category='episodic_summary'`
- **schema_v2_migrator** — 老库 `superseded_by / forgotten_at` 双列 ALTER + 失败时强制关相关 flag
- **eval_gate strict + CI** — `eval_gate_ci.sh` 看 git diff 召回类改动自动加 `--strict`

### 新技能调研

| 文档 | 作用 |
|------|------|
| [`plans/2026-05-22-ppt-deepresearch-survey.md`](./plans/2026-05-22-ppt-deepresearch-survey.md) | PPT 生成 + DeepResearch 技能**调研与设计** — Gamma/Tome/OpenAI Deep Research 对标 |

**新增技能**（commit `3d8bb03`）：
- **ppt-generate** — `deskpet/tools/ppt_tools.py`：python-pptx 本地生成，3 主题 × 7 布局
- **deep-research** — `deskpet/tools/research_tools.py`：多阶段流水线 + 严格引用核验（plan → search → fetch → synthesize → cite-check）
- SKILL.md 在 `deskpet/skills/builtin/ppt-generate/` 与 `deep-research/`

### 其它历史文档

`docs/` 下另有 Phase 2~6 的架构文档（`P4-agent-harness-prd.md` / `P6-agent-loop-architecture.md` 等）、打包 (`PACKAGING.md`)、性能 (`PERFORMANCE.md`)、权限 (`PERMISSIONS.md`)、技能系统 (`SKILLS.md`)。完整目录见 [`docs/INDEX.md`](./docs/INDEX.md)。
