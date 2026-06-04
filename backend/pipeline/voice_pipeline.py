# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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
        vad_threshold_during_tts: float = 0.65,
        min_speech_ms_during_tts: int = 400,
        tts_cooldown_ms: int = 300,
        # P4-S21 #13 fix: optional handles for the tool-use path. When all
        # three are present, _process_utterance routes through AgentLoop
        # instead of plain agent.chat_stream — meaning voice input can
        # actually trigger tools (e.g. "make me a todo.txt on the desktop"
        # really creates the file). Backwards-compatible: tests / dev
        # configs that pass nothing keep getting the legacy chat_stream
        # behaviour.
        service_context: object | None = None,
        tool_registry_v2: object | None = None,
        permission_gate_v2: object | None = None,
        local_llm: object | None = None,
        broadcast: object | None = None,
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
        self._barge_in_filter = BargeInFilter(
            cooldown_ms=tts_cooldown_ms,
            min_speech_during_tts_ms=min_speech_ms_during_tts,
        )
        # tool-use plumbing (all None == legacy chat_stream path)
        self._service_context = service_context
        self._tool_registry_v2 = tool_registry_v2
        self._permission_gate_v2 = permission_gate_v2
        self._local_llm = local_llm
        # VOICE-MSGPANEL-SYNC: 多窗口广播器（None == legacy / 单测，跳过 fan-out）。
        # 由 main.py audio_channel 注入 _broadcast_default_chat_peers，让语音对话
        # 也能像文字 chat_v2 一样同步到「消息·主线程」消息框窗口。
        self._broadcast = broadcast
        # P2-2-M3: stash the "normal" threshold at init time (from [vad])
        # so we can restore it after TTS / interrupt. The during_tts value
        # is the raised threshold we swap in while TTS is playing — keeps
        # speaker echo from re-triggering VAD.
        self._vad_threshold_normal = getattr(vad, "threshold", 0.5)
        self._vad_threshold_during_tts = vad_threshold_during_tts
        # P2-2-M3: speech_start fires once with duration=0, so we can't gate
        # barge-in on that single event. Instead, re-evaluate every frame
        # while in TTS. This flag ensures we fire tts_barge_in at most once
        # per continuous speech segment (reset on speech_start / speech_end).
        self._barge_in_fired_for_current_speech = False

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

    async def _broadcast_chat_v2(self, msg_type: str, text: str) -> None:
        """VOICE-MSGPANEL-SYNC: 把一轮语音对话 fan-out 给**其它** control 通道。

        桌宠主窗口和「消息·主线程」消息框是两个独立 Tauri 窗口、各自独立的
        ws。语音原本只经 audio_ws point-to-point 回发起窗口，消息框看不到。
        这里复用文字同款的 broadcaster（_broadcast_default_chat_peers），把
        语音轮次包成 chat_v2_user_echo / chat_v2_final 广播出去。

        originator = self.control_ws（发起语音的主窗口的 control 通道）会被
        broadcaster skip —— 主窗口已通过自己的 audio_ws transcript 显示这轮，
        不重复；消息框窗口的 control 通道则收到 → 补上。

        守卫：未注入 broadcast / 无 control_ws / 非 default 会话时跳过。
        best-effort：广播失败只告警，绝不影响 TTS 主链路。
        """
        # 注：不再依赖 self.control_ws（audio 连接时的快照，backend respawn 后可能
        # None/失效）。originator 由注入的 _voice_broadcast 闭包在广播时实时解析。
        if not (self._broadcast and self.session_id == "default"):
            return
        try:
            await self._broadcast(self.control_ws, {
                "type": msg_type,
                "payload": {"session_id": "default", "text": text},
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_broadcast_failed", msg_type=msg_type, error=str(exc))

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
                # New speech segment — rearm the barge-in-once guard.
                self._barge_in_fired_for_current_speech = False
                # NOTE: we deliberately do NOT try to barge-in here.
                # current_speech_duration_ms() is 0 at the speech_start frame
                # (VAD sets _speech_start_ms = _ms_counter before incrementing),
                # so the TTS-phase gate (min_speech_ms_during_tts=400) would
                # never open. The per-frame check below evaluates duration as
                # it grows across subsequent frames.

            elif event["event"] == "speech_end":
                speech_audio = event["audio"]
                await audio_ws.send_json({
                    "type": "vad_event",
                    "payload": {"status": "speech_end"},
                })
                # Speech segment finished — rearm the barge-in guard for the
                # next speech_start. (Also rearmed on speech_start, but cover
                # the edge case where speech_end arrives without a matching
                # speech_start in the same chunk.)
                self._barge_in_fired_for_current_speech = False
                # Cancel any prior in-flight utterance — only one should run
                # at a time. The new task will await the old one's teardown
                # before starting, preventing interleaved ASR/LLM/TTS output.
                prior = self._current_task
                if prior and not prior.done():
                    prior.cancel()
                self._current_task = asyncio.create_task(
                    self._process_utterance(speech_audio, audio_ws, prior)
                )

        # P2-2-M3: per-frame barge-in re-evaluation. speech_start fires only
        # once with duration=0, so min_speech_ms_during_tts would never gate
        # anything if we only checked at speech_start events. Here we check
        # every frame: if TTS is active AND VAD is currently in speech AND
        # we haven't already barged in for this segment, evaluate the filter.
        if self._processing and not self._barge_in_fired_for_current_speech:
            speech_ms = self.vad.current_speech_duration_ms()
            # speech_ms > 0 means the VAD is currently inside a speech segment
            # (it returns 0 outside speech).
            if speech_ms > 0 and self._barge_in_filter.should_allow(speech_ms):
                await audio_ws.send_json({
                    "type": "tts_barge_in",
                    "payload": {"reason": "vad_speech_detected"},
                })
                self._barge_in_fired_for_current_speech = True
                self._barge_in_filter.on_interrupted()
                self.interrupt()

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
            # VOICE-MSGPANEL-SYNC: user 这句同步给其它窗口（消息框），与文字
            # chat_v2_user_echo 同构。
            await self._broadcast_chat_v2("chat_v2_user_echo", text)

            # Step 2: Agent — two paths.
            #
            # If the tool-use stack is wired in (set via ctor in main.py),
            # run AgentLoop so voice input can actually invoke tools (write
            # files, run shell, fetch URLs — anything in ToolRegistryV2).
            # Otherwise fall back to the legacy streaming chat path that
            # ships text straight to TTS without giving the LLM any
            # tool-calling capability.
            response_text = ""
            if (
                self._tool_registry_v2 is not None
                and self._permission_gate_v2 is not None
                and self._local_llm is not None
            ):
                response_text = await self._run_with_tools(text, audio_ws)
            else:
                response_text = await self._run_legacy_chat_stream(text)

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
            # VOICE-MSGPANEL-SYNC: assistant 回复同步给其它窗口（chat_v2_final）。
            await self._broadcast_chat_v2("chat_v2_final", response_text)

            # Step 3: TTS (PCM16 24kHz stream via ffmpeg pipe — P2-2-M2)
            # Binary frame layout: 1-byte type header + audio data.
            # 0x01 = PCM16 mono 24kHz (M2 现行)；0x02 = MP3 (M1 历史兼容)。
            chunk_index = 0
            self._barge_in_filter.on_tts_start()
            # P2-2-M3: raise VAD threshold so speaker echo doesn't retrigger
            # speech_start. Restore in the outer finally below.
            try:
                self.vad.set_threshold(self._vad_threshold_during_tts)
            except AttributeError:
                pass  # tests may inject a stub VAD
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
            # P2-2-M3: always restore the normal VAD threshold — regardless
            # of whether TTS finished naturally, got interrupted, or raised.
            try:
                self.vad.set_threshold(self._vad_threshold_normal)
            except AttributeError:
                pass
            self._processing = False


    async def _run_legacy_chat_stream(self, text: str) -> str:
        """Pre-P4-S21 path: stream tokens straight from agent.chat_stream.

        No tool-use, no ContextAssembler, no SessionDB write (the underlying
        SimpleLLMAgent typically writes itself). Kept for backwards-compat
        when VoicePipeline is constructed without the v2 stack (older
        tests, minimal dev configs).
        """
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
            # Flush trailing buffer (dangling '[' at EOS)
            for item in parser.flush():
                if isinstance(item, TagEvent):
                    await self._emit_tag_event(item)
                else:
                    response_text += item
        return response_text

    async def _run_with_tools(self, text: str, audio_ws: WebSocket) -> str:
        """P4-S21 #13 path: route voice through AgentLoop tool-use loop.

        Mirrors main.py's `_run_chat`:
          1. persist user msg to SessionDB + enqueue vector
          2. ContextAssembler.assemble → bundle (with bundle.history)
          3. AgentLoop.run with OpenAICompatibleAgentLLM shim
          4. forward tool_call events to control channel (PermissionPopup
             still asks the user — language is unchanged), final assistant
             reply goes to TTS as a single string at the end of the loop

        We don't try to stream chunks through TTS for the tool-use path —
        AgentLoop emits coherent assistant turns at logical boundaries
        (after each LLM round). Streaming each token wouldn't compose
        cleanly with tag-parsing across round boundaries; piecing it
        together once at the end is much simpler and the latency hit is
        a few hundred ms of buffering at most.
        """
        from deskpet.agent.assembler.bundle import ContextBundle as _Bundle  # noqa: F401  (used via assembler)
        sc = self._service_context

        def _ctx(name: str):
            getter = getattr(sc, "get", None)
            if getter is None:
                return None
            return getter(name)

        # Step 1 — persist user msg (mirrors main.py:1481-1491)
        sdb = _ctx("session_db") if sc else None
        vw = _ctx("vector_worker") if sc else None
        user_msg_id = None
        if sdb is not None:
            try:
                user_msg_id = await sdb.append_message(
                    session_id=self.session_id, role="user", content=text,
                )
                if vw is not None and user_msg_id is not None:
                    await vw.enqueue(user_msg_id, text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("voice_chat_persist_user_failed", error=str(exc))

        # Step 2 — ContextAssembler (gives us bundle.history per #16 fix)
        bundle = None
        assembler = _ctx("context_assembler") if sc else None
        if assembler is not None and getattr(assembler, "enabled", True):
            try:
                bundle = await assembler.assemble(
                    user_message=text,
                    memory_manager=_ctx("memory_manager"),
                    tool_registry=_ctx("tool_router"),
                    skill_registry=_ctx("skill_loader"),
                    mcp_manager=_ctx("mcp_manager"),
                    session_id=self.session_id,
                    config={
                        "llm": {
                            "model": getattr(self._local_llm, "model", "unknown"),
                            "base_url": getattr(self._local_llm, "base_url", ""),
                        },
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "voice_assembler_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                bundle = None

        if bundle is not None:
            messages = bundle.build_messages(
                user_message=text,
                history=bundle.history,
            )
        else:
            messages = [{"role": "user", "content": text}]

        # Step 3 — AgentLoop
        from agent.agent_loop import (
            AgentLoop as _AgentLoop,
            AssistantMessageEvent as _AsstEv,
            ToolCallEvent as _TCEv,
            ToolResultEvent as _TREv,
            FinalEvent as _FinEv,
            ErrorEvent as _ErrEv,
        )
        from agent.tool_use_shim import OpenAICompatibleAgentLLM as _Shim
        shim = _Shim(provider=self._local_llm)
        loop = _AgentLoop(
            llm_registry=shim,
            tool_registry=self._tool_registry_v2,
            max_iterations=8,
        )

        final_text = ""
        parser = StreamingTagParser()

        # Tag the permission requests as voice-sourced so the gate can
        # add a TTS audible prompt next to the popup (#13 design step C).
        gate = self._permission_gate_v2
        prev_source = None
        if gate is not None:
            prev_source = getattr(gate, "current_source", None)
            try:
                gate.current_source = "voice"
            except Exception:  # noqa: BLE001 — defensive
                pass

        async with stage_timer("agent", session_id=self.session_id):
            try:
                async for ev in loop.run(messages, session_id=self.session_id):
                    if self._interrupted:
                        logger.info("agent_interrupted")
                        break
                    if isinstance(ev, _AsstEv):
                        # Mid-loop assistant turn (with tool_calls). Send
                        # to control channel as "thinking" indicator only;
                        # the final reply comes via _FinEv to avoid
                        # double-rendering on the frontend (same rule as
                        # main.py:1563).
                        if ev.content and ev.tool_calls and self.control_ws:
                            try:
                                await self.control_ws.send_json({
                                    "type": "chat_response",
                                    "payload": {"text": ev.content, "provider": "v2-voice"},
                                })
                            except Exception:  # noqa: BLE001
                                pass
                    elif isinstance(ev, _TCEv) and ev.tool_call and self.control_ws:
                        try:
                            await self.control_ws.send_json({
                                "type": "tool_call",
                                "payload": {
                                    "name": ev.tool_call.get("name"),
                                    "arguments": ev.tool_call.get("arguments"),
                                },
                            })
                        except Exception:  # noqa: BLE001
                            pass
                    elif isinstance(ev, _TREv) and self.control_ws:
                        try:
                            await self.control_ws.send_json({
                                "type": "tool_result",
                                "payload": {
                                    "tool": ev.tool_name,
                                    "ok": ev.ok,
                                    "result": ev.result,
                                },
                            })
                        except Exception:  # noqa: BLE001
                            pass
                    elif isinstance(ev, _FinEv):
                        final_text = ev.content or ""
                    elif isinstance(ev, _ErrEv):
                        logger.warning("agent_loop_error", error=ev.error)
                        if self.control_ws:
                            try:
                                await self.control_ws.send_json({
                                    "type": "chat_v2_error",
                                    "payload": {"error": ev.error},
                                })
                            except Exception:  # noqa: BLE001
                                pass
                        break
            finally:
                if gate is not None:
                    try:
                        gate.current_source = prev_source
                    except Exception:  # noqa: BLE001
                        pass

        # Apply tag parser to extract emotion/action tags from final text.
        # Even though we got the whole text in one shot, the parser still
        # works on a single chunk → flush.
        clean: list[str] = []
        for item in parser.feed(final_text):
            if isinstance(item, TagEvent):
                await self._emit_tag_event(item)
            else:
                clean.append(item)
        for item in parser.flush():
            if isinstance(item, TagEvent):
                await self._emit_tag_event(item)
            else:
                clean.append(item)

        response_text = "".join(clean)

        # Persist assistant reply (mirror main.py:1645-1658). AgentLoop
        # doesn't write to SessionDB itself; main.py and voice_pipeline
        # are the only writers. _process_utterance is the single in-flight
        # task per VoicePipeline (canceled-and-replaced via _current_task),
        # so we don't need a dedup guard here.
        if sdb is not None and response_text:
            try:
                asst_id = await sdb.append_message(
                    session_id=self.session_id,
                    role="assistant",
                    content=response_text,
                )
                if vw is not None and asst_id is not None:
                    await vw.enqueue(asst_id, response_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "voice_chat_persist_assistant_failed", error=str(exc)
                )

        return response_text


def _estimate_amplitude_from_size(chunk_size: int) -> float:
    """Legacy MP3-era 幅度启发式 —— P2-2-M2 已切到 PCM RMS，保留此函数
    仅为回旋余地（未来若再出现无法算 RMS 的格式可以复用）。"""
    return min(1.0, chunk_size / 8192)
