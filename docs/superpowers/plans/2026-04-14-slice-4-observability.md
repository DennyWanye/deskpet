# S4 — 可观测性 + VRAM 分级

**日期：** 2026-04-14
**分支：** `feat/slice-4-observability`

---

## 1. 范围

### ✅ 范围内
- `backend/observability/metrics.py`：`stage_timer(name, **ctx)` async context manager — 记录阶段耗时到 structlog
- `backend/observability/vram.py`：`detect_vram_gb()` + `recommend_asr_device(min_gb=4.0)` — 检测 GPU VRAM，返回 (device, compute_type)
- 集成：
  - `VoicePipeline._process_utterance`：ASR / Agent / TTS 三段用 `stage_timer` 包裹
  - `ToolUsingAgent`：工具调用耗时 + 触发率（log）
  - `FasterWhisperASR.__init__` 前：如果 config 的 `device` 是 "auto"，用 `recommend_asr_device()` 决定
- `config.toml` / `ASRConfig.device` 默认改为 `"auto"`（原 `"cuda"` 硬编码）
- 测试：metrics timer 发出 log / vram detect 带 fallback / recommend_asr_device 阈值逻辑

### ❌ 非范围
- Prometheus / OTLP 导出（MVP 只用 structlog 结构化 JSON 足够；外部收集器是独立部署 slice）
- LLM GPU VRAM 管理（ollama 自己管）
- TTS 分级（edge-tts 云端，不涉及本地 VRAM）

---

## 2. 设计要点

### metrics.stage_timer
```python
@asynccontextmanager
async def stage_timer(name: str, logger: BoundLogger, **ctx):
    start = time.monotonic()
    error: str | None = None
    try:
        yield
    except Exception as e:
        error = str(e)
        raise
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "stage_complete" if error is None else "stage_error",
            stage=name, elapsed_ms=round(elapsed_ms, 1),
            **ctx, **({"error": error} if error else {}),
        )
```

### vram.detect_vram_gb()
- 优先 `torch.cuda`：`torch.cuda.get_device_properties(0).total_memory` → GB
- Torch 未装 / 无 CUDA / 其它异常 → 返回 0.0
- 不 import 失败——纯 try/except 兜底

### vram.recommend_asr_device(min_gb=4.0)
- `detect_vram_gb() >= min_gb` → `("cuda", "float16")`
- 否则 → `("cpu", "int8")`（CTranslate2 推荐 int8 CPU）
- 返回 tuple；main.py 里根据 config 决定是否用

### ASRConfig 的 "auto" 约定
- `device="auto"`：构造 FasterWhisperASR 前查一次 VRAM，替换为实际值
- `device="cuda" / "cpu"`：显式指定，不动

---

## 3. 文件清单

### 新增
| 文件 | 估行 |
|---|---|
| `backend/observability/__init__.py` | 0 |
| `backend/observability/metrics.py` | ~40 |
| `backend/observability/vram.py` | ~40 |
| `backend/tests/test_observability.py` | ~90 |

### 修改
| `backend/main.py` | +12 / -2 | ASR "auto" 分支 + agent 耗时 log（如果放 main）|
| `backend/pipeline/voice_pipeline.py` | +20 / -0 | 三段 stage_timer |
| `backend/agent/providers/tool_using.py` | +8 / -2 | 工具耗时 log |
| `backend/config.py` | +0 / -0 | (不动默认,用户自己改 toml) |
| `backend/pyproject.toml` | packages += "observability" |

**预算：** 生产 ~120 行 / 测试 ~90 行。

---

## 4. 门控

- pytest 全绿（期望 ≥62 passed）
- 新测试：
  - stage_timer 成功路径发 `stage_complete` + 含 elapsed_ms
  - stage_timer 异常路径发 `stage_error` + 异常仍向外 raise
  - detect_vram_gb 无 torch / 无 cuda 时返回 0.0
  - recommend_asr_device 阈值行为（mock vram）
- import smoke
- 手动 sanity：ASR "auto" 路径 — 单元测试用 monkey-patch `recommend_asr_device`

---

## 5. 4 commits
1. `feat(backend): stage_timer + structured stage metrics`
2. `feat(backend): VRAM detection + ASR device auto-selection`
3. `feat(backend): instrument pipeline and tool calls with stage_timer`
4. `docs: add S4 plan + HANDOFF`
