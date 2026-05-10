# DeskPet

本地部署的桌面语音宠物：Live2D 桌宠 + 全本地语音交互管线（VAD → ASR → LLM → TTS）。

**技术栈：** Tauri 2 + React + PixiJS v7 + pixi-live2d-display（前端）· Python FastAPI + faster-whisper + Silero VAD + edge-tts + Ollama（后端）。

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
此时 chinzy 中转站偶发返回 HTTP 400。

**真因**：上游 LLM 中转站（chinzy.com）问题。诊断证据（2026-05-10 实际抓到的）：

| 证据 | 含义 |
|------|------|
| `body_len=0`，response body 完全空白 | chinzy 没说为什么 400，连错误描述都不给 |
| 同一 messages 重发常常就 200 OK | 不是确定性 bug，是间歇性 |
| 触发模式：`last_role=tool` 后继续 | OpenAI 标准的 tool calling 流程，messages 结构合法 |
| 重试链 (stream → non-stream → 3 次 backoff) 大多能救回来 | <5% 才会终极失败到 chat_v2_error |

**不是项目侧的 bug**：messages 是合法的 OpenAI 标准格式；同样的请求在 OpenAI 官方
API / DashScope 上不会 400。chinzy 作为多模型路由代理，对 deepseek-v4-pro 思维模式
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
