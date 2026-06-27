# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations


def test_resolve_new_strips_prefix_and_creates_deterministic_sid():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve("default", "/new research rust tokio", explicit_new=False)

    assert decision.effective_sid == "task-default-1"
    assert decision.created is True
    assert decision.reason == "explicit_new"
    assert decision.stripped_text == "research rust tokio"
    assert manager.active_sid("default") == "task-default-1"


def test_resolve_payload_new_without_prefix_keeps_text():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve("default", "research rust tokio", explicit_new=True)

    assert decision.effective_sid == "task-default-1"
    assert decision.created is True
    assert decision.reason == "explicit_new"
    assert decision.stripped_text == "research rust tokio"


def test_resolve_continue_strips_prefix_and_forces_l2():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve("default", "/continue compare again", explicit_new=False)

    assert decision.effective_sid == "default"
    assert decision.created is False
    assert decision.reason == "continue"
    assert decision.stripped_text == "compare again"
    assert decision.force_l2_page_in == "always"


def test_resolve_force_l2_marks_continue_without_stripping_text():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve(
        "default",
        "compare again",
        explicit_new=False,
        force_l2=True,
    )

    assert decision.effective_sid == "default"
    assert decision.reason == "continue"
    assert decision.stripped_text == "compare again"
    assert decision.force_l2_page_in == "always"


def test_resolve_default_is_bc_identity():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve("default", "hello", explicit_new=False)

    assert decision.effective_sid == "default"
    assert decision.created is False
    assert decision.reason == "default"
    assert decision.stripped_text == "hello"
    assert decision.force_l2_page_in is None


def test_peer_group_remaps_default_peers_together():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()
    manager.register_peer("default")
    manager.register_peer("message-panel-main")

    decision = manager.resolve("default", "/new topic b", explicit_new=False)
    manager.remap_peer_group("default", decision.effective_sid)

    assert manager.peer_group("default") == "task-default-1"
    assert manager.peer_group("message-panel-main") == "task-default-1"
    assert manager.peers_for_group("task-default-1") == [
        "default",
        "message-panel-main",
    ]

