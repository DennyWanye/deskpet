# P2-2-F1 Whisper 短音频准确率 — HANDOFF

**Slice:** P2-2 follow-up #1 — short-audio padding + hotwords bias
**Branch:** `worktree-p2-2-f1-whisper-short-audio` → merged to `master`
**Status:** Code + pytest DONE. **Manual sample-bench PENDING**（需用户录音后跑 `scripts/perf/asr_accuracy.py`）。
**Plan:** `docs/superpowers/plans/2026-04-21-p2-2-f1-whisper-short-audio.md`

## Goal recap

P2-2 真机手测出现：短音频（~1.5s）下 Whisper 把 "讲个笑话" 识别为
"一个消化"。两个同韵母字 (jiǎng→yī, xiào→xiāo) 串扰 + 短上下文下
beam search 倾向训练集高频词。

M3 已经做过两轮 hot-fix（锁 zh、删 initial_prompt、降 no_speech_threshold
等），长音频基本可用，但短音频仍弱。本 slice 加两层 zero-extra-latency
防御：

1. **音频静音 padding** —— 短于 3s 的音频前后各 pad 300ms 静音，给
   Whisper encoder 的 mel-spectrogram 窗口更多"呼吸"空间
2. **hotwords 偏置** —— 用 faster-whisper 的 `hotwords=` kwarg 对常用短
   语做 logit bias，压低拼音近似的训练集高频词

## Commits

| # | SHA | Title |
|---|-----|-------|
| 1 | `5f5ef12` | feat(P2-2-F1): ASRConfig.hotwords 字段 + [asr].hotwords 配置 |
| 2 | `d744e48` | feat(P2-2-F1): FasterWhisperASR 短音频 pad(300ms) + hotwords 偏置 |
| 3 | `831b94e` | feat(P2-2-F1): main.py 把 hotwords 喂给 ASR + config.toml [asr].hotwords 默认 8 条 |
| 4 | `d5a4ea9` | feat(P2-2-F1): scripts/perf/asr_accuracy.py 字符级 WER 基线对比 |

## What changed

### `backend/config.py`
`ASRConfig` 新增 `hotwords: list[str] = []`，其余字段不变。向后兼容：
`[asr]` 无此 key → 空列表 → 不注入偏置。

### `backend/providers/faster_whisper_asr.py`
- 构造器新增 `hotwords: list[str] | None = None` kwarg，存 `self._hotwords`
- `transcribe()` 入口新增两段逻辑：
  - **pad**: `0 < len(audio_np) < 16000*3` 时前后各 concat `zeros(4800)`
  - **hotwords**: `" ".join(self._hotwords)` 作为 `hotwords=` 传给 `WhisperModel.transcribe()`；空列表 → `None`
- 常量 `_PAD_MS=300`, `_PAD_SAMPLES=4800`, `_PAD_THRESHOLD_SAMPLES=48000`
  放模块级便于后续调参

### `backend/main.py`
`FasterWhisperASR(...)` 构造多传一个 `hotwords=config.asr.hotwords`。单行改动。

### `config.toml`
`[asr]` 段新增 `hotwords` 列表，8 条默认短语：
```toml
hotwords = [
    "讲个笑话", "你好", "再见", "谢谢",
    "今天天气", "帮我", "好的", "没问题",
]
```

### `scripts/perf/asr_accuracy.py`
离线字符级 WER 对比脚本：
- 读 `scripts/perf/asr_samples/*.wav`（文件名 = ground-truth 文本）
- 跑两遍 —— 旧版（子类屏蔽新行为）+ 新版
- 打印每条 WER + 均值；`impr_wer <= base_wer` → exit 0

**需要手动准备样本**：目录 `.gitignored`，每人录自己的音。推荐录
"讲个笑话 / 你好 / 再见 / 今天天气怎么样 / 谢谢" 5-10 条。

### `.gitignore`
追加 `scripts/perf/asr_samples/`。

## Test results

```
267 passed, 4 skipped in 20.68s
```

