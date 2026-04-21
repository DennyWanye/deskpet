# P2-2-F1 Whisper 短音频准确率 — Slice Plan

**Date**: 2026-04-21
**Parent sprint**: P2-2 (follow-up #1, non-milestone)
**Target branch**: `slice/p2-2-f1-whisper-short-audio` (worktree)
**Status**: DRAFT → ready for implementation

---

## 1. 问题陈述

P2-2 真机验收过程中发现短音频（< 2s）在 `faster-whisper large-v3-turbo` +
`language="zh"` 下仍然存在拼音近似幻觉：

| 用户说 | Whisper 输出 | 分析 |
|---|---|---|
| 讲个笑话 (jiǎng gè xiào huà, ~1.5s) | 一个消化 | j→y, xiào→xiāo 同韵母串扰 |

当前已做两轮 hot-fix（见 `backend/providers/faster_whisper_asr.py`）：

1. 2026-04-17: `language="zh"` 锁定 + `vad_filter=True`
2. 2026-04-20: 删 `initial_prompt`（prompt 文字被直接吐出）、
   `no_speech_threshold` 0.6 → 0.4、`condition_on_previous_text=False`、
   `temperature=0.0`

这些改动解决了长音频的"pt/es/fr drift"和"谢谢大家"幻觉，但**短音频 + 低
信息冗余**的情况下仍有问题：模型上下文窗口太短，beam search 偏向训练集
高频词（"消化" 是高频词，"笑话" 相对低频）。

### 非目标

- ❌ 改模型（`large-v3-turbo` → `large-v3`）—— 延迟不可接受，违反 P2-G1
- ❌ 引入后处理 LLM 纠错 —— 加 200ms+ 延迟，本 slice 范围外
- ❌ 切云端 ASR —— 隐私红线，本 slice 不动路由

## 2. 方案选择

六选二，按 **低成本 + 高确定性** 排序：

| 方案 | 代价 | 预估收益 | 本 slice 取舍 |
|---|---|---|---|
| **A. 音频静音 padding** | ~30 行 | 短音频上下文扩展，VAD 内部窗口更稳 | ✅ 做 |
| **B. 温和 initial_prompt（任务词）** | 需实验 | 不确定；上次删掉是因为副作用 | ❌ 跳过，风险大于收益 |
| **C. 切 large-v3（非 turbo）** | 延迟 +30-50% | 精度明显提升 | ❌ 违反 TTFT 门 |
| **D. `hotwords` 偏置** | ~50 行 + 配置段 | 对常用短语（笑话/再见/天气）显著提升 | ✅ 做 |
| **E. ASR 级联兜底（短音频走云端）** | ~200 行 + 隐私评审 | 最稳但违背 local_first | ❌ 放 follow-up F2 |
| **F. LLM 后处理纠错** | prompt-eng 投入高 | 不确定 | ❌ 放 follow-up F3 |

本 slice 做 **A + D**，配一个量化脚本证明改善。

## 3. 架构决策

### 3.1 音频 padding 放哪

`FasterWhisperASR.transcribe()` 入口，在 `audio_bytes → np.float32`
之后、`self._model.transcribe(...)` 之前：

```python
# P2-2-F1: short-audio safety padding
PAD_MS = 300  # 前后各 300ms 静音
pad_samples = int(16000 * PAD_MS / 1000)
if len(audio_np) < 16000 * 3:  # < 3s 的才 pad，长音频不动
    audio_np = np.concatenate([np.zeros(pad_samples, dtype=np.float32), audio_np, np.zeros(pad_samples, dtype=np.float32)])
```

**为什么阈值 3s**：长音频模型自身上下文充足，pad 只会浪费 VAD filter
运算。阈值用 3s 是保守选择，覆盖到绝大多数口语短句。

**为什么 300ms**：silero-vad 用 ~30-90ms 窗口；Whisper 的 mel-spectrogram
跨度 10ms/frame，300ms = 30 帧，给 encoder 足够"呼吸"空间而不会显著增
加延迟（300ms 静音对 VAD/ASR 几乎是零成本）。

### 3.2 hotwords 配置结构

新增 `[asr]` config 段（当前只有 `[vad]`，没有专门 `[asr]`）：

```toml
[asr]
# 热词偏置——对这些短语的 logit 加权，减少拼音近似幻觉
hotwords = [
    "讲个笑话",
    "你好",
    "再见",
    "谢谢",
    "今天天气",
    "帮我",
    "好的",
    "没问题",
]
```

`AppConfig.asr: ASRConfig` 新字段，loader 加 `if "asr" in raw`。

传到 Whisper：

```python
self._model.transcribe(
    audio_np,
    language="zh",
    ...,
    hotwords=" ".join(self._hotwords) if self._hotwords else None,
)
```

faster-whisper 的 `hotwords` 是**单字符串**（以空格分词 token 加权），
不是列表，所以要 join。

### 3.3 向后兼容

- `[asr]` 不存在 → `hotwords=None`，行为与当前完全一致
- `audio_bytes < PAD_MS*2*sample_width` 极短片段 → 仍走 pad 路径（不崩）
- 单元测试用 mock WhisperModel，不依赖 GPU

## 4. 文件改动清单

### 新增

| 路径 | 用途 |
|---|---|
| `backend/tests/test_asr_short_audio.py` | A + D 单元测试 |
| `scripts/perf/asr_accuracy.py` | 离线 WER 对比脚本（需要手测录音样本） |

### 修改

| 路径 | 变更 |
|---|---|
| `backend/providers/faster_whisper_asr.py` | 加 pad + hotwords 参数 |
| `backend/config.py` | `ASRConfig` dataclass + `AppConfig.asr` 字段 + loader |
| `config.toml` | 新增 `[asr]` 段 + 8 个默认 hotwords |
| `backend/main.py` | 构造 `FasterWhisperASR(hotwords=config.asr.hotwords)` |

### 不改

- `VoicePipeline`（ASR 是黑盒协议，没新 kwargs）
- 前端（用户不感知）
- `BargeInFilter` / `VoiceConfig`（与 M3 正交）

## 5. TDD 任务拆解

按 red → green → refactor 顺序：

### Task 1 — ASRConfig dataclass

**Red**: `test_config_asr.py`
- `test_asr_config_defaults_are_empty_hotwords`: `ASRConfig().hotwords == []`
- `test_app_config_has_asr_field`: `AppConfig().asr is not None`
- `test_load_config_without_asr_uses_defaults`
- `test_load_config_asr_reads_hotwords_list`

**Green**: 加 dataclass + loader 分支

### Task 2 — `FasterWhisperASR` 接受 hotwords

**Red**: `test_asr_short_audio.py::test_hotwords_param_passed_to_model`
- mock `WhisperModel.transcribe`，断言 `kwargs["hotwords"] == "讲个笑话 你好"`

**Red**: `test_asr_short_audio.py::test_hotwords_none_when_empty_list`
- 空 hotwords → `kwargs.get("hotwords") is None`

**Green**: 构造器加 `hotwords: list[str] | None = None`；transcribe
里 `hotwords=" ".join(self._hotwords) if self._hotwords else None`

### Task 3 — 短音频 padding

**Red**: `test_asr_short_audio.py::test_short_audio_is_padded`
- 传入 0.5s 音频（8000 samples），mock transcribe 捕获 `audio_np` 参数，
  断言长度 == 8000 + 2 * (0.3 * 16000) = 17600

**Red**: `test_asr_short_audio.py::test_long_audio_not_padded`
- 传入 4s 音频 → 长度不变

**Red**: `test_asr_short_audio.py::test_empty_audio_not_crashed`
- 传入 `b""` → 不崩，返回空串

**Green**: 加 conditional pad 逻辑

### Task 4 — main.py wiring

**Red**: 手测（或集成测试）—— 启动后日志里能看到
`faster-whisper loaded` 之后第一次 transcribe 的 `hotwords=...`

**Green**: `FasterWhisperASR(model=..., hotwords=config.asr.hotwords)`

### Task 5 — perf script

`scripts/perf/asr_accuracy.py`:
- 从 `scripts/perf/asr_samples/` 读 .wav 文件（名字即 ground-truth，如
  `讲个笑话.wav`）
- 跑两遍：一次走旧版 ASR（disable pad + hotwords）、一次走新版
- 打印每条的 transcribe 结果 + 字符级 WER
- Exit 0 = 新版 WER <= 旧版 WER

**注**：录音样本文件不进 git（太大且每人录音不同），脚本找不到目录就
退 0 + skip 提示。

## 6. 验收标准

**硬性**：
1. `cd backend && pytest` 全绿（新增 ~8 tests）
2. `config.toml` 无 `[asr]` 段时行为不变（回归）
3. `faster-whisper` 仍然只接受它支持的 kwargs（CTranslate2 版本对得上）

**软性**（手测）：
1. 说"讲个笑话"10 次，≥ 8 次识别为"讲个笑话"或"讲笑话"（当前 ~3/10）
2. 说"你好"5 次，5 次都识别为"你好"
3. 长对话（20s+）不退化

## 7. 风险

| 风险 | 缓解 |
|---|---|
| faster-whisper 版本不支持 `hotwords` kwarg | 加兼容层：`try/except TypeError`，不支持就退化 |
| pad 300ms 让某些边缘音频的 VAD filter 误判为无语音 | `no_speech_threshold=0.4` 已经降过，再观察；如果误杀上调到 0.5 |
| hotwords 太长 → 模型过拟合到词表 | 默认 8 个，用户可扩；warn 超过 32 个 |

## 8. Commit 策略

按 task 拆，每个 task 一 commit：

1. `test(P2-2-F1): ASRConfig dataclass` (red)
2. `feat(P2-2-F1): ASRConfig + [asr] config 段` (green)
3. `test(P2-2-F1): FasterWhisperASR hotwords + padding 单测` (red)
4. `feat(P2-2-F1): FasterWhisperASR 短音频 pad + hotwords 偏置` (green)
5. `feat(P2-2-F1): main.py 把 hotwords 喂给 FasterWhisperASR`
6. `feat(P2-2-F1): scripts/perf/asr_accuracy.py 准确率对比`
7. `docs(P2-2-F1): handoff + STATE 更新`

最终 merge 回 master（fast-forward），tag 不需要（非 milestone）。

## 9. 参考

- `docs/superpowers/handoffs/p2-2-m3.md` —— M3 handoff 作为 slice 结构模板
- `backend/providers/faster_whisper_asr.py` L52-63 —— 已有 hot-fix 注释
- faster-whisper docs: https://github.com/SYSTRAN/faster-whisper#transcribe

---

**Plan status**: SIGNED-OFF for implementation（2026-04-21）
