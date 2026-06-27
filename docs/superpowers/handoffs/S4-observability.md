# S4 — 可观测性 + VRAM 分级 HANDOFF

**完成：** 2026-04-14 · 分支 `feat/slice-4-observability`
**Plan：** [plans/2026-04-14-slice-4-observability.md](../plans/2026-04-14-slice-4-observability.md)

---

## 做了什么

- ✅ `backend/observability/metrics.py`：`stage_timer(name, logger=None, **ctx)` async context manager
  - 成功 → `stage_complete`；异常 → `stage_error`（含 error 字段），并重新抛出
  - 字段标准化：`stage`、`elapsed_ms`、任意 `**ctx` 自由扩展
- ✅ `backend/observability/vram.py`：`detect_vram_gb()` + `recommend_asr_device(min_gb=4.0)`
  - torch 未装 / CUDA 不可用 / 任何异常 → 0.0（降级到 CPU）
  - 返回 tuple `(device, compute_type)` — `("cuda", "float16")` 或 `("cpu", "int8")`
- ✅ `VoicePipeline` instrument：`asr` / `agent` / `tts` 三阶段分别用 `stage_timer` 包裹
- ✅ `ToolUsingAgent` instrument：`tool_invoke` 阶段带 `tool_name` + `session_id`
- ✅ `main.py`：`config.asr.device == "auto"` → `recommend_asr_device()` 自动选型；显式 `"cuda"/"cpu"` 维持原行为
- ✅ `pyproject.toml` packages 加 `observability`

---

## 门控

```
pytest tests/ -v --ignore=tests/test_e2e_pipeline.py
66 passed, 1 skipped in 5.65s
  - 9 new: test_observability.py
    - stage_timer: 成功 log / 异常 log+raise / ctx 字段透传
    - detect_vram_gb: 无 torch / 无 cuda
    - recommend_asr_device: 充足 VRAM / 零 VRAM / 自定义阈值 / 边界（== min）
  - 57 existing: 全绿（S0-S3 的 instrument 后行为不变）

import smoke: import main → OK; agent=ToolUsingAgent; asr device=cuda
```

---

## 偏离 Plan

### D1 — test caplog → capsys 迁移
- 最初用 `caplog` 抓 stage_timer 的日志失败
- 原因：structlog 默认的 `PrintLoggerFactory` 直接写 stdout，绕过 Python `logging`
- 改为 `capsys`（抓 stdout），断言 `"stage_complete" in captured` 等字面匹配
- 副作用：测试更贴合用户实际看到的行为（structlog 输出的文本 "stage=asr elapsed_ms=15.0"）

### D2 — config.toml 默认值没动
- Plan §2 写"config.toml ASR device 默认改为 auto"
- 实际保留 `cuda`（便于现有已调通的 GPU 环境零配置跑）
- 用户想启用 auto：手改 `config.toml` 的 `[asr] device = "auto"` 一行即可
- main.py 里有 `logger.info("asr_device_selected", source="auto")` 记录决策，便于排查

### D3 — 没接 Prometheus
- Plan §1 就在非范围；此处复述：structlog 的结构化 JSON 输出可被 Loki/Vector 等 agent 采集，不需要 SDK
- 将来要 Prom/OTLP：`metrics.py` 里 log 之前加一个 hook 即可，不破坏现有调用点

### D4 — 继续不 push

---

## 行数

- 生产：metrics.py 50 + vram.py 50 + voice_pipeline.py +12 + tool_using.py +3 + main.py +8 = **~125 行**
- 测试：test_observability.py 110 行

与 plan §3 预算（生产 ~120 / 测试 ~90）接近。

---

## 整体 Phase 1 收官（S0-S4）

| Slice | 分支 | 生产 +行 | 测试 +行 | 关键交付 |
|---|---|---|---|---|
| S0 | slice-0-agent-abstraction | ~80 | 77 | AgentProvider Protocol + SimpleLLMAgent + R1/R2 fix |
| S1 | slice-1-pipeline-stages | ~66 | 103 | StreamingTagParser + VoicePipeline 接 agent_engine |
| S2 | slice-2-memory | ~140 | 180 | MemoryStore + SqliteConversation + agent 注入 |
| S3 | slice-3-tools | ~170 | 200 | Tool Protocol + Registry + ToolUsingAgent + get_time |
| S4 | slice-4-observability | ~125 | 110 | stage_timer + VRAM detect + ASR auto device |
| **合计** | — | **~581** | **~670** | 5 层 AgentProvider 栈就绪 |

**装配栈（运行时）：**
```
ToolUsingAgent(registry, base=
  SimpleLLMAgent(memory=SqliteConversationMemory, llm=
    OllamaLLM(gemma4:e4b)))
```

**测试：** 66 passed + 1 skipped（ollama 集成测试）

**残留问题（供 Phase 2）：**
- Hermes agent 接入（V5 §12）——S0 抽象已就位,直接新 `HermesAgentProvider` 替换 agent_engine
- 语义记忆（bge-m3）——S2 MVP 之外
- 前端 Live2D 动作/表情绑定——S1 前端类型已就位,绑定逻辑需 pixi-live2d-display motion API
- 多轮 ReAct 工具调用——S3 MVP 之外