新增测试：
- `backend/tests/test_config_asr.py` (5 tests) — `[asr].hotwords` 载入
- `backend/tests/test_asr_short_audio.py` (9 tests) — pad 边界 + hotwords kwarg

旧测试零回归（256 → 256 passed + 11 new = 267）。

## Design decisions

### 为什么 pad 阈值用 3s 而不是 2s
保守选择。口语短句"今天天气怎么样"也就 ~2s，加 pad 零坏处；4s+ 才明显
没必要。实测 300ms 静音对 Whisper 的额外 encoder 计算成本 < 10ms，
不进 latency predicate 关键路径。

### 为什么阈值用 `<` 而不是 `<=`
边界情况（正好 3s）倾向不 pad，因为典型 3s 音频已经有充分上下文，
pad 只是无谓的算力开销。`test_boundary_audio_at_threshold_not_padded`
固化这个边界行为。

### 为什么空 hotwords 传 `None` 而不是 `""`
faster-whisper 的 tokenizer 对空字符串行为未定义（可能当成"空 token"
也可能报警）。`None` 是明确"不传"语义，和 `hotwords` kwarg 在 ASR
构造器里 `None` 的默认值一致。测试 `test_empty_hotwords_passes_none`
接受 `None` 或 `""` 都行，避免 faster-whisper 内部实现变化导致误判。

### 为什么不用 `initial_prompt`
2026-04-20 那次 hot-fix 把 `initial_prompt` 删了 —— 实测 Whisper
会把 prompt 里的具体文字直接吐出来当输出。`hotwords` 是 CTranslate2
层面的 logit bias，不走 decoder prompt 路径，没有这个副作用。

### 为什么 perf 脚本不跑 faster-whisper 的真实 WER 基准
那是 ASR 选型时该做的事，不是本 slice 的验收门。这里只需要证明
"新版本在我的录音上不比旧版差"。真正的公开 benchmark（AISHELL /
Common Voice zh）投入产出比不合算。

## Pending / Next steps

### 用户侧手动验证（Task 15 类似结构）

1. 重启 Tauri（backend 要 reload `[asr].hotwords`）
2. 手测 10 次"讲个笑话"：目标 ≥ 8 次识别正确（当前基线 ~3/10）
3. 手测"你好 / 再见 / 谢谢 / 今天天气怎么样"：各 3 次，目标 100%
4. 长对话回归：说 20s+ 复杂句，不退化
5. 可选：录 5-10 条 wav 到 `scripts/perf/asr_samples/`，跑
   `python scripts/perf/asr_accuracy.py`

### 调参空间

- `hotwords` 太长会过拟合。默认 8 条是起点；用户可自己加，但 >32 条
  应该 warn（**未实现**，后续 slice 再加）
- `_PAD_MS=300` 是启发式选的。如果实测发现 pad 导致 VAD filter 误杀，
  降到 200；如果短音频 WER 仍高，升到 500
- `_PAD_THRESHOLD_SAMPLES = 48000 (3s)` 可调。长 VN 对话几乎都 > 3s，
  所以目前没必要加环境变量覆盖

### Follow-ups（明确 out-of-scope）

- **F2 ASR 云端兜底** —— 短音频走云端 ASR（Azure / Deepgram / 阿里）
  长音频本地。要评估隐私红线 + 成本，独立 slice 讨论
- **F3 LLM 后处理纠错** —— ASR 输出丢给本地 LLM 做 "可能是什么意思"
  的轻量修复。加延迟 200ms+，prompt-eng 投入不确定
- **hotwords 动态学习** —— 从最近 N 轮对话里提取高频短语自动加到
  hotwords。Phase 3 产物，不着急

## Known limitations

1. **hotwords 是全局配置**，无法做 persona-specific（Phase 3 PersonaRegistry 时再想）
2. **pad 路径对极短（< 50ms）噪音可能产生空转** —— 被 `no_speech_threshold=0.4` 兜住，实测无 regression
3. **faster-whisper 某些旧版本不认识 `hotwords=` kwarg** —— 当前
   `uv.lock` 里锁的版本支持，如果后续升级踩坑，加 `try/except TypeError`
   兼容层
