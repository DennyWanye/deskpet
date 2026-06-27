# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B1 — SessionGoalStore unit tests (PRD Stage B)."""
from __future__ import annotations

import time

from deskpet.agent.goal_store import SessionGoal, SessionGoalStore


def test_set_creates_goal_with_defaults():
    store = SessionGoalStore()
    before = time.time()
    g = store.set("sid-1", "write a poem")
    after = time.time()
    assert isinstance(g, SessionGoal)
    assert g.session_id == "sid-1"
    assert g.text == "write a poem"
    assert g.max_iterations == 10
    assert g.iterations_used == 0
    assert g.done is False
    assert before <= g.set_at <= after


def test_set_custom_max_iterations():
    store = SessionGoalStore()
    g = store.set("sid-1", "x", max_iterations=3)
    assert g.max_iterations == 3


def test_get_returns_none_when_unset():
    store = SessionGoalStore()
    assert store.get("missing-sid") is None


def test_get_returns_stored_goal():
    store = SessionGoalStore()
    store.set("sid-a", "goal A")
    g = store.get("sid-a")
    assert g is not None
    assert g.text == "goal A"


def test_set_overwrites_existing_goal_and_resets_counters():
    store = SessionGoalStore()
    g1 = store.set("sid-1", "old goal")
    store.increment_iteration("sid-1")
    store.increment_iteration("sid-1")
    store.mark_done("sid-1")
    assert g1.iterations_used == 2
    assert g1.done is True

    # 覆盖 — 新 goal 应该 fresh counter
    g2 = store.set("sid-1", "new goal")
    assert g2.text == "new goal"
    assert g2.iterations_used == 0
    assert g2.done is False
    # store.get 也应返回 fresh entry
    stored = store.get("sid-1")
    assert stored is g2


def test_clear_removes_goal_returns_true():
    store = SessionGoalStore()
    store.set("sid-1", "x")
    assert store.clear("sid-1") is True
    assert store.get("sid-1") is None


def test_clear_nonexistent_returns_false():
    store = SessionGoalStore()
    assert store.clear("nope") is False


def test_mark_done_sets_flag():
    store = SessionGoalStore()
    store.set("sid-1", "x")
    assert store.mark_done("sid-1") is True
    g = store.get("sid-1")
    assert g is not None and g.done is True


def test_mark_done_nonexistent_returns_false():
    store = SessionGoalStore()
    assert store.mark_done("nope") is False


def test_mark_done_is_idempotent():
    store = SessionGoalStore()
    store.set("sid-1", "x")
    assert store.mark_done("sid-1") is True
    assert store.mark_done("sid-1") is True


def test_increment_iteration_counts_up():
    store = SessionGoalStore()
    store.set("sid-1", "x")
    assert store.increment_iteration("sid-1") == 1
    assert store.increment_iteration("sid-1") == 2
    assert store.increment_iteration("sid-1") == 3
    g = store.get("sid-1")
    assert g is not None and g.iterations_used == 3


def test_increment_iteration_nonexistent_returns_zero():
    store = SessionGoalStore()
    assert store.increment_iteration("nope") == 0


def test_multiple_sessions_are_isolated():
    store = SessionGoalStore()
    store.set("sid-a", "goal A")
    store.set("sid-b", "goal B")
    assert store.get("sid-a").text == "goal A"
    assert store.get("sid-b").text == "goal B"
    store.mark_done("sid-a")
    assert store.get("sid-a").done is True
    assert store.get("sid-b").done is False
    store.clear("sid-a")
    assert store.get("sid-a") is None
    assert store.get("sid-b") is not None
