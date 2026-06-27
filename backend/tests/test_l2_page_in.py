# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deskpet.agent.assembler.assembler import ContextAssembler
from deskpet.agent.assembler.bundle import AssemblyPolicy, MemoryPolicy, TASK_TYPES
from deskpet.agent.assembler.classifier import TaskClassifier
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.memory import MemoryComponent
from deskpet.agent.assembler.policy import _to_policy, load_policies
from deskpet.agent.assembler.registry import ComponentRegistry


class _MemoryManager:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [{"role": "user", "content": "old l2"}])
        self.calls: list[dict[str, Any]] = []

    async def recall(self, query: str, policy: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(policy))
        l2_top_k = int(policy.get("l2_top_k", 0))
        return {
            "l1": {},
            "l2": self.rows[-l2_top_k:] if l2_top_k > 0 else [],
            "l3": [],
        }


def _ctx(
    memory_policy: MemoryPolicy,
    *,
    user_message: str = "please research rust tokio",
    mm: _MemoryManager | None = None,
) -> ComponentContext:
    return ComponentContext(
        task_type="task",
        policy=AssemblyPolicy(task_type="task", memory=memory_policy),
        user_message=user_message,
        session_id="default",
        memory_manager=mm or _MemoryManager(),
    )


@pytest.mark.asyncio
async def test_l2_page_in_always_fetches_l2_and_preserves_reasoning_content():
    rows = [
        {"role": "user", "content": "old question"},
        {
            "role": "assistant",
            "content": "old answer",
            "reasoning_content": "prior thinking",
        },
    ]
    mm = _MemoryManager(rows)

    sl = await MemoryComponent().provide(
        _ctx(MemoryPolicy(l2_top_k=2, l2_page_in="always"), mm=mm)
    )

    assert mm.calls[-1]["l2_top_k"] == 2
    assert sl.meta["l2_count"] == 2
    assert sl.meta["l2_history"][-1]["reasoning_content"] == "prior thinking"


@pytest.mark.asyncio
async def test_l2_page_in_off_skips_l2_with_top_k_zero():
    mm = _MemoryManager()

    sl = await MemoryComponent().provide(
        _ctx(MemoryPolicy(l2_top_k=5, l2_page_in="off"), mm=mm)
    )

    assert mm.calls[-1]["l2_top_k"] == 0
    assert sl.meta["l2_count"] == 0
    assert sl.meta["l2_history"] == []


@pytest.mark.asyncio
async def test_l2_page_in_followup_skips_non_anaphora_and_keeps_anaphora():
    component = MemoryComponent()
    non_followup = _MemoryManager()
    followup = _MemoryManager()

    await component.provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="followup"),
            user_message="please research a new database topic",
            mm=non_followup,
        )
    )
    kept = await component.provide(
        _ctx(
            MemoryPolicy(l2_top_k=4, l2_page_in="followup"),
            user_message="this one in more detail",
            mm=followup,
        )
    )

    assert non_followup.calls[-1]["l2_top_k"] == 0
    assert followup.calls[-1]["l2_top_k"] == 4
    assert kept.meta["l2_count"] == 1


@pytest.mark.asyncio
async def test_l2_top_k_zero_does_not_crash_with_reasoning_content_fixture():
    mm = _MemoryManager(
        [
            {
                "role": "assistant",
                "content": "old thinking answer",
                "reasoning_content": "prior thinking payload",
            }
        ]
    )

    sl = await MemoryComponent().provide(
        _ctx(MemoryPolicy(l2_top_k=3, l2_page_in="off"), mm=mm)
    )

    assert mm.calls[-1]["l2_top_k"] == 0
    assert sl.meta["l2_history"] == []


def test_to_policy_parses_l2_page_in_and_defaults_to_always():
    assert (
        _to_policy("task", {"memory": {"l2_page_in": "followup"}})
        .memory
        .l2_page_in
        == "followup"
    )
    assert _to_policy("task", {}).memory.l2_page_in == "always"


def test_load_policies_clone_preserves_l2_page_in_when_task_missing(tmp_path: Path):
    default_yaml = tmp_path / "default.yaml"
    default_yaml.write_text(
        """
policies:
  chat:
    must: [memory]
    memory:
      l1: snapshot
      l2_top_k: 7
      l3_top_k: 1
      l2_page_in: followup
""",
        encoding="utf-8",
    )

    policies = load_policies(default_path=default_yaml)

    assert all(tt in policies for tt in TASK_TYPES)
    assert policies["task"].memory.l2_page_in == "followup"


def test_default_profile_l2_page_in_values():
    policies = load_policies()

    assert policies["task"].memory.l2_page_in == "followup"
    assert policies["web_search"].memory.l2_page_in == "followup"
    assert policies["command"].memory.l2_page_in == "followup"
    for task_type in ("recall", "chat", "emotion", "plan", "code"):
        assert policies[task_type].memory.l2_page_in == "always"


@pytest.mark.asyncio
async def test_memory_policy_override_forces_l2_page_in_always():
    registry = ComponentRegistry()
    registry.register(MemoryComponent())
    mm = _MemoryManager()
    assembler = ContextAssembler(
        component_registry=registry,
        policies={
            "task": AssemblyPolicy(
                task_type="task",
                must=["memory"],
                prefer=[],
                memory=MemoryPolicy(l2_top_k=2, l2_page_in="off"),
            )
        },
        classifier=TaskClassifier(embedder=None),
    )

    bundle = await assembler.assemble(
        "please research a new database topic",
        memory_manager=mm,
        session_id="default",
        task_type_override="task",
        memory_policy_override={"l2_page_in": "always"},
    )

    assert mm.calls[-1]["l2_top_k"] == 2
    assert bundle.history
