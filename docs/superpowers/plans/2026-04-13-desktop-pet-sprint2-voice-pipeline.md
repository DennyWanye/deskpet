# Desktop Pet Sprint 2: Voice Pipeline (全本地部署)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现完整的语音交互管线 — 用户说话 → VAD 检测 → ASR 转文字 → LLM 生成回复 → TTS 合成语音 → 播放音频 + Live2D 口型同步。全部模型本地部署在 4090 48GB 上。

**Architecture:** 前端通过 getUserMedia 录制麦克风音频，经 `/ws/audio` WebSocket 发送 16kHz PCM 帧到后端。后端 VAD → ASR → LLM → TTS 流式处理，TTS 音频流式回传前端播放，同时口型参数通过 `/ws/control` 发送到 Live2D。

**Tech Stack:**
- **VAD:** silero-vad v5 (PyTorch Hub, ~2MB, CPU)
- **ASR:** faster-whisper large-v3-turbo (HuggingFace auto-download, CUDA FP16, ~1.5GB VRAM)
- **TTS:** CosyVoice 2 (ModelScope, ~2GB VRAM)
- **Hardware:** NVIDIA 4090 48GB VRAM, 64GB RAM

---

## 模型资产获取

> **注意:** `backend/assets/` 与 `backend/temp/` 已在 `.gitignore` 中被忽略（单目录可达 GB 量级，不进版本库）。新机器 clone 仓库后需要按下面步骤补齐模型。

**目录约定:**
```
backend/
├── assets/
│   ├── faster-whisper-large-v3-turbo/   # ASR 主模型 (~2.7GB)
│   ├── cosyvoice2/                       # 预留给未来本地 TTS (~5.3GB, 可选)
│   └── ...                               # silero-vad 由 torch.hub 缓存至 ~/.cache/torch/hub
└── temp/                                 # TTS/ASR 运行时产物
```

### 1. Silero VAD（自动，无需人工下载）
首次 `import silero_vad` 时 torch.hub 会从 GitHub 自动拉取 (~2MB)，缓存在 `~/.cache/torch/hub/snakers4_silero-vad_master/`。离线环境可预先 `git clone https://github.com/snakers4/silero-vad` 到该路径。

```bash
uv pip install torch torchaudio  # GPU 版本用 --extra-index-url https://download.pytorch.org/whl/cu121
```

### 2. faster-whisper (ASR, 必需)
默认从 HuggingFace 下载 `Systran/faster-whisper-large-v3-turbo`（~1.5GB CT2 权重，FP16 ~2.7GB 展开）。

**推荐：离线放置 + `model_dir` 指定路径**
```bash
# 方法 A: huggingface-cli
pip install huggingface_hub
huggingface-cli download Systran/faster-whisper-large-v3-turbo \
    --local-dir backend/assets/faster-whisper-large-v3-turbo

# 方法 B: ModelScope 镜像（国内网络更稳）
pip install modelscope
modelscope download --model keepitsimple/faster-whisper-large-v3-turbo \
    --local-dir backend/assets/faster-whisper-large-v3-turbo
```

然后在 `config.toml` 中把 `[asr].model_dir` 指向该路径即可（留空则走默认 HF 缓存 `~/.cache/huggingface/`）。

### 3. TTS
**当前默认：edge-tts（在线、无需权重）**
```toml
[tts]
provider = "edge-tts"
voice = "zh-CN-XiaoyiNeural"   # 可选 XiaoxiaoNeural / YunxiNeural 等
```
依赖 Microsoft Edge 在线合成服务，首次运行 `pip install edge-tts` 即可；无模型权重需要下载。

**未来升级：CosyVoice 2（本地，离线可用）**
```bash
pip install modelscope
modelscope download --model iic/CosyVoice2-0.5B \
    --local-dir backend/assets/cosyvoice2
```
切换时把 `[tts].provider` 改为 `cosyvoice2` 并实现对应 provider。

---

## 新增文件结构

