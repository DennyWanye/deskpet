# P2-2 实时双工语音架构方案

**版本：** v5.0（最终执行版 — 4 轮挑战修订后）  
**日期：** 2026-04-16  
**作者：** 20 年架构师 × 30 年架构师审查（3 轮） × 40 年架构师签核  
**状态：** APPROVED FOR EXECUTION  
**对标路线图：** `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md` §3.3  

---

## 1. 现状分析

### 1.1 当前架构（半双工）

```
用户点击麦克风 → 录音 → 点停止 → 发送 PCM → 后端 VAD+ASR → Agent → TTS → 
前端缓冲全部 MP3 → tts_end → 一次性解码播放
```

**关键文件映射：**

| 层 | 文件 | 职责 |
|---|---|---|
| 前端录音 | `useAudioRecorder.ts` | 手动开关麦克风，`createScriptProcessor` 重采样到 16kHz PCM16，512 样本/帧 |
| 前端播放 | `useAudioPlayer.ts` | 缓冲所有 MP3 chunk，`tts_end` 时合并→`decodeAudioData`→一次性播放 |
| 传输 | `AudioChannel.ts` | 单条 WebSocket，binary(PCM上行/MP3下行) + JSON 混合 |
| 后端管线 | `voice_pipeline.py` | VAD→ASR→Agent→TTS 串行，有 `interrupt()` 但前端未自动触发 |
| 后端 WS | `main.py:audio_channel` | `while True: data = await ws.receive_bytes(); await pipeline.process_audio_chunk(data, ws)` |
| VAD | `silero_vad.py` | Silero v5，16kHz，512 样本帧，250ms 最小语音/500ms 最小静音 |
| TTS | `edge_tts_provider.py` | Edge TTS，MP3 流式输出，4KB 缓冲 |

### 1.2 核心瓶颈

| # | 瓶颈 | 影响 |
|---|---|---|
| B1 | **全量缓冲播放** — `decodeAudioData` 需要完整 MP3 流 | TTS 延迟 = 全部合成完才出声 |
| B2 | **手动麦克风** — 用户要手动点击开/关 | 无法实现"用户开口即打断" |
| B3 | **deprecated API** — `createScriptProcessor` 在主线程处理音频 | 高负载时掉帧 |
| B4 | **MP3 格式局限** — MP3 不支持帧级随机访问 | 无法做精确 fade-out |

### 1.3 并发模型验证（v5.0 新增）

> **40 年架构师签核补丁：** 确认 TTS 期间 VAD 帧可以被并发处理。

当前 `main.py:audio_channel` 的主循环：
```python
while True:
    data = await ws.receive_bytes()      # await 让出
    await pipeline.process_audio_chunk(data, ws)  # VAD 同步处理
```

`process_audio_chunk` 中 `speech_end` 触发 `asyncio.create_task(_process_utterance(...))`，TTS 在独立 Task 中运行。主循环继续 `receive_bytes → VAD`。因为：
1. `_process_utterance` 内的 `async for audio_chunk in self.tts.synthesize_stream()` 每个 chunk 有 `await`
2. `await audio_ws.send_bytes()` 也是 `await`
3. edge-tts 内部用 `async for` yield

**结论：事件循环天然在 TTS chunk 间让出控制权，主循环的 VAD 处理可以穿插执行。不需要额外拆 Task。** M1 开始前的 spike 简化为一个 10 分钟的 `asyncio.sleep(0)` 插入验证。

---

## 2. 目标

| 目标 | 描述 | 验收指标 | 备注 |
|---|---|---|---|
| G1 | 用户开口 → TTS 合成中断 | p95 < 200ms（VAD 确认 speech_start → interrupt 执行） | 端到端感知 = min_speech_ms + G1 |
| G2 | TTS 流式播放，首 PCM chunk 到发声 | < 500ms（不含 edge-tts TTFB 300-800ms） | edge-tts TTFB 不可控，是上游限制 |
| G3 | 回声消除 | 误触率 < 5%（耳机），< 15%（扬声器） | |
| G4 | 麦克风常开 | 用户 opt-in 后，连接即录音 | 默认关闭 |
| G5 | 平稳打断体验 | fade-out 50ms，无爆音 | |

---

## 3. 三 Milestone 渐进实施

> **核心策略：** WebSocket 极致化，3 个递进 milestone，每个可独立交付。

### 3.1 Milestone 1：服务端 VAD 打断 + 麦克风常开（3-4 天）

**核心价值：中断后端 TTS 合成**（2-5s 过程），前端停止已缓冲播放。

**改动清单：**

| 文件 | 改动 |
|---|---|
| `voice_pipeline.py` | TTS 期间 VAD 持续监听（已天然支持，但需加 `tts_barge_in` 消息发送）；binary frame 加 `0x02` type byte |
| `useAudioRecorder.ts` | `createScriptProcessor` → AudioWorklet（Blob URL 内联 processor 代码，解决 WebView2 路径问题）；新增 always-on 模式 |
| `useAudioPlayer.ts` | 新增 `bargeIn()`：50ms gain ramp-down → `stop()` |
| `AudioChannel.ts` | binary frame type header 解析（内聚，listener 不感知）；`tts_barge_in` 加入 AudioMessage |
| `App.tsx` | autoVoice → 自动 startRecording；监听 `tts_barge_in` |
| `SettingsPanel.tsx` | "自动语音模式" 开关 |

