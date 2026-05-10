"""P5-S1: SessionActivityStore unit tests."""
from __future__ import annotations

import asyncio
import time

import pytest

from agent.session_activity import (
    SessionActivityStore,
    args_hash,
    _RECENT_EVENTS_CAP,
)


@pytest.fixture
def store() -> SessionActivityStore:
    return SessionActivityStore()


@pytest.mark.asyncio
async def test_bump_creates_entry(store: SessionActivityStore):
    await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "ls"})
    sa = await store.get("sid-A")
    assert sa is not None
    assert sa.session_id == "sid-A"
    assert len(sa.recent_events) == 1
    assert sa.recent_events[-1].name == "bash_run"


@pytest.mark.asyncio
async def test_recent_events_caps_at_5(store: SessionActivityStore):
    for i in range(_RECENT_EVENTS_CAP + 3):
        await store.bump(
            "sid-A", event_type="tool_call", name=f"tool_{i}", args={"i": i}
        )
    sa = await store.get("sid-A")
    assert sa is not None
    assert len(sa.recent_events) == _RECENT_EVENTS_CAP
    # First 3 evicted (FIFO)
    names = [e.name for e in sa.recent_events]
    assert names == ["tool_3", "tool_4", "tool_5", "tool_6", "tool_7"]


@pytest.mark.asyncio
async def test_signature_window_consecutive_count(store: SessionActivityStore):
    # 3 consecutive same-signature calls
    for _ in range(3):
        await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "pip install foo"})
    sa = await store.get("sid-A")
    assert sa is not None
    sig = f"bash_run:{args_hash({'cmd': 'pip install foo'})}"
    assert sa.tool_signature_window.get(sig) == 3


@pytest.mark.asyncio
async def test_signature_window_resets_on_different(store: SessionActivityStore):
    await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "a"})
    await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "a"})
    # Different signature → resets window
    await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "b"})
    sa = await store.get("sid-A")
    assert sa is not None
    sig_b = f"bash_run:{args_hash({'cmd': 'b'})}"
    assert sa.tool_signature_window == {sig_b: 1}


@pytest.mark.asyncio
async def test_signature_window_resets_on_assistant_message(store: SessionActivityStore):
    await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "a"})
    await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "a"})
    # Non-tool event resets entirely
    await store.bump("sid-A", event_type="assistant_message")
    sa = await store.get("sid-A")
    assert sa is not None
    assert sa.tool_signature_window == {}


@pytest.mark.asyncio
async def test_drop_removes_entry(store: SessionActivityStore):
    await store.bump("sid-A", event_type="tool_call", name="t", args={})
    await store.drop("sid-A")
    assert await store.get("sid-A") is None


@pytest.mark.asyncio
async def test_mark_error_pending_sets_status(store: SessionActivityStore):
    await store.bump("sid-A", event_type="tool_call", name="t", args={})
    await store.mark_error_pending("sid-A")
    sa = await store.get("sid-A")
    assert sa is not None
    assert sa.error_pending_supervisor is True
    assert sa.status == "error"


@pytest.mark.asyncio
async def test_clear_error_pending(store: SessionActivityStore):
    await store.mark_error_pending("sid-A")
    await store.clear_error_pending("sid-A")
    sa = await store.get("sid-A")
    assert sa is not None
    assert sa.error_pending_supervisor is False


@pytest.mark.asyncio
async def test_snapshot_dict_shape(store: SessionActivityStore):
    await store.bump("sid-A", event_type="tool_call", name="bash_run", args={"cmd": "x"}, iteration=3, max_iterations=50)
    sa = await store.get("sid-A")
    assert sa is not None
    snap = sa.to_snapshot_dict()
    # No raw conversation
    assert "messages" not in snap
    # Required fields
    assert snap["session_id"] == "sid-A"
    assert snap["current_iteration"] == 3
    assert snap["max_iterations"] == 50
    assert "last_5_events" in snap
    assert "tool_signature_window" in snap
    assert "last_activity_age_seconds" in snap
    assert snap["last_activity_age_seconds"] >= 0


def test_args_hash_stable_across_key_order():
    a = args_hash({"a": 1, "b": 2})
    b = args_hash({"b": 2, "a": 1})
    assert a == b


def test_args_hash_handles_unencodable():
    """Falls back to str() representation rather than raising."""
    class Custom:
        def __str__(self):
            return "Custom()"

    h1 = args_hash(Custom())
    h2 = args_hash(Custom())
    assert h1 == h2  # str() output is stable


@pytest.mark.asyncio
async def test_concurrent_bumps_are_safe(store: SessionActivityStore):
    """asyncio.Lock prevents race conditions on shared dict mutation."""
    async def hammer(idx: int):
        for _ in range(10):
            await store.bump(
                f"sid-{idx % 3}",
                event_type="tool_call",
                name=f"tool_{idx}",
                args={"idx": idx},
            )

    await asyncio.gather(*(hammer(i) for i in range(20)))
    # 3 distinct sids should exist
    sids = await store.all_sids()
    assert len(sids) == 3
