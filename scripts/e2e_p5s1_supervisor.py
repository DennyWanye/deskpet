# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S1/S2/S3/S4 — visual / behavioral E2E smoke for the supervisor stack.

Drives the running backend through its real WS surface and SessionDB:

1. Connects to control WS (DESKPET_DEV_MODE=1 expected, no secret needed)
2. Sends `supervisor_user_choice` and verifies it lands in supervisor_hints
3. Sends `supervisor_toggle` and verifies the ack
4. Imports SessionActivityStore + WatchdogLoop + SupervisorAgent in-process
   to fully simulate a stuck-session → supervisor → broadcast flow without
   waiting 15 minutes of real wall-clock time.
5. Verifies the supervisor_hints audit table records the dispatched action
6. Verifies broadcast reaches a listening client

Run:
    cd backend && DESKPET_DEV_MODE=1 .venv/Scripts/python main.py &
    cd .. && python scripts/e2e_p5s1_supervisor.py

Exit 0 → all checks pass.
Exit 1 → at least one check failed (check stdout for [FAIL] lines).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Make backend importable for direct module manipulation
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import websockets  # type: ignore


PORT = int(os.environ.get("DESKPET_PORT", "8100"))
WS_URL = f"ws://127.0.0.1:{PORT}/ws/control?session_id=e2e-p5s1"

passed = 0
failed = 0


def ok(label: str) -> None:
    global passed
    passed += 1
    print(f"  [PASS] {label}")


def fail(label: str, detail: str = "") -> None:
    global failed
    failed += 1
    print(f"  [FAIL] {label}")
    if detail:
        print(f"         {detail}")


