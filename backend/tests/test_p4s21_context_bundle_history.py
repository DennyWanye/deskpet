"""P4-S21 #16 — ContextBundle.history is populated from L2 raw rows
and consumed by build_messages as real OpenAI messages[].

Regression guard for the "VPN bug": user said "let's build a VPN"; 5
turns later the pet asked "what do you want to do?". Root cause was
that L2 was rendered into a system-prompt text block instead of being
passed through as conversation history.
"""
from __future__ import annotations

import pytest

from deskpet.agent.assembler.bundle import ContextBundle
from deskpet.agent.assembler.components.memory import (
    MemoryComponent,
    _render_l2_l3,
    _render_l3_only,
)


# ---------------------------------------------------------------------------
# build_messages: history flows through verbatim
# ---------------------------------------------------------------------------


def test_history_field_default_empty():
    b = ContextBundle(task_type="chat")
    assert b.history == []


def test_build_messages_with_history_appends_user_assistant_turns():
    b = ContextBundle(
        task_type="chat",
        frozen_system="you are a desktop pet",
    )
    b.history = [
        {"role": "user", "content": "let's build a VPN"},
        {"role": "assistant", "content": "what flavor — wireguard or openvpn?"},
        {"role": "user", "content": "wireguard please"},
    ]
    msgs = b.build_messages(
        user_message="ok let's start",
        history=b.history,
    )
    # Expect: system, ...history..., final user
    assert msgs[0]["role"] == "system"
    assert msgs[1] == b.history[0]
    assert msgs[2] == b.history[1]
    assert msgs[3] == b.history[2]
    assert msgs[-1] == {"role": "user", "content": "ok let's start"}


def test_build_messages_without_history_skips_history_block():
    b = ContextBundle(task_type="chat", frozen_system="x")
    msgs = b.build_messages(user_message="hello")
    # Only the system + final user; no orphan user/assistant turns
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user"]


# ---------------------------------------------------------------------------
# MemoryComponent: stuffs L2 raw rows into slice.meta["l2_history"]
# ---------------------------------------------------------------------------


class _StubMemoryManager:
    def __init__(self, l1=None, l2=None, l3=None):
        self._l1 = l1
        self._l2 = l2 or []
        self._l3 = l3 or []

    async def recall(self, query, policy):
        return {"l1": self._l1, "l2": self._l2, "l3": self._l3}


class _StubPolicy:
    def __init__(self):
        self.memory = type("M", (), {"l1": "snapshot", "l2_top_k": 10, "l3_top_k": 5})()


class _StubCtx:
    def __init__(self, mm):
        self.memory_manager = mm
        self.policy = _StubPolicy()
        self.session_id = "default"
        self.user_message = "hello"


@pytest.mark.asyncio
async def test_memory_component_promotes_l2_to_meta_l2_history():
    l2_rows = [
        {"role": "user", "content": "我希望你帮我做一个VPN的项目"},
        {"role": "assistant", "content": "好的，你想要什么类型？"},
        {"role": "user", "content": "企业版，重点稳定"},
    ]
    comp = MemoryComponent()
    sl = await comp.provide(_StubCtx(_StubMemoryManager(l2=l2_rows)))
    assert "l2_history" in sl.meta
    assert sl.meta["l2_history"] == [
        {"role": "user", "content": "我希望你帮我做一个VPN的项目"},
        {"role": "assistant", "content": "好的，你想要什么类型？"},
        {"role": "user", "content": "企业版，重点稳定"},
    ]


@pytest.mark.asyncio
async def test_memory_component_skips_empty_or_unknown_role_rows():
    l2_rows = [
        {"role": "user", "content": "kept"},
        {"role": "assistant", "content": ""},  # empty → drop
        {"role": "weird-role", "content": "drop me"},  # unknown → drop
        {"role": "system", "content": "[summary] kept"},  # system summaries kept
    ]
    comp = MemoryComponent()
    sl = await comp.provide(_StubCtx(_StubMemoryManager(l2=l2_rows)))
    keep = sl.meta["l2_history"]
    assert {"role": "user", "content": "kept"} in keep
    assert {"role": "system", "content": "[summary] kept"} in keep
    assert all(h["role"] != "weird-role" for h in keep)
    assert all(h["content"] for h in keep)


@pytest.mark.asyncio
async def test_memory_component_text_block_no_longer_includes_l2():
    """L2 was previously rendered into the text block too — now only L3.
    Token budget regression guard: we shouldn't double-charge by
    including L2 both as messages AND as system text."""
    l2_rows = [{"role": "user", "content": "很重要的上下文"}]
    l3_hits = [{"text": "older recall", "score": 0.9, "source": "vec"}]
    comp = MemoryComponent()
    sl = await comp.provide(_StubCtx(_StubMemoryManager(l2=l2_rows, l3=l3_hits)))
    # L2 content NOT in the text block
    assert "很重要的上下文" not in sl.text_content
    # L3 is still rendered (semantic recall stays as text)
    assert "older recall" in sl.text_content
    # And L2 lives in meta history instead
    assert sl.meta["l2_history"] == [{"role": "user", "content": "很重要的上下文"}]


# ---------------------------------------------------------------------------
# Backwards compat — _render_l2_l3 keeps working but ignores L2
# ---------------------------------------------------------------------------


def test_render_l2_l3_legacy_ignores_l2_now():
    out = _render_l2_l3(
        [{"role": "user", "content": "should not appear"}],
        [{"text": "appears", "score": 0.8, "source": "vec"}],
    )
    assert "should not appear" not in out
    assert "appears" in out


def test_render_l3_only_handles_empty_input():
    assert _render_l3_only([]) == ""
