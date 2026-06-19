# SPDX-License-Identifier: BUSL-1.1
"""FP-5 缺口 5k 回归（2026-06-06 子代理复评第9处）：语音 venue 的技能自创 codify
接线。原 voice_pipeline 用裸 _AgentLoop → codify(FP-5)/verify(FP-3) 在语音对话里
永不触发（与文字 venue 不对称）。修复后：app_config + service_context 在 → turn
结束调 main._maybe_codify_skill（control_ws 当 ws 发技能候选卡）；缺失 → no-op(BC)。
"""
from __future__ import annotations

import pytest

from pipeline.voice_pipeline import VoicePipeline


class _FakeWS:
    async def send_json(self, data: dict) -> None:  # pragma: no cover - unused here
        pass


class _FakeSC:
    def __init__(self):
        self._d = {"tool_path_recorder": object(), "skill_loader": object()}

    def get(self, name):
        return self._d.get(name)


def _make_pipeline(*, app_config, service_context, control_ws=None):
    # vad/asr/agent/tts unused by _maybe_codify_voice — pass minimal stubs.
    return VoicePipeline(
        vad=object(), asr=object(), agent=object(), tts=object(),
        control_ws=control_ws, session_id="voice-sess-1",
        service_context=service_context, local_llm=object(),
        app_config=app_config,
    )


@pytest.mark.asyncio
async def test_voice_codify_invokes_helper_with_control_ws(monkeypatch):
    """app_config + sc 在 → _maybe_codify_voice 调 main._maybe_codify_skill,
    且把 control_ws 当 ws 传入（技能候选卡经语音控制通道发桌宠主 UI）。"""
    calls = []

    async def _fake_codify(sc, config, sid, ws, waiters):
        calls.append((sc, config, sid, ws, waiters))

    import main
    monkeypatch.setattr(main, "_maybe_codify_skill", _fake_codify)

    ws = _FakeWS()
    sc = _FakeSC()
    cfg = object()
    p = _make_pipeline(app_config=cfg, service_context=sc, control_ws=ws)
    await p._maybe_codify_voice()
    # codify 是 fire-and-forget（不阻塞 TTS）→ drain 后台任务再断言。
    import asyncio
    if p._codify_tasks:
        await asyncio.gather(*list(p._codify_tasks))

    assert len(calls) == 1, f"应恰好调一次 codify,实际 {len(calls)}"
    got_sc, got_cfg, got_sid, got_ws, _waiters = calls[0]
    assert got_sc is sc
    assert got_cfg is cfg
    assert got_sid == "voice-sess-1"
    assert got_ws is ws, "control_ws 必须作为 codify 的 ws 传入(技能卡通道)"


@pytest.mark.asyncio
async def test_voice_codify_noop_without_app_config(monkeypatch):
    """app_config 缺失(legacy/单测) → 不调 codify(BC,与裸 _AgentLoop 行为一致)。"""
    calls = []

    async def _fake_codify(*a, **k):
        calls.append(a)

    import main
    monkeypatch.setattr(main, "_maybe_codify_skill", _fake_codify)

    p = _make_pipeline(app_config=None, service_context=_FakeSC(), control_ws=_FakeWS())
    await p._maybe_codify_voice()
    assert calls == [], "app_config=None 时不应触发 codify"


@pytest.mark.asyncio
async def test_voice_codify_noop_without_service_context(monkeypatch):
    """service_context 缺失 → no-op(BC)。"""
    calls = []

    async def _fake_codify(*a, **k):
        calls.append(a)

    import main
    monkeypatch.setattr(main, "_maybe_codify_skill", _fake_codify)

    p = _make_pipeline(app_config=object(), service_context=None, control_ws=_FakeWS())
    await p._maybe_codify_voice()
    assert calls == [], "service_context=None 时不应触发 codify"
