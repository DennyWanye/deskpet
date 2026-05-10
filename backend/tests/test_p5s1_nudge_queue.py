"""P5-S4: NudgeQueue tests."""
from __future__ import annotations

import asyncio

import pytest

from agent.nudge_queue import NudgeQueue, Hint, format_hints_for_injection


@pytest.mark.asyncio
async def test_push_pop_roundtrip():
    q = NudgeQueue()
    await q.push("sid", Hint(text="a", alert_id="1"))
    await q.push("sid", Hint(text="b", alert_id="2"))
    hints = await q.pop_all("sid")
    assert [h.text for h in hints] == ["a", "b"]


@pytest.mark.asyncio
async def test_pop_clears_queue():
    q = NudgeQueue()
    await q.push("sid", Hint(text="a"))
    _ = await q.pop_all("sid")
    assert await q.peek("sid") is False
    assert await q.size("sid") == 0


@pytest.mark.asyncio
async def test_cap_drops_oldest():
    q = NudgeQueue(cap=3)
    for i in range(5):
        await q.push("sid", Hint(text=str(i), alert_id=f"a{i}"))
    hints = await q.pop_all("sid")
    # Oldest 2 dropped → keep 2,3,4
    assert [h.text for h in hints] == ["2", "3", "4"]


@pytest.mark.asyncio
async def test_clear_specific_sid():
    q = NudgeQueue()
    await q.push("a", Hint(text="x"))
    await q.push("b", Hint(text="y"))
    await q.clear("a")
    assert await q.peek("a") is False
    assert await q.peek("b") is True


@pytest.mark.asyncio
async def test_clear_all():
    q = NudgeQueue()
    await q.push("a", Hint(text="x"))
    await q.push("b", Hint(text="y"))
    await q.clear()
    assert await q.peek("a") is False
    assert await q.peek("b") is False


@pytest.mark.asyncio
async def test_concurrent_push_safe():
    """asyncio.Lock prevents lost updates under concurrent push."""
    q = NudgeQueue(cap=100)

    async def hammer(idx: int):
        for j in range(10):
            await q.push("sid", Hint(text=f"{idx}-{j}", alert_id=f"a{idx}-{j}"))

    await asyncio.gather(*(hammer(i) for i in range(10)))
    hints = await q.pop_all("sid")
    assert len(hints) == 100  # All 10 tasks × 10 pushes preserved


@pytest.mark.asyncio
async def test_peek_nonconsuming():
    q = NudgeQueue()
    await q.push("sid", Hint(text="x"))
    assert await q.peek("sid") is True
    assert await q.peek("sid") is True  # still there
    hints = await q.pop_all("sid")
    assert len(hints) == 1


def test_format_single_hint():
    h = Hint(text="switch pip mirror", alert_id="a1")
    s = format_hints_for_injection([h])
    assert s == "[Supervisor] switch pip mirror"


def test_format_multiple_hints():
    s = format_hints_for_injection([
        Hint(text="one"),
        Hint(text="two"),
    ])
    assert s.startswith("[Supervisor]")
    assert "- one" in s
    assert "- two" in s


def test_format_empty_list_returns_empty():
    assert format_hints_for_injection([]) == ""