```
backend/
├── providers/
│   ├── silero_vad.py          # Task 1: VAD provider
│   ├── faster_whisper_asr.py  # Task 2: ASR provider
│   └── cosyvoice_tts.py       # Task 3: TTS provider
├── pipeline/
│   ├── __init__.py
│   └── voice_pipeline.py      # Task 4: VAD→ASR→LLM→TTS 编排
├── main.py                    # 修改: 集成语音管线到 /ws/audio

tauri-app/src/
├── ws/
│   └── AudioChannel.ts        # Task 5: 音频 WebSocket 客户端
├── hooks/
│   ├── useWebSocket.ts        # 修改: 导出 useAudioChannel
│   └── useAudioRecorder.ts    # Task 5: 麦克风录制 hook
├── types/
│   └── messages.ts            # 修改: 新增音频相关消息类型
├── components/
│   └── Live2DCanvas.tsx       # Task 6: 新增口型同步参数接收
└── App.tsx                    # 修改: 集成语音 UI 控件
```

---

## Task 1: VAD — silero-vad 语音活动检测

**目标:** 在后端接收音频流时，实时检测语音起止，只把包含语音的片段传给 ASR。

### 步骤

- [ ] **1.1** 安装依赖
  ```bash
  cd G:/projects/deskpet/backend
  uv add torch torchaudio --extra-index-url https://download.pytorch.org/whl/cu121
  ```
  > 注意: 如果已安装 PyTorch CUDA 版本，跳过此步。

- [ ] **1.2** 创建 `backend/providers/silero_vad.py`
  ```python
  """silero-vad v5 — 流式语音活动检测"""
  from __future__ import annotations
  import torch
  import torchaudio
  import structlog

  logger = structlog.get_logger()

  class SileroVAD:
      """
      接收 16kHz int16 PCM 帧 (512 samples = 32ms)，
      输出 speech_start / speech_end 事件。
      """
      def __init__(self, threshold: float = 0.5, min_speech_ms: int = 250, min_silence_ms: int = 500):
          self.threshold = threshold
          self.min_speech_ms = min_speech_ms
          self.min_silence_ms = min_silence_ms
          self._model = None
          self._reset_state()

      def _reset_state(self):
          self._is_speech = False
          self._speech_start_ms = 0
          self._silence_start_ms = 0
          self._audio_buffer = bytearray()
          self._ms_counter = 0

      async def load(self):
          """加载模型 (首次调用时)"""
          if self._model is not None:
              return
          logger.info("loading silero-vad model")
          self._model, utils = torch.hub.load(
              'snakers4/silero-vad', 'silero_vad', trust_repo=True
          )
          self._model.eval()
          logger.info("silero-vad loaded")

      def reset(self):
          """重置状态 (新一轮对话)"""
          self._reset_state()
          if self._model is not None:
              self._model.reset_states()

      def process_chunk(self, pcm_bytes: bytes) -> list[dict]:
          """
          处理一帧 PCM16 音频 (512 samples, 32ms, 16kHz).
          返回事件列表: [{"event": "speech_start"}, {"event": "speech_end", "audio": bytes}]
          """
          events = []
          chunk_ms = len(pcm_bytes) / 2 / 16000 * 1000  # bytes / 2(int16) / sr * 1000

          # Convert to float tensor for VAD
          audio_int16 = torch.frombuffer(pcm_bytes, dtype=torch.int16).float() / 32768.0
          prob = self._model(audio_int16, 16000).item()

          if prob >= self.threshold:
              if not self._is_speech:
                  self._speech_start_ms = self._ms_counter
                  self._is_speech = True
                  self._audio_buffer = bytearray()
                  events.append({"event": "speech_start"})
              self._silence_start_ms = 0
              self._audio_buffer.extend(pcm_bytes)
          else:
              if self._is_speech:
                  if self._silence_start_ms == 0:
                      self._silence_start_ms = self._ms_counter
                  self._audio_buffer.extend(pcm_bytes)
                  silence_duration = self._ms_counter - self._silence_start_ms
                  speech_duration = self._ms_counter - self._speech_start_ms
                  if silence_duration >= self.min_silence_ms and speech_duration >= self.min_speech_ms:
                      events.append({
                          "event": "speech_end",
                          "audio": bytes(self._audio_buffer),
                      })
                      self._is_speech = False
                      self._audio_buffer = bytearray()
                      self._silence_start_ms = 0

          self._ms_counter += chunk_ms
          return events
  ```

- [ ] **1.3** 添加 VAD 配置到 `config.py`
  ```python
  @dataclass
  class VADConfig:
      threshold: float = 0.5
      min_speech_ms: int = 250
      min_silence_ms: int = 500
  ```

- [ ] **1.4** 更新 `config.toml` 添加 `[vad]` section

- [ ] **1.5** 编写测试 `tests/test_vad.py` — 用合成正弦波 + 静音验证 speech_start/speech_end 事件

