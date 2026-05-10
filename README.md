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
