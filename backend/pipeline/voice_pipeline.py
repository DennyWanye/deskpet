"""Voice pipeline: VAD → ASR → LLM → TTS, fully streaming."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import numpy as np
import structlog
from fastapi import WebSocket

from observability.metrics import stage_timer
from pipeline.barge_in_filter import BargeInFilter
from pipeline.tag_parser import StreamingTagParser, TagEvent

if TYPE_CHECKING:
    from agent.providers.base import AgentProvider
    from providers.silero_vad import SileroVAD
    from providers.faster_whisper_asr import FasterWhisperASR
    from providers.edge_tts_provider import EdgeTTSProvider

logger = structlog.get_logger()


class VoicePipeline:
    """
    Manages the voice processing flow for a single WebSocket session.

    Audio in → VAD detects speech segments → ASR transcribes →
    Agent generates reply (streaming, with emotion/action tag extraction) →
    TTS synthesizes → audio streamed back + lip-sync / emotion /
    action params sent to control channel.

    Lifecycle: one instance per audio WebSocket connection.
    """

    def __init__(
        self,
        vad: SileroVAD,
        asr: FasterWhisperASR,
        agent: "AgentProvider",
        tts: EdgeTTSProvider,
        control_ws: WebSocket | None = None,
        session_id: str = "default",
    ):
        self.vad = vad
        self.asr = asr
        self.agent = agent
        self.tts = tts
        self.control_ws = control_ws
        self.session_id = session_id
        self._interrupted = False
        self._processing = False
        self._current_task: asyncio.Task | None = None
        self._barge_in_filter = BargeInFilter()

    def interrupt(self) -> None:
        """User barge-in — stop current TTS generation."""
        self._interrupted = True
        task = self._current_task
        if task and not task.done():
            task.cancel()

    async def _emit_tag_event(self, evt: TagEvent) -> None:
        """Forward emotion/action tag to control channel for Live2D driving."""
        if not self.control_ws:
            return
        msg_type = "emotion_change" if evt.kind == "emotion" else "action_trigger"
        try:
            await self.control_ws.send_json({
                "type": msg_type,
                "payload": {"value": evt.value},
            })
        except Exception:
            pass  # control channel may have disconnected

    async def process_audio_chunk(self, pcm_bytes: bytes, audio_ws: WebSocket) -> None:
        """
        Process one audio frame, drive the full pipeline.

        Flow:
        1. VAD detects speech_start / speech_end
        2. speech_end → ASR transcription
        3. Transcript → LLM streaming reply
        4. LLM reply → TTS streaming synthesis
        5. TTS audio sent back via audio_ws (binary)
        6. Lip-sync params sent via control_ws (JSON)
        """
        # Debug: log every ~30 chunks (~1 second) with max amplitude
        self._chunk_counter = getattr(self, "_chunk_counter", 0) + 1
        if self._chunk_counter % 30 == 1:
            audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
            max_amp = int(np.abs(audio_int16).max()) if len(audio_int16) else 0
            logger.info(
                "audio_chunk",
                n=self._chunk_counter,
                bytes=len(pcm_bytes),
                max_amp=max_amp,
                silent=max_amp < 200,
            )

        events = self.vad.process_chunk(pcm_bytes)

        for event in events:
            if event["event"] == "speech_start":
                await audio_ws.send_json({
                    "type": "vad_event",
                    "payload": {"status": "speech_start"},
                })
                if self._processing:
                    speech_ms = self.vad.current_speech_duration_ms()
                    if self._barge_in_filter.should_allow(speech_ms):
                        await audio_ws.send_json({
                            "type": "tts_barge_in",
                            "payload": {"reason": "vad_speech_detected"},
                        })
                        self._barge_in_filter.on_interrupted()
                        self.interrupt()

            elif event["event"] == "speech_end":
                speech_audio = event["audio"]
                await audio_ws.send_json({
                    "type": "vad_event",
                    "payload": {"status": "speech_end"},
                })
                # Cancel any prior in-flight utterance — only one should run
                # at a time. The new task will await the old one's teardown
                # before starting, preventing interleaved ASR/LLM/TTS output.
                prior = self._current_task
                if prior and not prior.done():
                    prior.cancel()
                self._current_task = asyncio.create_task(
                    self._process_utterance(speech_audio, audio_ws, prior)
                )

    async def _process_utterance(
        self,
        audio_bytes: bytes,
        audio_ws: WebSocket,
        prior: asyncio.Task | None = None,
    ) -> None:
        """Process a complete speech segment: ASR → LLM → TTS.

        If `prior` is provided, wait for its cancellation to finish before
        starting — keeps only one utterance in flight.
        """
        if prior is not None:
            try:
                await prior
            except (asyncio.CancelledError, Exception):
                pass

        self._interrupted = False
        self._processing = True

        try:
            # Step 1: ASR
            async with stage_timer("asr", session_id=self.session_id):
                text = await self.asr.transcribe(audio_bytes)
            if not text.strip():
                return

            logger.info("user_said", text=text)
            await audio_ws.send_json({
                "type": "transcript",
                "payload": {"text": text, "role": "user"},
            })

            # Step 2: Agent (streaming) — parse emotion/action tags inline,
            # forward tag events to control channel, keep clean text for TTS.
            response_text = ""
            messages = [{"role": "user", "content": text}]
            parser = StreamingTagParser()
            async with stage_timer("agent", session_id=self.session_id):
                async for token in self.agent.chat_stream(
                    messages, session_id=self.session_id
                ):
                    if self._interrupted:
                        logger.info("agent_interrupted")
                        break
                    for item in parser.feed(token):
                        if isinstance(item, TagEvent):
                            await self._emit_tag_event(item)
                        else:
                            response_text += item
                # Flush trailing buffer (e.g. dangling '[' at EOS)
                for item in parser.flush():
                    if isinstance(item, TagEvent):
                        await self._emit_tag_event(item)
                    else:
                        response_text += item

            if self._interrupted or not response_text.strip():
                return

            # 路由指示灯（前端右上角）只在收到 chat_response / transcript
            # 携带的 provider 字段时切换颜色。纯语音用户永远不会走 control
            # 通道的 chat_response 分支，所以这里模仿 main.py 的做法，从
            # agent 底层 llm 的 _cloud / _local last_usage 推断本轮实际服务
            # 的路由，并把它捎在 transcript 里发给前端。
            served_by: str | None = None
            # agent 可能是 ToolUsingAgent(base=SimpleLLMAgent(llm=...)) 的嵌套，
            # 实际 llm 在最内层；顺着 _llm / _base 走到第一个有 _cloud/_local 的
            # 对象为止。最多 8 层防环。
            probe = self.agent
            llm = None
            for _ in range(8):
                if hasattr(probe, "_cloud") or hasattr(probe, "_local"):
                    llm = probe
                    break
                nxt = getattr(probe, "_llm", None) or getattr(probe, "_base", None)
                if nxt is None or nxt is probe:
                    break
                probe = nxt
            if llm is not None:
                for route in ("cloud", "local"):
                    provider = getattr(llm, f"_{route}", None)
                    if provider is None:
                        continue
                    if getattr(provider, "last_usage", None):
                        served_by = route
                        break

            logger.info("llm_response", text=response_text[:100], served_by=served_by)
            transcript_payload: dict = {"text": response_text, "role": "assistant"}
            if served_by:
                transcript_payload["provider"] = served_by
            await audio_ws.send_json({
                "type": "transcript",
                "payload": transcript_payload,
            })

            # Step 3: TTS (PCM16 24kHz stream via ffmpeg pipe — P2-2-M2)
            # Binary frame layout: 1-byte type header + audio data.
            # 0x01 = PCM16 mono 24kHz (M2 现行)；0x02 = MP3 (M1 历史兼容)。
            chunk_index = 0
            self._barge_in_filter.on_tts_start()
            async with stage_timer("tts", session_id=self.session_id, chars=len(response_text)):
                async for pcm_chunk in self.tts.synthesize_pcm_stream(response_text):
                    if self._interrupted:
                        logger.info("tts_interrupted")
                        break
                    frame = b"\x01" + pcm_chunk
                    await audio_ws.send_bytes(frame)
                    # Lip-sync：直接读 PCM16 算 RMS，比 MP3 大小启发式精准
                    # 得多。RMS 到 amplitude 的尺度 (÷8000) 按经验调，既能
                    # 让正常语音打到 0.6-0.9，又不让轻声被吞。
                    if self.control_ws:
                        try:
                            pcm_arr = np.frombuffer(pcm_chunk, dtype=np.int16)
                            rms = float(
                                np.sqrt(np.mean(pcm_arr.astype(np.float32) ** 2))
                            )
                            amplitude = min(1.0, rms / 8000.0)
                            await self.control_ws.send_json({
                                "type": "lip_sync",
                                "payload": {
                                    "chunk_index": chunk_index,
                                    "amplitude": amplitude,
                                },
                            })
                        except Exception:
                            pass  # control channel may have disconnected
                    chunk_index += 1
            self._barge_in_filter.on_tts_end()

            # TTS end marker
            await audio_ws.send_json({
                "type": "tts_end",
                "payload": {},
            })

        except asyncio.CancelledError:
            logger.info("utterance_cancelled")
            raise
        except Exception as e:
            logger.error("pipeline_error", error=str(e))
            try:
                await audio_ws.send_json({
                    "type": "error",
                    "payload": {"message": str(e)},
                })
            except Exception:
                pass
        finally:
            self._processing = False


def _estimate_amplitude_from_size(chunk_size: int) -> float:
    """Legacy MP3-era 幅度启发式 —— P2-2-M2 已切到 PCM RMS，保留此函数
    仅为回旋余地（未来若再出现无法算 RMS 的格式可以复用）。"""
    return min(1.0, chunk_size / 8192)
