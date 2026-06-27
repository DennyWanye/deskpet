from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


class _FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_broadcast_control_is_best_effort(monkeypatch):
    import main

    ok = _FakeWebSocket()
    bad = _FakeWebSocket(fail=True)
    monkeypatch.setattr(main, "_control_connections", {"ok": ok, "bad": bad})

    await main._broadcast_control({"type": "ppt_outline_proposed", "payload": {"outline_id": "o1"}})

    assert ok.sent == [{"type": "ppt_outline_proposed", "payload": {"outline_id": "o1"}}]


@pytest.mark.asyncio
async def test_ppt_outline_propose_accept_broadcasts_history_and_marks_status(monkeypatch):
    import main
    from deskpet.tools.ppt_tools import SlideOutline

    waiters = main.PPTOutlineWaiters()
    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", waiters)
    monkeypatch.setattr(main, "_ppt_pro_cfg", lambda: SimpleNamespace(outline_history=True, confirm_timeout_s=1))
    monkeypatch.setattr(main.uuid, "uuid4", lambda: SimpleNamespace(hex="outline-1"))

    saved = []
    statuses = []
    monkeypatch.setattr(main, "save_outline", lambda *a, **k: saved.append(a) or True)
    monkeypatch.setattr(main, "list_history", lambda sid, limit=20: [{"outline_id": "old"}])
    monkeypatch.setattr(main, "mark_status", lambda oid, status: statuses.append((oid, status)) or True)

    ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {"panel": ws})

    task = asyncio.create_task(
        main._ppt_outline_propose(
            "sid-1",
            topic="Topic",
            slides=[SlideOutline(title="Slide 1")],
            sources_count=3,
            outline_md="# Outline",
            no_research=False,
        )
    )
    await asyncio.sleep(0)

    assert ws.sent[0]["type"] == "ppt_outline_proposed"
    payload = ws.sent[0]["payload"]
    assert payload["outline_id"] == "outline-1"
    assert payload["session_id"] == "sid-1"
    assert payload["history"] == [{"outline_id": "old"}]
    assert saved and saved[0][0:4] == ("outline-1", "sid-1", "Topic", saved[0][3])

    assert waiters.resolve("outline-1", {"action": "accept"})
    assert await task == {"action": "accept"}
    assert statuses == [("outline-1", "accepted")]
    assert waiters.pop("outline-1") is None


@pytest.mark.asyncio
async def test_ppt_outline_propose_timeout_cancels(monkeypatch):
    import main
    from deskpet.tools.ppt_tools import SlideOutline

    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", main.PPTOutlineWaiters())
    monkeypatch.setattr(main, "_ppt_pro_cfg", lambda: SimpleNamespace(outline_history=False, confirm_timeout_s=0.01))
    monkeypatch.setattr(main.uuid, "uuid4", lambda: SimpleNamespace(hex="outline-timeout"))
    monkeypatch.setattr(main, "mark_status", lambda *a, **k: True)
    monkeypatch.setattr(main, "_control_connections", {"panel": _FakeWebSocket()})

    decision = await main._ppt_outline_propose(
        "sid-1",
        topic="Topic",
        slides=[SlideOutline(title="Slide 1")],
        sources_count=0,
        outline_md="# Outline",
        no_research=True,
    )

    assert decision == {"action": "cancel"}


@pytest.mark.asyncio
async def test_control_ws_ppt_outline_decision_resolves_once(monkeypatch):
    import main

    waiters = main.PPTOutlineWaiters()
    fut = asyncio.get_running_loop().create_future()
    waiters.add("outline-1", fut)
    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", waiters)
    ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {"panel": ws})

    handled = await main._handle_control_ws_message(
        {"type": "ppt_outline_decision", "payload": {"outline_id": "outline-1", "action": "accept"}},
        session_id="sid-1",
        ws=ws,
    )

    assert handled is True
    assert fut.result() == {"action": "accept", "feedback": "", "reuse_id": None}
    assert ws.sent == [
        {"type": "ppt_outline_resolved", "payload": {"outline_id": "outline-1"}}
    ]

    assert await main._handle_control_ws_message(
        {"type": "ppt_outline_decision", "payload": {"outline_id": "outline-1", "action": "cancel"}},
        session_id="sid-1",
        ws=ws,
    )
    assert len(ws.sent) == 1


@pytest.mark.asyncio
async def test_ppt_artifact_push_uses_tool_result_and_sessiondb(monkeypatch):
    import main

    ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {"sid-1": ws})
    rows = []

    class _SDB:
        async def append_message(self, **kwargs):
            rows.append(kwargs)
            return 123

    monkeypatch.setattr(main.service_context, "get", lambda name: _SDB() if name == "session_db" else None)

    artifacts = [{"kind": "file", "path": "deck.pptx"}]
    await main._ppt_artifact_push("sid-1", artifacts, "done")

    assert ws.sent[0]["type"] == "tool_result"
    payload = ws.sent[0]["payload"]
    assert payload["tool"] == "ppt_pro"
    assert payload["artifacts"] == artifacts
    assert payload["session_id"] == "sid-1"
    assert payload["text"] == "done"
    assert json.loads(rows[0]["content"])["artifacts"] == artifacts
    assert rows[0]["role"] == "tool"


def test_ppt_outline_startup_expire_is_called(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main, "expire_dangling_proposed", lambda: calls.append("expire") or 2)

    assert main._expire_ppt_outline_dangling_for_startup() == 2
    assert calls == ["expire"]


def test_ppt_pro_startup_wires_all_services(monkeypatch):
    import main

    seen = {}
    monkeypatch.setattr(main.ppt_tools, "set_ppt_pro_services", lambda **kwargs: seen.update(kwargs))

    main._wire_ppt_pro_services_for_startup()

    assert seen["outline_propose"] is main._ppt_outline_propose
    assert seen["notifier"] is main._ppt_notify_chat_bubble
    assert seen["artifact_pusher"] is main._ppt_artifact_push
    assert seen["receipt_reporter"] is main._ppt_receipt_report
    assert callable(seen["run_blocking"])