---

## Task 2: ASR — faster-whisper 语音识别

**目标:** 接收 VAD 切出的音频段，转录为文字。

### 步骤

- [ ] **2.1** 安装依赖
  ```bash
  cd G:/projects/deskpet/backend
  uv add faster-whisper
  ```

- [ ] **2.2** 创建 `backend/providers/faster_whisper_asr.py`
  ```python
  """faster-whisper ASR provider — 本地 CUDA 推理"""
  from __future__ import annotations
  import io
  import numpy as np
  import structlog
  from faster_whisper import WhisperModel

  logger = structlog.get_logger()

  class FasterWhisperASR:
      """
      实现 ASRProvider protocol.
      加载 large-v3-turbo 模型到 CUDA FP16.
      """
      def __init__(self, model: str = "large-v3-turbo", device: str = "cuda",
                   compute_type: str = "float16"):
          self.model_name = model
          self.device = device
          self.compute_type = compute_type
          self._model: WhisperModel | None = None

      async def load(self):
          if self._model is not None:
              return
          logger.info("loading faster-whisper", model=self.model_name, device=self.device)
          self._model = WhisperModel(
              self.model_name,
              device=self.device,
              compute_type=self.compute_type,
          )
          logger.info("faster-whisper loaded")

      async def transcribe(self, audio_bytes: bytes) -> str:
          """
          接收 16kHz int16 PCM bytes, 返回转录文本.
          """
          if self._model is None:
              await self.load()

          # Convert PCM16 to float32 numpy array
          audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

          segments, info = self._model.transcribe(
              audio_np,
              language=None,  # 自动检测语言
              beam_size=5,
              vad_filter=False,  # 我们已经有 silero-vad
          )

          text = " ".join(seg.text.strip() for seg in segments)
          logger.info("asr_result", text=text, language=info.language,
                      duration=f"{info.duration:.1f}s")
          return text
  ```

- [ ] **2.3** 注册到 ServiceContext — 在 `main.py` 的启动逻辑中
  ```python
  from providers.faster_whisper_asr import FasterWhisperASR
  asr = FasterWhisperASR(
      model=config.asr.model,
      device=config.asr.device,
      compute_type=config.asr.compute_type,
  )
  service_context.register("asr_engine", asr)
  ```

- [ ] **2.4** 编写测试 `tests/test_asr.py` — 用预录音频文件验证转录结果

---

## Task 3: TTS — CosyVoice 2 语音合成

**目标:** 接收文本，流式生成语音 PCM 并返回。

### 步骤

- [ ] **3.1** 安装 CosyVoice 2
  ```bash
  cd G:/projects/deskpet/backend
  uv add cosyvoice2
  # 如果 pip 版本不可用，使用 ModelScope:
  # uv add modelscope
  # python -c "from modelscope import snapshot_download; snapshot_download('iic/CosyVoice2-0.5B', local_dir='./assets/cosyvoice2')"
  ```

- [ ] **3.2** 创建 `backend/providers/cosyvoice_tts.py`
  ```python
  """CosyVoice 2 TTS provider — 本地 CUDA 推理，流式输出"""
  from __future__ import annotations
  import io
  import struct
  from typing import AsyncIterator
  import numpy as np
  import structlog

  logger = structlog.get_logger()

  class CosyVoiceTTS:
      """
      实现 TTSProvider protocol.
      使用 CosyVoice2-0.5B 模型, 支持流式合成.
      输出: 22050Hz int16 PCM (CosyVoice 默认采样率)
      """
      def __init__(self, model_dir: str = "./assets/cosyvoice2"):
          self.model_dir = model_dir
          self._model = None

      async def load(self):
          if self._model is not None:
              return
          logger.info("loading cosyvoice2", model_dir=self.model_dir)
          from cosyvoice2 import CosyVoice2
          self._model = CosyVoice2(self.model_dir)
          logger.info("cosyvoice2 loaded")

      async def synthesize(self, text: str) -> bytes:
          """合成完整音频, 返回 22050Hz int16 PCM bytes."""
          if self._model is None:
              await self.load()

          chunks = []
          for chunk in self._model.inference_sft(text, "中文女"):
              audio_np = chunk["tts_speech"].numpy().flatten()
              pcm = (audio_np * 32767).astype(np.int16).tobytes()
              chunks.append(pcm)
          return b"".join(chunks)

      async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
          """流式合成, 每个 chunk 是一段 22050Hz int16 PCM."""
          if self._model is None:
              await self.load()

          for chunk in self._model.inference_sft(text, "中文女"):
              audio_np = chunk["tts_speech"].numpy().flatten()
              pcm = (audio_np * 32767).astype(np.int16).tobytes()
              yield pcm
  ```

