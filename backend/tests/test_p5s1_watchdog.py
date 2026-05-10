"""P5-S1: WatchdogLoop unit tests."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agent.session_activity import SessionActivityStore
from agent.watchdog import WatchdogLoop


class _FakeCMM:
    """Stand-in for CodeModeManager."""

    def __init__(self, sids: list[str]):
        self._sids = sids

    def all_sessions(self) -> dict[str, Any]:
        class _S:
            def __init__(self, enabled: bool = True):
                self.enabled = enabled
                self.code_session_id = "code-x"
                self.project_root = "/tmp/x"
                self.project_name = "x"

        return {sid: _S(True) for sid in self._sids}


@pytest.fixture
def store() -> SessionActivityStore:
    return SessionActivityStore()


@pytest.mark.asyncio
async def test_inactivity_trigger_fires(store: SessionActivityStore):
    """A session inactive past stuck_threshold + status=running → trigger."""
    sid = "code-aaa"
    # Seed an old activity timestamp (16 minutes ago)
    await store.bump(sid, event_type="assistant_message")
    sa = await store.get(sid)
    assert sa is not None
    sa.last_event_ts = time.time() - 16 * 60  # 16 min old
    sa.status = "running"

    triggered: list[tuple[str, dict]] = []

    async def hook(s: str, snap: dict) -> None:
        triggered.append((s, snap))

    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=_FakeCMM([sid]),
        hook=hook,
        scan_interval_seconds=0.05,
        stuck_threshold_seconds=900,
        dedup_seconds=720,
        startup_grace_seconds=0.0,
    )
    # Manually tick (don't start the long-running task)
    await wd._tick()
    assert len(triggered) == 1
    assert triggered[0][0] == sid


@pytest.mark.asyncio
async def test_dedup_prevents_repeat(store: SessionActivityStore):
    sid = "code-bbb"
    await store.bump(sid, event_type="assistant_message")
    sa = await store.get(sid)
    assert sa is not None
    sa.last_event_ts = time.time() - 16 * 60
    sa.status = "running"

    fire_count = 0

    async def hook(s: str, snap: dict) -> None:
        nonlocal fire_count
        fire_count += 1

    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=_FakeCMM([sid]),
        hook=hook,
        scan_interval_seconds=0.05,
        stuck_threshold_seconds=900,
        dedup_seconds=720,
        startup_grace_seconds=0.0,
    )
    await wd._tick()
    await wd._tick()  # Same sid; should be deduped
    assert fire_count == 1


@pytest.mark.asyncio
async def test_error_pending_triggers_immediately(store: SessionActivityStore):
    """chat_v2_error fires the watchdog even before the inactivity threshold."""
    sid = "code-ccc"
    await store.bump(sid, event_type="error", snippet="ConnectError")
    await store.mark_error_pending(sid)
    # Activity is FRESH (just bumped) but error_pending should still fire
    sa = await store.get(sid)
    assert sa is not None
    sa.last_event_ts = time.time()  # Just now
    sa.error_pending_supervisor = True

    triggered = []

    async def hook(s: str, snap: dict) -> None:
        triggered.append(s)

    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=_FakeCMM([sid]),
        hook=hook,
        startup_grace_seconds=0.0,
    )
    await wd._tick()
    assert triggered == [sid]


@pytest.mark.asyncio
async def test_hook_exception_isolated(store: SessionActivityStore):
    """A hook crash MUST NOT abort the loop or leak past the tick."""
    sid = "code-ddd"
    await store.bump(sid, event_type="assistant_message")
    sa = await store.get(sid)
    sa.last_event_ts = time.time() - 1000
    sa.status = "running"

    async def crashing_hook(s: str, snap: dict) -> None:
        raise RuntimeError("boom")

    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=_FakeCMM([sid]),
        hook=crashing_hook,
        startup_grace_seconds=0.0,
    )
    # Should NOT raise
    await wd._tick()
    # Dedup timestamp should still have been set so the next tick skips it
    assert wd.last_scan(sid) is not None


@pytest.mark.asyncio
async def test_idle_status_does_not_trigger(store: SessionActivityStore):
    """When status==idle, even old activity should not trigger."""
    sid = "code-eee"
    await store.bump(sid, event_type="final")
    sa = await store.get(sid)
    sa.last_event_ts = time.time() - 1000
    sa.status = "idle"

    triggered = []

    async def hook(s: str, snap: dict) -> None:
        triggered.append(s)

    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=_FakeCMM([sid]),
        hook=hook,
        startup_grace_seconds=0.0,
    )
    await wd._tick()
    assert triggered == []


@pytest.mark.asyncio
async def test_clears_error_pending_after_scan(store: SessionActivityStore):
    sid = "code-fff"
    await store.bump(sid, event_type="error")
    await store.mark_error_pending(sid)
    sa = await store.get(sid)
    sa.error_pending_supervisor = True

    async def hook(s: str, snap: dict) -> None:
        pass

    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=_FakeCMM([sid]),
        hook=hook,
        startup_grace_seconds=0.0,
    )
    await wd._tick()
    sa_after = await store.get(sid)
    assert sa_after.error_pending_supervisor is False


@pytest.mark.asyncio
async def test_start_stop_idempotent(store: SessionActivityStore):
    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=_FakeCMM([]),
        scan_interval_seconds=0.1,
        startup_grace_seconds=0.05,
    )
    wd.start()
    assert wd.is_running()
    wd.start()  # idempotent — second call should warn but not crash
    await asyncio.sleep(0.15)  # let one tick fire
    await wd.stop()
    assert not wd.is_running()
    await wd.stop()  # safe to call again
