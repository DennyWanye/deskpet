"""P5-S1/S2: supervisor watchdog loop.

Runs as an independent ``asyncio.Task`` started after backend startup.
Every ``scan_interval_seconds`` it sweeps all enabled Code-mode sessions
and checks two trigger conditions:

  (a) ``error_pending_supervisor`` — chat_v2_error was recently emitted
      and the supervisor hasn't looked at it yet
  (b) ``last_activity_age_seconds > stuck_threshold_seconds`` (default
      900s = 15 min) AND status is ``running`` or ``permission``

For triggered sids it builds a snapshot, calls the supervisor LLM (S2),
and dispatches the resulting SupervisorAction (S2/S4: broadcast +
optional nudge). Same sid won't be re-scanned within ``dedup_seconds``
(default 720s = 12 min).

The loop catches all exceptions per-tick so a transient failure (LLM
timeout, JSON parse error, broken provider) never kills the watchdog.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("deskpet.agent.watchdog")


# Default knobs — overridable via config.toml ``[supervisor]`` section.
DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_STUCK_THRESHOLD_SECONDS = 900  # 15 min
DEFAULT_DEDUP_SECONDS = 720  # 12 min
DEFAULT_STARTUP_GRACE_SECONDS = 30


# Hook signature: ``async def hook(sid: str, snapshot: dict) -> None``.
# S2 wires this to the supervisor LLM dispatcher; S1 ships with a no-op
# logger so the loop is observable end-to-end before the LLM lands.
SupervisorHook = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _default_hook(sid: str, snapshot: dict[str, Any]) -> None:
    """No-op default — logs the snapshot summary so S1-only deploys still
    produce visible signal in the log.
    """
    age = snapshot.get("last_activity_age_seconds", 0)
    status = snapshot.get("status", "?")
    logger.info(
        "watchdog_trigger sid=%s status=%s age=%.0fs (no supervisor hook wired)",
        sid,
        status,
        age,
    )


class WatchdogLoop:
    """Periodic supervisor scan. Single-instance per backend process.

    Lifecycle:
        wd = WatchdogLoop(...)
        wd.start()                  # in main.py startup, after grace
        ...
        await wd.stop()             # in main.py shutdown

    The loop is cancellable; ``stop()`` cancels the task and awaits its
    teardown.
    """

    def __init__(
        self,
        *,
        session_activity: Any,                    # SessionActivityStore
        code_mode_manager: Any,                   # CodeModeManager
        hook: Optional[SupervisorHook] = None,
        scan_interval_seconds: float = DEFAULT_SCAN_INTERVAL_SECONDS,
        stuck_threshold_seconds: float = DEFAULT_STUCK_THRESHOLD_SECONDS,
        dedup_seconds: float = DEFAULT_DEDUP_SECONDS,
        startup_grace_seconds: float = DEFAULT_STARTUP_GRACE_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._activity = session_activity
        self._cmm = code_mode_manager
        self._hook = hook or _default_hook
        self._scan_interval = float(scan_interval_seconds)
        self._stuck_threshold = float(stuck_threshold_seconds)
        self._dedup = float(dedup_seconds)
        self._grace = float(startup_grace_seconds)
        self._clock = clock
        # Last supervisor scan timestamp per sid for de-duplication.
        self._last_scan_ts: dict[str, float] = {}
        # The task itself; None when not running.
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._enabled = True

    # ─── lifecycle ─────────────────────────────────────────────────────

    def set_hook(self, hook: SupervisorHook) -> None:
        """Replace the supervisor hook (used by main.py to wire S2's
        dispatcher after watchdog construction).
        """
        self._hook = hook

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Schedule the loop on the running event loop. Idempotent —
        calling twice produces a warning and a no-op.
        """
        if self.is_running():
            logger.warning("watchdog already running; start() ignored")
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name="supervisor-watchdog")
        logger.info(
            "watchdog started (scan_interval=%ds, stuck_threshold=%ds, dedup=%ds)",
            int(self._scan_interval),
            int(self._stuck_threshold),
            int(self._dedup),
        )

    async def stop(self) -> None:
        """Cancel the loop and wait for teardown. Safe to call when not running."""
        if self._task is None:
            return
        t = self._task
        self._stopped.set()
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None
        logger.info("watchdog stopped")

    def disable(self) -> None:
        """Soft-disable: future ticks no-op. Use ``stop()`` to fully tear down.
        """
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    # ─── main loop ─────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Outer loop: grace → tick → sleep → repeat."""
        try:
            await asyncio.sleep(self._grace)
        except asyncio.CancelledError:
            return

        while not self._stopped.is_set():
            if self._enabled:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("supervisor_loop_error: %s", exc)
            try:
                await asyncio.sleep(self._scan_interval)
            except asyncio.CancelledError:
                return

    async def _tick(self) -> None:
        """One scan pass over all active Code-mode sessions."""
        # Pull active code-mode sids. ``all_sessions()`` returns a dict-like
        # mapping; we only care about enabled ones with a real code_session_id.
        try:
            sessions = self._cmm.all_sessions() if self._cmm else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("watchdog_cmm_query_failed error=%s", exc)
            return

        now = self._clock()
        candidates: list[str] = []
        for base_sid, st in (sessions or {}).items():
            try:
                if not getattr(st, "enabled", False):
                    continue
            except Exception:
                continue
            # The watchdog watches the *base* session id (the one chat
            # messages flow under), not the code_session_id hash. Activity
            # is recorded under the base sid in main.py's WS forwarder.
            candidates.append(base_sid)

        for sid in candidates:
            # Dedup check
            last_scan = self._last_scan_ts.get(sid, 0.0)
            if last_scan and now - last_scan < self._dedup:
                continue

            sa = await self._activity.get(sid)
            if sa is None:
                # No activity recorded yet — session just entered Code
                # mode but hasn't emitted any events. Skip.
                continue

            # Trigger evaluation
            if not self._should_trigger(sa, now):
                continue

            # Build snapshot via the activity record's helper
            snapshot = sa.to_snapshot_dict()
            # Mark scan timestamp BEFORE invoking the hook so a slow
            # hook doesn't delay future ticks for unrelated sids.
            self._last_scan_ts[sid] = now

            # Clear error_pending so a single error doesn't re-fire; the
            # supervisor is now responsible for this sid until next scan.
            try:
                await self._activity.clear_error_pending(sid)
            except Exception:
                pass

            # Run the hook with its own try/except to isolate from the loop.
            try:
                await self._hook(sid, snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("supervisor_hook_failed sid=%s error=%s", sid, exc)

    def _should_trigger(self, sa: Any, now: float) -> bool:
        """Apply the two trigger rules from the spec."""
        # (a) chat_v2_error pending
        if getattr(sa, "error_pending_supervisor", False):
            return True
        # (b) inactivity threshold + status filter
        last_event_ts = getattr(sa, "last_event_ts", 0.0)
        status = getattr(sa, "status", "")
        age = max(0.0, now - last_event_ts)
        if age > self._stuck_threshold and status in ("running", "permission"):
            return True
        return False

    # ─── introspection (used by tests + observability) ─────────────────

    def last_scan(self, sid: str) -> Optional[float]:
        return self._last_scan_ts.get(sid)

    def reset_dedup(self, sid: Optional[str] = None) -> None:
        if sid is None:
            self._last_scan_ts.clear()
        else:
            self._last_scan_ts.pop(sid, None)