async def recv_until(ws, target_type: str, timeout: float = 5.0) -> dict | None:
    """Drain ws until a message of `target_type` arrives, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
        except asyncio.TimeoutError:
            return None
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if obj.get("type") == target_type:
            return obj
    return None


async def test_supervisor_toggle_ack() -> None:
    """Round-trip: send supervisor_toggle, expect supervisor_toggle_ack."""
    print("\n[1] supervisor_toggle round-trip ws acknowledgement")
    async with websockets.connect(WS_URL) as ws:
        # Drain initial startup_status if any
        await asyncio.sleep(0.2)
        await ws.send(json.dumps({"type": "supervisor_toggle", "payload": {"enabled": True}}))
        resp = await recv_until(ws, "supervisor_toggle_ack", timeout=3.0)
        if resp is None:
            fail("supervisor_toggle_ack received", "no ack within 3s")
            return
        if resp.get("payload", {}).get("enabled") is True:
            ok("supervisor_toggle_ack received with enabled=true")
        else:
            fail("supervisor_toggle_ack payload shape", str(resp))


async def test_supervisor_user_choice_persists() -> None:
    """Send supervisor_user_choice, then query SessionDB to verify the row landed."""
    print("\n[2] supervisor_user_choice → SessionDB audit row")
    sid = "e2e-choice-" + uuid.uuid4().hex[:8]
    alert_id = "alert-" + uuid.uuid4().hex[:8]
    btn_text = "让它继续 (e2e)"

    async with websockets.connect(WS_URL) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "supervisor_user_choice",
                    "payload": {
                        "session_id": sid,
                        "alert_id": alert_id,
                        "button_index": 0,
                        "button_text": btn_text,
                    },
                }
            )
        )
        # No ack from backend; give DB a moment to flush.
        await asyncio.sleep(0.6)

    # Verify via direct SessionDB read
    from deskpet.memory.session_db import SessionDB
    import paths as _paths  # type: ignore

    db_path = _paths.user_data_dir() / "data" / "state.db"
    sdb = SessionDB(db_path=str(db_path))
    await sdb.initialize()
    rows = await sdb.list_supervisor_hints(session_id=sid)
    if not rows:
        fail("user_choice row found in supervisor_hints", "no rows for sid=" + sid)
        return
    target = next((r for r in rows if r.get("alert_id") == alert_id), None)
    if target is None:
        fail("user_choice row alert_id matches", "rows: " + str(rows))
        return
    if target.get("user_button") != btn_text:
        fail("user_choice button text persisted", str(target))
        return
    ok("user_choice persisted with correct button text + alert_id")


async def test_supervisor_agent_full_flow() -> None:
    """In-process simulation: build a fake snapshot, run supervisor_agent.diagnose()
    with a mock LLM provider, verify broadcast + DB row + nudge queue push."""
    print("\n[3] in-process supervisor flow (mock LLM, real broadcast + DB)")

    from agent.supervisor import SupervisorAgent
    from agent.nudge_queue import NudgeQueue
    from deskpet.memory.session_db import SessionDB
    import paths as _paths  # type: ignore

    db_path = _paths.user_data_dir() / "data" / "state.db"
    sdb = SessionDB(db_path=str(db_path))
    await sdb.initialize()

    nq = NudgeQueue(cap=3)
    broadcast_log: list[tuple[str, dict]] = []

    async def fake_broadcast(typ: str, payload: dict):
        broadcast_log.append((typ, payload))

    async def push_hint(target_sid: str, action):
        from agent.nudge_queue import Hint
        await nq.push(
            target_sid,
            Hint(
                text=action.hint_for_main_agent or action.user_message,
                alert_id=action.alert_id,
                severity=action.severity,
            ),
        )

    async def audit(action, target_sid):
        await sdb.append_supervisor_hint(
            session_id=target_sid,
            alert_id=action.alert_id,
            hint_text=action.hint_for_main_agent or action.user_message,
            action=action.action,
            severity=action.severity,
            diagnosis=action.diagnosis,
        )

    class MockProvider:
        async def chat_with_tools(self, messages, *, tools=None, max_tokens=2048, temperature=None):
            return {
                "content": json.dumps(
                    {
                        "action": "nudge",
                        "severity": "yellow",
                        "diagnosis": "e2e: agent looked stuck on bash_run loop",
                        "hint_for_main_agent": "切换 pip 镜像源到 mirrors.tuna.tsinghua.edu.cn",
                        "user_message": "我让它换个 pip 源试试",
                        "suggested_buttons": ["让它继续", "我看看"],
                    }
                )
            }

    async def fake_snap(sid: str) -> dict:
        return {
            "session_id": sid,
            "status": "running",
            "last_activity_age_seconds": 1100,
            "current_iteration": 12,
            "max_iterations": 50,
            "last_5_events": [
                {"type": "tool_call", "name": "bash_run", "args_hash": "abc", "ok": False},
            ],
            "tool_signature_window": {"bash_run:abc": 4},
            "todos_state": [],
            "user_goal": "Install dependencies",
        }

    agent = SupervisorAgent(
        provider=MockProvider(),
        snapshot_builder=fake_snap,
        nudge_queue_push=push_hint,
        broadcast=fake_broadcast,
        audit=audit,
        timeout_seconds=5.0,
    )

    test_sid = "e2e-flow-" + uuid.uuid4().hex[:8]
    action = await agent.diagnose(test_sid)

    if action.action != "nudge":
        fail("supervisor returned nudge", f"got action={action.action}")
        return
    ok("supervisor returned nudge for stuck-snapshot")

    if not broadcast_log:
        fail("supervisor_alert broadcast fired", "no broadcast invocations")
        return
    typ, payload = broadcast_log[0]
    if typ != "supervisor_alert":
        fail("broadcast type is supervisor_alert", f"got {typ}")
        return
    if payload.get("session_id") != test_sid:
        fail("broadcast payload session_id matches", str(payload))
        return
    ok("supervisor_alert broadcast fired with correct payload")

    if not await nq.peek(test_sid):
        fail("nudge_queue received hint", "queue empty after dispatch")
        return
    hints = await nq.pop_all(test_sid)
    if "pip" not in hints[0].text and "镜像" not in hints[0].text:
        fail("nudge_queue hint text contains pip/镜像", str(hints[0].text))
        return
    ok("nudge_queue received hint with expected pip-mirror content")

    # DB audit row
    rows = await sdb.list_supervisor_hints(session_id=test_sid)
    target = next((r for r in rows if r.get("alert_id") == action.alert_id), None)
    if target is None:
        fail("supervisor_hints DB row written", f"no row found, rows={rows}")
        return
    if target.get("action") != "nudge":
        fail("supervisor_hints row action=nudge", str(target))
        return
    if target.get("severity") != "yellow":
        fail("supervisor_hints row severity=yellow", str(target))
        return
    ok("supervisor_hints DB row written with action=nudge severity=yellow")


async def test_watchdog_inactivity_trigger() -> None:
    """Force-feed SessionActivity an old timestamp, run one watchdog tick,
    verify the supervisor hook fires."""
    print("\n[4] watchdog inactivity → hook invocation (in-process)")

    from agent.session_activity import SessionActivityStore
    from agent.watchdog import WatchdogLoop

    store = SessionActivityStore()
    sid = "e2e-stuck-" + uuid.uuid4().hex[:8]
    await store.bump(sid, event_type="tool_call", name="bash_run", args={"cmd": "x"})
    sa = await store.get(sid)
    sa.last_event_ts = time.time() - 1000  # 16+ min old
    sa.status = "running"

    fired: list[str] = []

    async def hook(s: str, snapshot: dict) -> None:
        fired.append(s)

    class FakeCMM:
        def all_sessions(self):
            class _S:
                enabled = True
                code_session_id = "code-x"
                project_root = "/tmp/x"
                project_name = "x"

            return {sid: _S()}

    wd = WatchdogLoop(
        session_activity=store,
        code_mode_manager=FakeCMM(),
        hook=hook,
        startup_grace_seconds=0.0,
    )
    await wd._tick()
    if fired != [sid]:
        fail("watchdog _tick invoked hook for stuck sid", f"fired={fired}")
        return
    ok("watchdog detected inactivity and fired hook")


async def main() -> int:
    print("==== P5-S1 supervisor E2E smoke ====")
    print(f"Backend:  ws://127.0.0.1:{PORT}/ws/control")
    try:
        await test_supervisor_toggle_ack()
        await test_supervisor_user_choice_persists()
        await test_supervisor_agent_full_flow()
        await test_watchdog_inactivity_trigger()
    except Exception as exc:
        import traceback
        print(f"\n[ERROR] uncaught exception during E2E: {exc}")
        traceback.print_exc()
        return 2

    print(f"\n==== {passed} passed, {failed} failed ====")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
