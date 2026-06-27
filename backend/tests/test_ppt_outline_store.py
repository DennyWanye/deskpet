import asyncio
import dataclasses
import json
import sqlite3

from deskpet.tools.ppt_tools import SlideOutline, parse_outline


def _conn_factory(db_path):
    def _connect():
        return sqlite3.connect(db_path)

    return _connect


def test_ppt_outline_history_crud_and_round_trip(tmp_path):
    from deskpet.tools.ppt_outline_store import (
        ensure_ppt_outline_table,
        expire_dangling_proposed,
        get_outline,
        list_history,
        mark_status,
        save_outline,
    )

    db_path = tmp_path / "state.db"
    conn_factory = _conn_factory(db_path)
    slides = [
        SlideOutline(
            layout="bullet",
            title="封面",
            subtitle="PPT Pro",
            bullets=["研究背景", "关键问题"],
            image_prompt="clean product strategy cover",
        ),
        SlideOutline(
            layout="two_column",
            title="对比",
            left_title="现在",
            left=["手工收集资料"],
            right_title="Pro",
            right=["调研后拟纲"],
            notes="speaker note",
        ),
    ]

    with conn_factory() as conn:
        ensure_ppt_outline_table(conn)

    assert save_outline(
        "outline-1",
        "sid-1",
        "PPT Pro 计划",
        slides,
        7,
        conn_factory=conn_factory,
    )

    row = get_outline("outline-1", conn_factory=conn_factory)
    assert row is not None
    assert row["outline_id"] == "outline-1"
    assert row["session_id"] == "sid-1"
    assert row["topic"] == "PPT Pro 计划"
    assert row["sources_count"] == 7
    assert row["status"] == "proposed"
    assert "T" in row["created_at"]
    assert json.loads(row["slides_json"]) == [dataclasses.asdict(s) for s in slides]

    restored = parse_outline(row["slides_json"])
    assert [dataclasses.asdict(s) for s in restored] == [
        dataclasses.asdict(s.normalize()) for s in slides
    ]

    assert list_history("sid-1", conn_factory=conn_factory) == [
        {
            "outline_id": "outline-1",
            "topic": "PPT Pro 计划",
            "created_at": row["created_at"],
            "sources_count": 7,
            "status": "proposed",
        }
    ]

    assert mark_status("outline-1", "accepted", conn_factory=conn_factory)
    assert get_outline("outline-1", conn_factory=conn_factory)["status"] == "accepted"

    assert save_outline(
        "outline-2",
        "sid-1",
        "另一个大纲",
        [SlideOutline(title="待处理")],
        0,
        conn_factory=conn_factory,
    )
    assert expire_dangling_proposed(conn_factory=conn_factory) == 1
    assert get_outline("outline-1", conn_factory=conn_factory)["status"] == "accepted"
    assert get_outline("outline-2", conn_factory=conn_factory)["status"] == "expired"


def test_ppt_outline_history_degrades_when_db_unavailable(caplog):
    from deskpet.tools.ppt_outline_store import (
        expire_dangling_proposed,
        get_outline,
        list_history,
        mark_status,
        save_outline,
    )

    def broken_factory():
        raise sqlite3.OperationalError("cannot open database")

    assert save_outline(
        "outline-1",
        "sid-1",
        "topic",
        [SlideOutline(title="x")],
        1,
        conn_factory=broken_factory,
    ) is False
    assert mark_status("outline-1", "accepted", conn_factory=broken_factory) is False
    assert list_history("sid-1", conn_factory=broken_factory) == []
    assert get_outline("outline-1", conn_factory=broken_factory) is None
    assert expire_dangling_proposed(conn_factory=broken_factory) == 0
    assert "ppt outline" in caplog.text.lower()


def test_ppt_outline_waiters_are_idempotent():
    from deskpet.tools.ppt_outline_store import PPTOutlineWaiters

    loop = asyncio.new_event_loop()
    try:
        waiters = PPTOutlineWaiters()
        fut = loop.create_future()
        decision = {"action": "accept"}

        waiters.add("outline-1", fut)
        assert waiters.resolve("outline-1", decision) is True
        assert fut.done()
        assert fut.result() == decision

        assert waiters.resolve("outline-1", {"action": "reject"}) is False
        popped = waiters.pop("outline-1")
        assert popped is fut
        assert waiters.pop("outline-1") is None
        assert waiters.resolve("outline-1", decision) is False

        done = loop.create_future()
        done.set_result({"action": "already-done"})
        waiters.add("outline-2", done)
        assert waiters.resolve("outline-2", {"action": "accept"}) is False
        assert done.result() == {"action": "already-done"}
    finally:
        loop.close()
