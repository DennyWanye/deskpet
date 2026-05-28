# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for ImageGenerationWorker (async fire-and-quick-return).

OpenSpec 2026-05-16-async-image-gen specs/async-tools/spec.md.
The slow _generate_png / _save_image / _open_file are monkeypatched —
no real httpx, no real file IO needed beyond tmp.
"""
from __future__ import annotations

import asyncio

import pytest

from deskpet.memory.image_worker import ImageGenerationWorker, ImageJob


@pytest.fixture
def notifier_spy():
    calls: list[tuple[str, str]] = []

    async def _n(sid: str, text: str) -> None:
        calls.append((sid, text))

    return calls, _n


def _patch_gen(monkeypatch, *, png=b"\x89PNG-fake", err=None, delay=0.0):
    import deskpet.tools.image_tools as it

    def _fake_gen(prompt, size, model):
        if delay:
            import time

            time.sleep(delay)
        return (png, err)

    monkeypatch.setattr(it, "_generate_png", _fake_gen)
    monkeypatch.setattr(it, "_save_image", lambda b: __import__("pathlib").Path(f"/tmp/genimg_x.png"))
    monkeypatch.setattr(it, "_open_file", lambda p: True)


@pytest.mark.asyncio
async def test_job_processed_success_notifies(notifier_spy, monkeypatch):
    calls, n = notifier_spy
    _patch_gen(monkeypatch)
    w = ImageGenerationWorker(n, max_concurrent=2)
    await w.start()
    try:
        status, jid = w.submit(
            session_id="default", prompt="一只猫", size="1024x1024", model="gpt-image-2"
        )
        assert status == "queued" and jid
        for _ in range(50):
            if calls:
                break
            await asyncio.sleep(0.05)
        assert len(calls) == 1
        sid, text = calls[0]
        assert sid == "default"
        assert "画好" in text and "gpt-image-2" in text
    finally:
        await w.stop()


@pytest.mark.asyncio
async def test_failure_notifies_graceful_no_crash(notifier_spy, monkeypatch):
    calls, n = notifier_spy
    _patch_gen(monkeypatch, png=None, err="the relay 连试 2 次仍失败（断连）")
    w = ImageGenerationWorker(n, max_concurrent=1)
    await w.start()
    try:
        w.submit(session_id="s1", prompt="x", size="1024x1024", model="m")
        for _ in range(50):
            if calls:
                break
            await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert "没画成" in calls[0][1] and "断连" in calls[0][1]
        # worker still alive after a failure → next job still works
        _patch_gen(monkeypatch)
        w.submit(session_id="s1", prompt="y", size="1024x1024", model="m")
        for _ in range(50):
            if len(calls) == 2:
                break
            await asyncio.sleep(0.05)
        assert len(calls) == 2 and "画好" in calls[1][1]
    finally:
        await w.stop()


@pytest.mark.asyncio
async def test_same_prompt_deduped(notifier_spy, monkeypatch):
    calls, n = notifier_spy
    _patch_gen(monkeypatch, delay=0.3)  # keep first in-flight
    w = ImageGenerationWorker(n, max_concurrent=2)
    await w.start()
    try:
        s1, _ = w.submit(session_id="d", prompt="P", size="S", model="m")
        s2, _ = w.submit(session_id="d", prompt="P", size="S", model="m")
        assert s1 == "queued"
        assert s2 == "already_generating"  # same (sid,prompt,size) in-flight
        for _ in range(60):
            if calls:
                break
            await asyncio.sleep(0.05)
        assert len(calls) == 1  # only one actually generated
    finally:
        await w.stop()


@pytest.mark.asyncio
async def test_dedup_key_stable():
    a = ImageJob(session_id="d", prompt="P", size="1024x1024", model="m")
    b = ImageJob(session_id="d", prompt="P", size="1024x1024", model="m2")
    c = ImageJob(session_id="d", prompt="Q", size="1024x1024", model="m")
    assert a.dedup_key() == b.dedup_key()  # model not part of key
    assert a.dedup_key() != c.dedup_key()  # prompt is


@pytest.mark.asyncio
async def test_start_stop_idempotent(notifier_spy):
    _, n = notifier_spy
    w = ImageGenerationWorker(n)
    await w.start()
    await w.start()  # double start = no-op, no crash
    await w.stop()
    await w.stop()  # double stop safe


@pytest.mark.asyncio
async def test_submit_before_start_unavailable(notifier_spy):
    _, n = notifier_spy
    w = ImageGenerationWorker(n)
    status, jid = w.submit(
        session_id="d", prompt="x", size="s", model="m"
    )
    assert status == "unavailable" and jid == ""


@pytest.mark.asyncio
async def test_concurrency_capped(notifier_spy, monkeypatch):
    calls, n = notifier_spy
    import deskpet.tools.image_tools as it

    live = {"now": 0, "peak": 0}

    def _slow_gen(prompt, size, model):
        import time

        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.25)
        live["now"] -= 1
        return (b"PNG", None)

    monkeypatch.setattr(it, "_generate_png", _slow_gen)
    monkeypatch.setattr(it, "_save_image", lambda b: __import__("pathlib").Path("/tmp/g.png"))
    monkeypatch.setattr(it, "_open_file", lambda p: True)

    w = ImageGenerationWorker(n, max_concurrent=2)
    await w.start()
    try:
        for i in range(4):
            w.submit(session_id="d", prompt=f"P{i}", size="s", model="m")
        for _ in range(120):
            if len(calls) == 4:
                break
            await asyncio.sleep(0.05)
        assert len(calls) == 4
        assert live["peak"] <= 2  # never more than max_concurrent at once
    finally:
        await w.stop()
