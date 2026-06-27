# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-0.4 — context.py 子代理服务槽注册。"""
from __future__ import annotations

import pytest

from context import _VALID_SERVICES, ServiceContext

_NEW_SLOTS = (
    "subagent_scheduler",
    "subagent_registry",
    "team_store",
    "task_graph_store",
)


def test_register_get_new_slots():  # 0.4.1 / 0.4.2
    ctx = ServiceContext()
    for slot in _NEW_SLOTS:
        marker = object()
        ctx.register(slot, marker)
        assert ctx.get(slot) is marker


def test_unknown_service_still_raises():  # 0.4.3
    ctx = ServiceContext()
    with pytest.raises(ValueError):
        ctx.register("definitely_not_a_service", object())
    with pytest.raises(ValueError):
        ctx.get("definitely_not_a_service")


def test_valid_services_superset():  # 0.4.4
    for slot in _NEW_SLOTS:
        assert slot in _VALID_SERVICES
    # 旧槽未丢
    for slot in ("session_goal_store", "session_db", "vector_worker"):
        assert slot in _VALID_SERVICES


def test_default_slots_none():  # 多做：默认 None
    ctx = ServiceContext()
    for slot in _NEW_SLOTS:
        assert ctx.get(slot) is None