- [ ] **3.3** 注册到 ServiceContext
  ```python
  from providers.cosyvoice_tts import CosyVoiceTTS
  tts = CosyVoiceTTS(model_dir=config.tts.model_dir)
  service_context.register("tts_engine", tts)
  ```

- [ ] **3.4** 更新 TTSProvider protocol — 添加 `async def load(self)` 方法到 base.py

- [ ] **3.5** 编写测试 `tests/test_tts.py` — 验证输出是有效 PCM 音频

---

## Task 4: Voice Pipeline — 语音管线编排

**目标:** 将 VAD → ASR → LLM → TTS 串联为完整管线，处理 `/ws/audio` 上的音频流。

### 步骤

- [ ] **4.1** 创建 `backend/pipeline/__init__.py` (空文件)

- [ ] **4.2** 创建 `backend/pipeline/voice_pipeline.py`
  ```python
  """语音管线: VAD → ASR → LLM → TTS, 全流式处理"""
  from __future__ import annotations
  import asyncio
  from typing import AsyncIterator
  import structlog
  from fastapi import WebSocket

  logger = structlog.get_logger()

  class VoicePipeline:
      """
      管理单个 WebSocket 会话的语音处理流程.

      音频流入 → VAD 检测语音段 → ASR 转录 → LLM 生成回复 → TTS 合成
      → 音频流式回传 + 口型参数发送到 control channel

      生命周期: 每个 audio WebSocket 连接创建一个实例.
      """
      def __init__(self, vad, asr, llm, tts, control_ws: WebSocket | None = None):
          self.vad = vad
          self.asr = asr
          self.llm = llm
          self.tts = tts
          self.control_ws = control_ws  # 用于发送口型参数
          self._interrupted = False
          self._processing = False

      def interrupt(self):
          """用户打断当前合成"""
          self._interrupted = True

      async def process_audio_chunk(self, pcm_bytes: bytes, audio_ws: WebSocket):
          """
          处理一帧音频, 驱动整条管线.

          流程:
          1. VAD 检测 speech_start / speech_end
          2. speech_end → ASR 转录
          3. 转录文本 → LLM 生成回复 (流式)
          4. LLM 回复 → TTS 合成 (流式)
          5. TTS 音频通过 audio_ws 发回前端
          6. 口型参数通过 control_ws 发送
          """
          events = self.vad.process_chunk(pcm_bytes)

          for event in events:
              if event["event"] == "speech_start":
                  # 通知前端: 检测到用户说话
                  await audio_ws.send_json({
                      "type": "vad_event",
                      "payload": {"status": "speech_start"},
                  })
                  # 如果正在播放 TTS，打断
                  if self._processing:
                      self.interrupt()

              elif event["event"] == "speech_end":
                  speech_audio = event["audio"]
                  await audio_ws.send_json({
                      "type": "vad_event",
                      "payload": {"status": "speech_end"},
                  })
                  # 启动异步处理
                  asyncio.create_task(
                      self._process_utterance(speech_audio, audio_ws)
                  )

      async def _process_utterance(self, audio_bytes: bytes, audio_ws: WebSocket):
          """处理一段完整的语音: ASR → LLM → TTS"""
          self._interrupted = False
          self._processing = True

          try:
              # Step 1: ASR
              text = await self.asr.transcribe(audio_bytes)
              if not text.strip():
                  return

              logger.info("user_said", text=text)
              await audio_ws.send_json({
                  "type": "transcript",
                  "payload": {"text": text, "role": "user"},
              })

              # Step 2: LLM (流式)
              response_text = ""
              messages = [{"role": "user", "content": text}]
              async for token in self.llm.chat_stream(messages):
                  if self._interrupted:
                      logger.info("llm_interrupted")
                      break
                  response_text += token

              if self._interrupted or not response_text.strip():
                  return

              logger.info("llm_response", text=response_text[:100])
              await audio_ws.send_json({
                  "type": "transcript",
                  "payload": {"text": response_text, "role": "assistant"},
              })

              # Step 3: TTS (流式合成 + 流式发送)
              chunk_index = 0
              async for pcm_chunk in self.tts.synthesize_stream(response_text):
                  if self._interrupted:
                      logger.info("tts_interrupted")
                      break
                  # 发送音频数据 (binary frame)
                  await audio_ws.send_bytes(pcm_chunk)
                  # 发送口型参数到 control channel
                  if self.control_ws:
                      try:
                          await self.control_ws.send_json({
                              "type": "lip_sync",
                              "payload": {
                                  "chunk_index": chunk_index,
                                  "amplitude": _calc_amplitude(pcm_chunk),
                              },
                          })
                      except Exception:
                          pass  # control channel 可能已断开
                  chunk_index += 1

              # TTS 结束标记
              await audio_ws.send_json({
                  "type": "tts_end",
                  "payload": {},
              })

          except Exception as e:
              logger.error("pipeline_error", error=str(e))
              await audio_ws.send_json({
                  "type": "error",
                  "payload": {"message": str(e)},
              })
          finally:
              self._processing = False

  def _calc_amplitude(pcm_bytes: bytes) -> float:
      """计算 PCM 音频块的 RMS 振幅 (0.0 ~ 1.0), 用于口型同步"""
      import numpy as np
      audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
      rms = np.sqrt(np.mean(audio ** 2)) / 32768.0
      return min(1.0, rms * 3.0)  # 放大以获得更明显的口型
  ```