### 3.2 Milestone 2：PCM 流式播放（3-4 天）

**核心价值：消除全量缓冲延迟**，TTS 边合成边播放。

| 文件 | 改动 |
|---|---|
| `edge_tts_provider.py` | 新增 `synthesize_pcm_stream()`：ffmpeg pipe MP3→PCM16 24kHz，yield 4096-sample chunks |
| `voice_pipeline.py` | TTS 阶段切 `synthesize_pcm_stream()`，frame type 改 `0x01` |
| `useAudioPlayer.ts` | **重写**：Jitter Buffer(2-chunk) + AudioBufferSourceNode 排队播放 |

> **ffmpeg 分发策略（v5.0 补丁）：** 开发阶段假设 PATH 有 ffmpeg。Tauri 打包分发在 P2-0 release 前解决（bundle ffmpeg.exe 到 resources 目录或用 Rust 侧 symphonia crate 解码）。M2 不阻塞。

### 3.3 Milestone 3：回声抑制 + 验收（2-3 天）

**三层回声防线（纯时域）：**

| 层 | 机制 |
|---|---|
| L1 | 系统 AEC（`echoCancellation: true`） |
| L2 | 时域状态机 `IDLE→TTS_PLAYING→COOLDOWN(300ms)→IDLE`，TTS 期间 min_speech_ms 提高到 400ms |
| L3 | VAD 阈值 0.5→0.65（TTS 期间） |

### 3.4 打断延迟分析

| 场景 | min_speech_ms | pipeline | 端到端感知 |
|---|---|---|---|
| 耳机 + TTS 播放 | 250ms | ~50ms | ~300ms |
| 扬声器 + TTS 播放 | 400ms | ~50ms | ~450ms |

G1 硬指标（pipeline 部分 p95 < 200ms）可达。端到端分设备档位验收。

### 3.5 edge-tts TTFB 约束

edge-tts TTFB 300-800ms 不可控。G2 测量架构延迟（PCM chunk 到达→出声 ~340ms）。长期通过 CosyVoice 2 本地推理消除。

---

## 4. 砍掉的内容（YAGNI）

| 项 | 原因 |
|---|---|
| WebRTC (aiortc) | 无 AEC3、低维护、Windows 差 |
| Phoneme 口型 | Live2D 模型无 viseme |
| StreamingASR | faster-whisper 批量在 <30s 够用 |
| MSE 播放 | Chromium MSE 不支持 `audio/mpeg` |
| 能量域回声 | 不可调 |
| minimp3 | 生态差，ffmpeg pipe 可靠 |

---

## 5. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| PCM 滚动播放 glitch | 中 | 中 | Jitter buffer + gap 容忍窗口 |
| 扬声器回声误报 | 高 | 中 | 三层防线；最坏推荐耳机 |
| AudioWorklet WebView2 路径 | 中 | 低 | Blob URL 内联 |
| ffmpeg 子进程启动慢 | 低 | 中 | 常驻 1 个进程 |
| edge-tts TTFB 波动 | 高 | 中 | 不可控；CosyVoice 2 长期方案 |
| ffmpeg 分发（Tauri 打包） | 确定 | 低 | P2-0 release 前解决，不阻塞 M2 |

---

## 6. 接口契约

### 6.1 新增 WebSocket 消息

```json
{ "type": "tts_barge_in", "payload": { "reason": "vad_speech_detected" } }
```

### 6.2 Binary Frame 格式（M1 起生效）

```
字节 0: 0x01=PCM16(24kHz,mono,LE)  0x02=MP3
字节 1..N: audio data
```

### 6.3 配置项

```toml
[voice]
always_on_mic = false
vad_threshold_normal = 0.5
vad_threshold_during_tts = 0.65
min_speech_ms_normal = 250
min_speech_ms_during_tts = 400
tts_cooldown_ms = 300
```

---

## 7. 未来演进（不在 scope）

| 方向 | 触发条件 |
|---|---|
| WebRTC (libwebrtc) | 远程访问需求 |
| 流式 ASR (sherpa-onnx) | 长语句延迟瓶颈 |
| CosyVoice 2 | 自然中文语音 / 消除 TTFB |
| Phoneme 口型 | 自研 Live2D 模型 |

---

## 变更日志

| 版本 | 变更 |
|---|---|
| v1.0 | 初稿：WebSocket + WebRTC 两阶段 |
| v2.0 | 第 1 轮挑战：砍 WebRTC/aiortc/phoneme/MSE |
| v3.0 | 第 2 轮挑战：+Jitter Buffer，砍能量域→时域状态机，M1=后端 TTS 中断，+隐私设计 |
| v4.0 | 第 3 轮挑战：ffmpeg 替代 minimp3，M1 引入 type header 前向兼容，打断延迟重定义，TTFB 分析 |
| v5.0 | 第 4 轮签核：+并发模型验证（asyncio Task 天然支持 VAD||TTS），+ffmpeg 分发策略，+AudioWorklet Blob URL 内联明确 |
