# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from pipeline.voice_pipeline import VoicePipeline


class _FakeWS:
    def __init__(self) -> None:
        self.json_frames: list[dict] = []
        self.binary_frames: list[bytes] = []

    async def send_json(self, data: dict) -> None:
        self.json_frames.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.binary_frames.append(data)


class _FakeASR:
    async def transcribe(self, audio: bytes) -> str:
        return "/new research rust tokio"


class _FakeAgent:
    async def chat_stream(self, messages, *, session_id: str) -> AsyncIterator[str]:
        yield "legacy"


class _FakeTTS:
    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        if False:
            yield b""


class _FakeVAD:
    threshold = 0.5

    def set_threshold(self, value: float) -> None:
        self.threshold = value


class _ServiceContext:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def get(self, name: str) -> Any:
        return self._mapping.get(name)


class _SessionDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def append_message(self, *, session_id: str, role: str, content: str) -> int:
        self.calls.append((session_id, role, content))
        return len(self.calls)


class _Assembler:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def assemble(self, **kwargs: Any):
        self.calls.append(kwargs)
        return None


class _ToolRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:
        return [
            {
                "name": "deepresearch",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "user_request": {"type": "string"},
                    },
                },
            }
        ]

    async def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        self.calls.append((name, dict(params), session_id))
        return {"ok": True, "result": "tool ok", "error": None}


class _LocalLLM:
    model = "stub-model"
    base_url = "http://stub"

    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_tools(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "deepresearch",
                        "arguments": {"topic": "drifted"},
                    }
                ],
                "stop_reason": "tool_use",
                "model": "stub",
                "usage": {},
            }
        return {
            "content": "final answer",
            "tool_calls": [],
            "stop_reason": "end_turn",
            "model": "stub",
            "usage": {},
        }


class _RecordingBroadcast:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    async def __call__(self, originator, msg) -> None:
        self.calls.append((originator, msg))


@pytest.mark.asyncio
async def test_voice_new_scope_uses_one_effective_sid_and_loop_user_request():
    sdb = _SessionDB()
    assembler = _Assembler()
    tools = _ToolRegistry()
    bc = _RecordingBroadcast()
    control = _FakeWS()
    local_llm = _LocalLLM()
    service_context = _ServiceContext(
        {
            "session_db": sdb,
            "context_assembler": assembler,
            "tool_router": tools,
        }
    )
    pipe = VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakeTTS(),
        control_ws=control,
        session_id="default",
        service_context=service_context,
        tool_registry_v2=tools,
        permission_gate_v2=object(),
        local_llm=local_llm,
        broadcast=bc,
    )

    response = await pipe._process_utterance(b"fake-pcm", _FakeWS())

    assert response == "final answer"
    assert sdb.calls == [
        ("task-default-1", "user", "research rust tokio"),
        ("task-default-1", "assistant", "final answer"),
    ]
    assert assembler.calls[0]["session_id"] == "task-default-1"
    assert assembler.calls[0]["user_message"] == "research rust tokio"
    assert tools.calls == [
        (
            "deepresearch",
            {"topic": "drifted", "user_request": "research rust tokio"},
            "task-default-1",
        )
    ]
    assert [msg["payload"]["session_id"] for _, msg in bc.calls] == [
        "task-default-1",
        "task-default-1",
    ]

