import asyncio
import json

import pytest

from deskpet.tools.code_tools.clarify_tool import (
    build_ask_clarification_tool,
    build_clarification_ask,
    resolve_clarification_response,
)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@pytest.mark.asyncio
async def test_clarify_blocks_until_response():
    ws = _FakeWebSocket()
    pending: dict[str, asyncio.Future] = {}
    ask = build_clarification_ask(
        {"default": ws},
        pending,
        timeout_s=1.0,
        request_id_factory=lambda: "rid-1",
    )
    handler, _schema = build_ask_clarification_tool(ask)

    task = asyncio.create_task(
        handler({"question": "Which project?", "options": ["A", "B"]}, session_id="default")
    )
    await asyncio.sleep(0)

    assert ws.sent == [
        {
            "type": "clarification_request",
            "payload": {
                "request_id": "rid-1",
                "question": "Which project?",
                "options": ["A", "B"],
            },
        }
    ]
    assert "rid-1" in pending

    assert resolve_clarification_response(pending, {"request_id": "rid-1", "answer": "B"})
    result = json.loads(await task)

    assert result == {"ok": True, "answer": "B"}
    assert pending == {}


@pytest.mark.asyncio
async def test_clarify_timeout():
    ws = _FakeWebSocket()
    pending: dict[str, asyncio.Future] = {}
    ask = build_clarification_ask(
        {"default": ws},
        pending,
        timeout_s=0.01,
        request_id_factory=lambda: "rid-timeout",
    )
    handler, _schema = build_ask_clarification_tool(ask)

    result = json.loads(await handler({"question": "Still there?"}, session_id="default"))

    assert result == {
        "ok": False,
        "reason": "user_did_not_respond_or_no_channel",
    }
    assert pending == {}


@pytest.mark.asyncio
async def test_clarify_not_cancelled_by_new_chat():
    ws = _FakeWebSocket()
    pending: dict[str, asyncio.Future] = {}
    chat_inflight: dict[str, asyncio.Task] = {}
    ask = build_clarification_ask(
        {"default": ws},
        pending,
        timeout_s=1.0,
        request_id_factory=lambda: "rid-chat-race",
    )

    ask_task = asyncio.create_task(ask("Pick one", [], "default"))
    await asyncio.sleep(0)

    old_chat_task = asyncio.create_task(asyncio.sleep(1))
    chat_inflight["default"] = old_chat_task

    assert "rid-chat-race" in pending
    assert not pending["rid-chat-race"].cancelled()

    # A new chat turn may cancel the old chat task, but clarification
    # pending lives on the independent control channel and must survive.
    chat_inflight["default"].cancel()

    assert not pending["rid-chat-race"].cancelled()
    assert resolve_clarification_response(
        pending,
        {"request_id": "rid-chat-race", "answer": "continue"},
    )
    assert await ask_task == "continue"
    assert pending == {}