- [ ] **4.3** 重写 `main.py` 中的 `/ws/audio` endpoint
  ```python
  # 在 main.py 中:

  # 全局: 跟踪每个连接的 control_ws, 用于语音管线发送口型参数
  _control_connections: dict[str, WebSocket] = {}

  @app.websocket("/ws/audio")
  async def audio_channel(ws: WebSocket):
      await ws.accept()
      if not _validate_secret(ws):
          await ws.close(code=4001, reason="invalid secret")
          return

      # 获取关联的 control channel (通过 session_id query param)
      session_id = ws.query_params.get("session_id", "default")
      control_ws = _control_connections.get(session_id)

      # 初始化语音管线
      vad = SileroVAD(
          threshold=config.vad.threshold,
          min_speech_ms=config.vad.min_speech_ms,
          min_silence_ms=config.vad.min_silence_ms,
      )
      await vad.load()

      pipeline = VoicePipeline(
          vad=vad,
          asr=service_context.asr_engine,
          llm=service_context.llm_engine,
          tts=service_context.tts_engine,
          control_ws=control_ws,
      )

      try:
          while True:
              data = await ws.receive_bytes()
              await pipeline.process_audio_chunk(data, ws)
      except WebSocketDisconnect:
          logger.info("audio channel disconnected")
  ```

- [ ] **4.4** 修改 `/ws/control` endpoint — 注册到 `_control_connections` 字典

