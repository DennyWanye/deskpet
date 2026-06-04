# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""VOICE-MSGPANEL-SYNC: VoicePipeline 语音对话多窗口广播测试。

Bug：桌宠主窗口的语音对话只经 audio_ws point-to-point 回发起窗口，从不
广播给其它 control 通道，导致「消息·主线程」消息框看不到语音对话
（真机复现 VOICE-MSGPANEL-01）。

修复：VoicePipeline 在产生 user transcript / assistant 回复时，复用文字
同款的 `_broadcast_default_chat_peers`，fan-out 成 chat_v2_user_echo /
chat_v2_final 给除发起窗口（self.control_ws = originator）外的所有 control
通道。

测试组对应 spec §7.1（5 条）。
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from pipeline.voice_pipeline import VoicePipeline
from providers.edge_tts_provider import PCM_CHUNK_BYTES


# ---- fakes（与 test_voice_pipeline_tts.py 同款，独立复制保持测试自洽）----


class _FakeWS:
    def __init__(self):
        self.binary_frames: list[bytes] = []
        self.json_frames: list[dict] = []

    async def send_bytes(self, data: bytes) -> None:
        self.binary_frames.append(bytes(data))

    async def send_json(self, data: dict) -> None:
        self.json_frames.append(data)


class _FakeASR:
    def __init__(self, text: str = "你可以问我做什么"):
        self._text = text

    async def transcribe(self, audio: bytes) -> str:
        return self._text


class _FakeAgent:
    def __init__(self, reply: str = "当然可以呀～那我问你"):
        self._reply = reply

    async def chat_stream(self, messages, *, session_id: str):
        yield self._reply


class _FakePCMTTS:
    def __init__(self, chunks: list[bytes] | None = None):
        self._chunks = chunks or [b"\x00" * PCM_CHUNK_BYTES]

    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        for c in self._chunks:
            yield c


class _FakeVAD:
    def on_tts_start(self): ...
    def on_tts_end(self): ...


class _RecordingBroadcast:
    """记录每次 (originator_ws, msg) 调用 —— 镜像 _broadcast_default_chat_peers 签名。"""

    def __init__(self):
        self.calls: list[tuple[object, dict]] = []

    async def __call__(self, originator, msg) -> None:
        self.calls.append((originator, msg))


def _make_pipe(*, session_id="default", broadcast=None, control_ws=None,
               asr_text="你可以问我做什么", reply="当然可以呀"):
    return VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR(asr_text),
        agent=_FakeAgent(reply),
        tts=_FakePCMTTS([b"\x00" * PCM_CHUNK_BYTES]),
        control_ws=control_ws if control_ws is not None else _FakeWS(),
        session_id=session_id,
        broadcast=broadcast,
    )


# ---- tests ----


@pytest.mark.asyncio
async def test_default_session_broadcasts_user_and_final():
    """default 会话：广播被调 2 次，payload = user_echo + final，originator = control_ws。"""
    control = _FakeWS()
    bc = _RecordingBroadcast()
    pipe = _make_pipe(
        session_id="default", broadcast=bc, control_ws=control,
        asr_text="你可以问我做什么", reply="当然可以呀",
    )

    await pipe._process_utterance(b"fake-pcm-in", _FakeWS())

    assert len(bc.calls) == 2, f"expected 2 broadcasts, got {len(bc.calls)}: {bc.calls}"
    (o1, m1), (o2, m2) = bc.calls
    assert m1["type"] == "chat_v2_user_echo"
    assert m1["payload"] == {"session_id": "default", "text": "你可以问我做什么"}
    assert m2["type"] == "chat_v2_final"
    assert m2["payload"] == {"session_id": "default", "text": "当然可以呀"}
    assert o1 is control and o2 is control


@pytest.mark.asyncio
async def test_non_default_session_does_not_broadcast():
    """非 default 会话（code-*）不广播 —— 只有 companion 主线程需要多窗口同步。"""
    bc = _RecordingBroadcast()
    pipe = _make_pipe(session_id="code-abc123", broadcast=bc)

    await pipe._process_utterance(b"fake", _FakeWS())

    assert bc.calls == [], f"non-default session must not broadcast, got: {bc.calls}"


@pytest.mark.asyncio
async def test_broadcast_none_is_safe():
    """broadcast=None（legacy ctor / 老测试）：不抛异常，TTS 仍执行。"""
    audio_ws = _FakeWS()
    pipe = _make_pipe(session_id="default", broadcast=None)

    await pipe._process_utterance(b"fake", audio_ws)

    # TTS 二进制帧仍发出 —— 广播缺席不影响主链路。
    assert len(audio_ws.binary_frames) >= 1


@pytest.mark.asyncio
async def test_broadcast_failure_does_not_break_tts():
    """广播抛异常（peer 断开等）必须被 swallow —— TTS 仍完成。"""

    class _BrokenBroadcast:
        async def __call__(self, originator, msg):
            raise RuntimeError("simulated peer disconnect mid-broadcast")

    audio_ws = _FakeWS()
    pipe = _make_pipe(session_id="default", broadcast=_BrokenBroadcast())

    # 不应抛出 —— 异常被 best-effort try/except 吞掉。
    await pipe._process_utterance(b"fake", audio_ws)

    assert len(audio_ws.binary_frames) >= 1, "TTS must still emit despite broadcast failure"


@pytest.mark.asyncio
async def test_broadcasts_regardless_of_control_ws_snapshot():
    """根因守护（VOICE-MSGPANEL-SYNC 运行时缺陷修复）：广播**不依赖** control_ws
    快照。

    真机复现发现：backend respawn 后 audio_ws 常先于 control_ws 重连，
    VoicePipeline.control_ws（audio 连接期快照）会是 None，旧守卫
    `... and self.control_ws and ...` 会挡掉广播 → 语音进不了消息框。
    修复后无论 control_ws 是有效 ws 还是 None，default 会话都必须广播；真正的
    originator-skip（防主窗口重复显示）由注入的 _voice_broadcast 闭包在广播时
    **实时**解析 `_control_connections.get(session_id)`，不靠连接期快照。
    """
    # case 1: control_ws 有值
    bc1 = _RecordingBroadcast()
    pipe1 = _make_pipe(session_id="default", broadcast=bc1, control_ws=_FakeWS())
    await pipe1._process_utterance(b"fake", _FakeWS())
    assert len(bc1.calls) == 2

    # case 2: control_ws 为 None（backend respawn 后 audio 先连的根因场景）
    bc2 = _RecordingBroadcast()
    pipe2 = VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR("请记住我喜欢爬山"),
        agent=_FakeAgent("记住啦"),
        tts=_FakePCMTTS([b"\x00" * PCM_CHUNK_BYTES]),
        control_ws=None,
        session_id="default",
        broadcast=bc2,
    )
    await pipe2._process_utterance(b"fake", _FakeWS())
    assert len(bc2.calls) == 2, (
        f"control_ws=None（根因场景）时仍须广播，实际 {len(bc2.calls)}"
    )
    assert bc2.calls[0][1]["type"] == "chat_v2_user_echo"
    assert bc2.calls[1][1]["type"] == "chat_v2_final"
