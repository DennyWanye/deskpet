# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ask_clarification tool and control-channel helpers."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any


_SCHEMA = {
    "name": "ask_clarification",
    "description": "当用户意图不清时，向用户提问澄清并等待其回答后再继续。阻塞式：返回用户的回答。",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "向用户提的澄清问题(简短1-2句)",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选项(可空)",
            },
        },
        "required": ["question"],
    },
}


def resolve_clarification_response(
    pending: dict[str, asyncio.Future],
    payload: dict[str, Any],
) -> bool:
    """Resolve a pending clarification future from a control WS payload."""

    request_id = str(payload.get("request_id") or "")
    fut = pending.get(request_id)
    if fut is None or fut.done():
        return False
    fut.set_result(str(payload.get("answer") or ""))
    return True


def build_clarification_ask(
    control_connections: dict[str, Any],
    pending: dict[str, asyncio.Future],
    *,
    timeout_s: float = 120.0,
    request_id_factory: Callable[[], str] | None = None,
):
    """Build the async sender used by ask_clarification.

    The returned callable sends a clarification_request on the independent
    control WebSocket and waits for the matching clarification_response.
    """

    make_request_id = request_id_factory or (lambda: str(uuid.uuid4()))

    async def _ask(question: str, options: list[str] | None, session_id: str) -> str:
        ws = control_connections.get(session_id) or control_connections.get("default")
        if ws is None:
            return ""

        request_id = make_request_id()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        pending[request_id] = fut
        try:
            await ws.send_json(
                {
                    "type": "clarification_request",
                    "payload": {
                        "request_id": request_id,
                        "question": question,
                        "options": list(options or []),
                    },
                }
            )
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return ""
        finally:
            pending.pop(request_id, None)

    return _ask


def build_ask_clarification_tool(ask_fn):
    """Build the ask_clarification tool handler and schema."""

    async def _handler(args, task_id="", session_id="default"):
        sid = str(args.get("_session_id") or args.get("session_id") or session_id)
        answer = await ask_fn(
            args.get("question", ""),
            args.get("options", []),
            sid,
        )
        if answer:
            return json.dumps({"ok": True, "answer": answer}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "reason": "user_did_not_respond_or_no_channel"},
            ensure_ascii=False,
        )

    return _handler, _SCHEMA
