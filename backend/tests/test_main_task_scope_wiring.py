# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import pytest


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


def test_main_chat_scope_helper_resolves_new_and_remaps_group(monkeypatch):
    import main
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()
    manager.register_peer("default")
    manager.register_peer("message-panel-main")
    monkeypatch.setattr(main, "task_session_manager", manager)
    monkeypatch.setattr(
        main,
        "_chat_peer_groups",
        {
            "default": "default",
            "message-panel-main": "default",
        },
    )

    decision = main._resolve_chat_task_scope(
        base_sid="default",
        text="/new build a crawler",
        payload={},
    )

    assert decision.effective_sid == "task-default-1"
    assert decision.stripped_text == "build a crawler"
    assert main._chat_peer_groups == {
        "default": "task-default-1",
        "message-panel-main": "task-default-1",
    }


def test_main_chat_scope_helper_keeps_default_bc(monkeypatch):
    import main
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()
    monkeypatch.setattr(main, "task_session_manager", manager)
    monkeypatch.setattr(main, "_chat_peer_groups", {"default": "default"})

    decision = main._resolve_chat_task_scope(
        base_sid="default",
        text="plain chat",
        payload={},
    )

    assert decision.effective_sid == "default"
    assert decision.stripped_text == "plain chat"
    assert main._chat_peer_groups == {"default": "default"}


@pytest.mark.asyncio
async def test_main_broadcast_uses_new_sid_for_session_switch_events(monkeypatch):
    import main

    origin = _FakeWS()
    peer = _FakeWS()
    monkeypatch.setattr(
        main,
        "_control_connections",
        {"default": origin, "message-panel-main": peer},
    )
    monkeypatch.setattr(
        main,
        "_chat_peer_groups",
        {
            "default": "task-default-1",
            "message-panel-main": "task-default-1",
        },
    )
    evt = {
        "type": "session_switched",
        "payload": {
            "old_sid": "default",
            "new_sid": "task-default-1",
            "reason": "explicit_new",
        },
    }

    await main._broadcast_default_chat_peers(origin, evt)

    assert peer.sent == [evt]