- [ ] **4.5** 添加模型预加载到 FastAPI lifespan
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      # 启动时预加载模型
      logger.info("preloading models...")
      if service_context.asr_engine:
          await service_context.asr_engine.load()
      if service_context.tts_engine:
          await service_context.tts_engine.load()
      logger.info("models loaded, server ready")
      yield
      # 关闭时清理
      logger.info("shutting down")

  app = FastAPI(title="Desktop Pet Backend", version="0.2.0", lifespan=lifespan)
  ```

- [ ] **4.6** 编写集成测试 `tests/test_pipeline.py` — Mock 各 provider，验证事件流

---

## Task 5: Frontend — 麦克风录制 + 音频播放

**目标:** 前端通过 getUserMedia 录制麦克风音频，发送到后端；接收 TTS 音频并播放。

### 步骤

- [ ] **5.1** 创建 `tauri-app/src/ws/AudioChannel.ts`
  ```typescript
  /**
   * 音频 WebSocket 通道.
   * 发送: PCM16 binary frames (麦克风录音)
   * 接收: PCM16 binary frames (TTS 音频) + JSON 控制消息
   */
  export class AudioChannel {
    private ws: WebSocket | null = null;
    private url: string;
    private secret: string;
    private binaryListeners = new Set<(data: ArrayBuffer) => void>();
    private jsonListeners = new Set<(msg: AudioMessage) => void>();

    constructor(port: number = 8100, secret: string = "") {
      this.url = `ws://127.0.0.1:${port}/ws/audio`;
      this.secret = secret;
    }

    connect(sessionId: string = "default") {
      const wsUrl = `${this.url}?secret=${encodeURIComponent(this.secret)}&session_id=${sessionId}`;
      this.ws = new WebSocket(wsUrl);
      this.ws.binaryType = "arraybuffer";

      this.ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          this.binaryListeners.forEach(fn => fn(event.data));
        } else {
          try {
            const msg = JSON.parse(event.data);
            this.jsonListeners.forEach(fn => fn(msg));
          } catch { /* ignore */ }
        }
      };
    }

    sendAudio(pcmData: ArrayBuffer) {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(pcmData);
      }
    }

    onBinary(fn: (data: ArrayBuffer) => void) {
      this.binaryListeners.add(fn);
      return () => { this.binaryListeners.delete(fn); };
    }

    onJson(fn: (msg: AudioMessage) => void) {
      this.jsonListeners.add(fn);
      return () => { this.jsonListeners.delete(fn); };
    }

    disconnect() {
      this.ws?.close();
      this.ws = null;
    }
  }
  ```

- [ ] **5.2** 创建 `tauri-app/src/hooks/useAudioRecorder.ts`
  ```typescript
  /**
   * 麦克风录制 hook.
   * 使用 AudioWorklet 获取 16kHz PCM16 音频帧.
   * 每 32ms (512 samples) 发送一帧到 AudioChannel.
   */
  export function useAudioRecorder(audioChannel: AudioChannel | null) {
    // - getUserMedia({ audio: { sampleRate: 16000, channelCount: 1 } })
    // - AudioWorkletNode 重采样到 16kHz (如果浏览器不支持 16kHz 直出)
    // - 每帧 512 samples = 1024 bytes, 通过 audioChannel.sendAudio() 发送
    // - 返回 { isRecording, startRecording, stopRecording }
  }
  ```

- [ ] **5.3** 创建 `tauri-app/src/hooks/useAudioPlayer.ts`
  ```typescript
  /**
   * TTS 音频播放 hook.
   * 接收 22050Hz PCM16 binary frames, 通过 Web Audio API 播放.
   * 使用 AudioBuffer 队列实现流式无缝播放.
   */
  export function useAudioPlayer(audioChannel: AudioChannel | null) {
    // - AudioContext with 22050Hz sample rate
    // - 接收 binary frames → 解码为 Float32Array
    // - 使用 AudioBufferSourceNode 队列，前一个结束时启动下一个
    // - 返回 { isPlaying, stop }
  }
  ```

- [ ] **5.4** 更新 `types/messages.ts` — 新增音频相关消息类型
  ```typescript
  // 新增类型
  export interface VADEvent {
    type: "vad_event";
    payload: { status: "speech_start" | "speech_end" };
  }

  export interface TranscriptMessage {
    type: "transcript";
    payload: { text: string; role: "user" | "assistant" };
  }

  export interface LipSyncMessage {
    type: "lip_sync";
    payload: { chunk_index: number; amplitude: number };
  }

  export interface TTSEndMessage {
    type: "tts_end";
    payload: {};
  }

  export type AudioMessage = VADEvent | TranscriptMessage | TTSEndMessage | ErrorMessage;
  ```

- [ ] **5.5** 更新 `useWebSocket.ts` — 导出 `useAudioChannel` hook

- [ ] **5.6** 更新 `App.tsx` — 添加语音按钮 (push-to-talk 或 always-on) + 状态指示器
  - 录音状态指示 (红色脉动圆点)
  - VAD 状态显示 (检测到说话时高亮)
  - TTS 播放状态
  - 转录文本自动添加到消息列表

- [ ] **5.7** 创建 AudioWorklet processor 文件 `tauri-app/public/audio-processor.js`
  ```javascript
  // AudioWorkletProcessor: 重采样到 16kHz, 输出 PCM16 帧
  class PCMProcessor extends AudioWorkletProcessor {
    process(inputs, outputs, parameters) {
      const input = inputs[0]?.[0];
      if (!input) return true;
      // 下采样到 16kHz (如果需要)
      // 转换为 Int16
      // 每 512 samples 发送一次 port.postMessage()
      return true;
    }
  }
  registerProcessor("pcm-processor", PCMProcessor);
  ```

---

## Task 6: Live2D 口型同步 + 表情

**目标:** 根据 TTS 音频振幅驱动 Live2D 模型的口型参数，实现说话时嘴巴同步张合。

### 步骤

- [ ] **6.1** 修改 `Live2DCanvas.tsx` — 接受外部口型参数
  ```typescript
  interface Live2DCanvasProps {
    modelPath: string;
    onFpsUpdate?: (fps: number) => void;
    mouthOpenY?: number;  // 0.0 ~ 1.0, 驱动 ParamMouthOpenY
  }
  ```

- [ ] **6.2** 在 Live2D 渲染循环中应用口型参数
  ```typescript
  // 在 renderLoop 中:
  if (model.internalModel?.coreModel) {
    const coreModel = model.internalModel.coreModel;
    // ParamMouthOpenY 控制嘴巴张合
    const paramIndex = coreModel.getParameterIndex("ParamMouthOpenY");
    if (paramIndex >= 0) {
      coreModel.setParameterValueByIndex(paramIndex, mouthOpenY);
    }
  }
  ```

- [ ] **6.3** 在 `App.tsx` 中连接口型参数
  - 从 control channel 接收 `lip_sync` 消息
  - 提取 amplitude → 映射到 mouthOpenY (使用 lerp 平滑)
  - 传递给 `<Live2DCanvas mouthOpenY={...} />`

- [ ] **6.4** Canvas2D fallback 也添加口型动画
  - 根据 amplitude 调整嘴巴弧线的张开程度

- [ ] **6.5** 添加简单表情切换
  - 正常 → 说话中 (微笑 + 口型)
  - 思考中 (LLM 生成时) → 眼睛看向一侧
  - 通过 `lip_sync` 消息中的 `expression` 字段控制

---

## Task 7: 打断机制 (Barge-in)

**目标:** 用户在 TTS 播放中说话时，立即停止播放并开始处理新语音。

### 步骤

- [ ] **7.1** 后端: VoicePipeline 已支持 `interrupt()` 方法
  - speech_start 事件自动触发 interrupt
  - LLM 和 TTS 流式循环检查 `_interrupted` 标志

- [ ] **7.2** 前端: AudioPlayer 接收到 speech_start VAD 事件时
  - 立即停止所有 AudioBufferSourceNode 播放
  - 清空播放队列
  - 发送 interrupt 消息到 control channel

- [ ] **7.3** 前端: 处理打断后的 UI 状态
  - 被打断的 assistant 消息标记为 "(interrupted)"
  - 清空 lip_sync 状态，mouth 归零

- [ ] **7.4** 测试打断流程
  - 模拟: TTS 播放中 → 发送新的 speech audio → 验证播放停止 + 新转录开始

---

## 执行顺序

```
Task 1 (VAD)  ──┐
Task 2 (ASR)  ──┤── 可以并行开发
Task 3 (TTS)  ──┘
      │
      ▼
