"""Voice pipeline: VAD → ASR → LLM → TTS, fully streaming."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import numpy as np
import structlog
from fastapi import WebSocket

from observability.metrics import stage_timer
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

            logger.info("llm_response", text=response_text[:100])
            await audio_ws.send_json({
                "type": "transcript",
                "payload": {"text": response_text, "role": "assistant"},
            })

            # Step 3: TTS (streaming synthesis + streaming send)
            chunk_index = 0
            async with stage_timer("tts", session_id=self.session_id, chars=len(response_text)):
                async for audio_chunk in self.tts.synthesize_stream(response_text):
                    if self._interrupted:
                        logger.info("tts_interrupted")
                        break
                    # Send audio data (binary frame)
                    await audio_ws.send_bytes(audio_chunk)
                    # Send lip-sync params to control channel
                    if self.control_ws:
                        try:
                            await self.control_ws.send_json({
                                "type": "lip_sync",
                                "payload": {
                                    "chunk_index": chunk_index,
                                    "amplitude": _estimate_amplitude_from_size(len(audio_chunk)),
                                },
                            })
                        except Exception:
                            pass  # control channel may have disconnected
                    chunk_index += 1

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
    """
    Rough amplitude estimate based on chunk size.
    For MP3 data we can't easily compute RMS, so we use a heuristic.
    When CosyVoice 2 (PCM output) is integrated, this will use proper RMS.
    """
    # Normalize to 0.0-1.0 range based on typical chunk sizes
    return min(1.0, chunk_size / 8192)