Task 4 (Pipeline) ── 依赖 1+2+3
      │
      ▼
Task 5 (Frontend Audio) ── 可以和 Task 4 并行, 但集成需要 4
      │
      ▼
Task 6 (Lip Sync) ── 依赖 5 + Live2D model access
      │
      ▼
Task 7 (Barge-in) ── 依赖 4+5+6
```

**建议执行路径:**
1. **Phase A** (并行): Task 1 + Task 2 + Task 3 — 三个 provider 独立实现和测试
2. **Phase B**: Task 4 — 集成为管线
3. **Phase C** (并行): Task 5 + Task 6 — 前端音频 + 口型同步
4. **Phase D**: Task 7 — 打断机制 + 端到端测试

**预估时间:** 4-6 小时 (假设模型下载已完成)

---

## VRAM 预估 (4090 48GB)

| 模型 | VRAM |
|------|------|
| faster-whisper large-v3-turbo (FP16) | ~1.5 GB |
| CosyVoice 2 0.5B | ~2 GB |
| silero-vad (CPU) | 0 GB |
| Ollama gemma4:e4b | ~28 GB |
| **Total** | **~31.5 GB** |

✅ 48GB VRAM 完全够用，还有 16GB+ 余量。

---

## 验收标准

- [ ] 用户对着麦克风说话，VAD 自动检测语音起止
- [ ] ASR 正确转录中英文混合语音
- [ ] LLM 生成中文回复
- [ ] TTS 流式合成自然中文语音
- [ ] 前端流式播放 TTS 音频，无明显卡顿
- [ ] Live2D 口型随 TTS 音频振幅同步张合
- [ ] 用户打断 TTS 播放，系统立即响应
- [ ] 端到端延迟: 用户说完 → 开始播放回复 < 3 秒
