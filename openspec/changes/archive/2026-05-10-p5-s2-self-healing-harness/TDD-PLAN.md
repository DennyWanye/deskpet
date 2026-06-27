# P5-S2 TDD Plan

> Companion to [tasks.md](tasks.md). This document specifies the **test-first contract** for each phase: what test must exist BEFORE the implementation, what it asserts, and what mock surface it uses.
>
> Ordering is non-negotiable: red → green → refactor. Reviewers SHALL reject any PR that introduces implementation code without a failing test in the same diff.

## 测试金字塔总览

```
        Manual E2E (6 evidence docs)             ← phase 0/1/2/3/4/5 各 1 次
              ↑
         Integration (8 tests)                   ← cross-component flow
              ↑
            Unit (40+)                           ← per class / pure fn
```

## 命名约定

- `tests/test_p5s2_<topic>.py` — backend unit + integration
- `tests/e2e_p5s2_<scenario>.py` — backend live LLM E2E (skipped without `the relay_e2e` marker)
- `src/.../<module>.test.ts` / `.test.tsx` — frontend vitest
- evidence: `openspec/changes/p5-s2-self-healing-harness/evidence/<task-id>-<slug>.md`

---

## Phase 0 — Sensor Hint

### Backend tests (must exist before any code change)

#### `tests/test_p5s2_tool_error_schema.py`

```python
import json
import pytest
from deskpet.tools.os_tools import (
    run_shell, write_file, edit_file, read_file, list_directory,
    desktop_create_file, web_fetch, glob, grep,
)

# Contract: every error response MUST have hint (str, non-empty) and SHOULD have examples (list).

@pytest.mark.parametrize("tool, args, expected_keyword", [
    (run_shell, {}, "command"),
    (write_file, {}, "path"),
    (write_file, {"path": "x"}, "content"),
    (edit_file, {}, "path"),
    (read_file, {}, "path"),
    (list_directory, {}, "path"),
    (desktop_create_file, {}, "name"),
])
def test_missing_required_param_returns_hint(tool, args, expected_keyword):
    out = json.loads(tool(args, ""))
    assert out.get("ok") is False, f"{tool.__name__} should report ok=false on missing param"
    assert "hint" in out, f"{tool.__name__} error must include hint field"
    assert isinstance(out["hint"], str) and out["hint"], "hint must be non-empty str"
    assert expected_keyword in out["hint"], f"hint should mention the missing field name"


def test_write_file_overwrite_blocked_returns_alternatives(tmp_path):
    fp = tmp_path / "exists.txt"
    fp.write_text("old")
    out = json.loads(write_file({"path": str(fp), "content": "new"}, ""))
    assert out["ok"] is False
    assert "overwrite" in out["error"]
    assert "edit_file" in (out.get("hint") or "")


def test_run_shell_timeout_returns_partial_output_and_hint():
    out = json.loads(run_shell({"command": "sleep 10", "timeout": 1}, ""))
    assert out["error"] == "timeout"
    assert "hint" in out
    assert "stdout_partial" in out
    assert out.get("elapsed_seconds") == 1
```

#### `tests/test_p5s2_tool_dispatch_hint_passthrough.py`

```python
@pytest.mark.asyncio
async def test_hint_field_survives_dispatch_serialization():
    """Some downstream code paths string-format tool results; hint must survive round-trip."""
    from deskpet.tools.registry_v2 import ToolRegistryV2
    registry = ToolRegistryV2()

    # Register a fake tool that returns a hinted error
    def _fake(args, task_id):
        return json.dumps({"ok": False, "error": "x", "hint": "do y", "examples": [{"a": 1}]})

    registry.register(name="fake", toolset="test", schema={...}, handler=_fake,
                       permission_category="read_file")

    result = await registry.execute_tool("fake", {}, "task1", "session1")
    parsed = json.loads(result)
    assert parsed["hint"] == "do y"
    assert parsed["examples"] == [{"a": 1}]
```

### Live E2E (manual, evidence required)

`evidence/0.11-hint-recovery.md` MUST contain:

1. Screenshot of code-mode chat after sending "创建 test.txt 内容 hello" (deliberately ambiguous)
2. Log excerpt showing first tool_result with `hint`
3. Log excerpt showing second tool_call with corrected args (proving LLM consumed the hint)
4. Final answer screenshot

---

## Phase 1 — Diagnostic logging

#### `tests/test_p5s2_sse_diagnostic.py`

```python
@pytest.mark.asyncio
async def test_tool_call_args_logged_per_call(caplog):
    """When chat_stream_with_tools yields a final event with tool_calls, each call's
    actual args + parse status MUST be logged at INFO level."""
    p = OpenAICompatibleProvider(...)
    p._test_transport = httpx.MockTransport(_sse_with_tool_call(
        {"name": "write_file", "arguments": '{"path"'}  # malformed
    ))

    with caplog.at_level("INFO", logger="providers.openai_compatible"):
        async for ev in p.chat_stream_with_tools(...):
            pass

    diagnostic_records = [r for r in caplog.records if "tool_call_args_dump" in r.message]
    assert len(diagnostic_records) == 1
    rec = diagnostic_records[0]
    assert "args_len=" in rec.message
    assert "parse_ok=False" in rec.message
```

### Live E2E

`evidence/1.3-args-dump.md` — reproduce 50-iteration vpn-tunnel case + paste log + classify root cause (the relay truncation / model bug / parser bug).

---

## Phase 2 — Error Taxonomy

#### `tests/test_p5s2_error_taxonomy.py`

```python
from agent.errors import (
    classify, TransientToolError, PermanentToolError, HallucinationError,
)

@pytest.mark.parametrize("raw, expected_class", [
    ({"error": "missing required parameter: path"}, PermanentToolError),
    ({"error": "missing_required_parameters"}, PermanentToolError),
    ({"error": "would_overwrite"}, PermanentToolError),
    ({"error": "schema_invalid"}, PermanentToolError),
    ({"error": "circuit_open"}, PermanentToolError),
    ({"error": "timeout"}, TransientToolError),
    ({"error": "Server disconnected without sending a response"}, TransientToolError),
    ({"error": "ReadTimeout"}, TransientToolError),
    ({"error": "503 Service Unavailable"}, TransientToolError),
    ({"error": "tool_not_found"}, HallucinationError),
    ({"error": "<some unseen string>"}, TransientToolError),  # conservative default
    ("plain string error message", TransientToolError),       # str input also accepted
    (Exception("unknown"), TransientToolError),
])
def test_classify(raw, expected_class):
    assert classify(raw) is expected_class
```

#### `tests/test_p5s2_agent_loop_permanent_break.py`

```python
@pytest.mark.asyncio
async def test_permanent_error_breaks_after_first_dispatch():
    """LLM keeps emitting invalid tool_call; first PermanentError result must
    end the loop immediately (NOT after max_iterations=50)."""
    invalid_call_resp = ChatResponse(
        content="", stop_reason="tool_use",
        tool_calls=[ToolCall(id="c1", name="write_file", arguments={})],
        ...
    )
    llm = _ScriptedLLM([invalid_call_resp] * 10)

    class _PermErrorTools:
        def schemas(self, **k): return []
        async def execute_tool(self, name, args, *_):
            return json.dumps({"ok": False, "error": "missing_required_parameters",
                                "hint": "...", "missing": ["path", "content"]})

    loop = AgentLoop(llm_registry=llm, tool_registry=_PermErrorTools(), max_iterations=50)
    events = [ev async for ev in loop.run([{"role": "user", "content": "x"}], session_id="s")]

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].reason == "permanent_tool_error"
    # Critical: must have stopped EARLY, not after 50 iterations
    assert len(llm.calls) <= 3, f"expected ≤3 LLM calls before break; got {len(llm.calls)}"
```

### Live E2E

`evidence/2.11-permanent-break.md` — reproduce vpn-tunnel case + show ≤3 iter break (was 50)

---

## Phase 3 — Circuit Breaker

#### `tests/test_p5s2_circuit_breaker.py`

```python
@pytest.fixture
def breaker():
    from agent.circuit_breaker import ToolCircuitBreaker
    return ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)

@pytest.mark.asyncio
async def test_three_failures_open(breaker):
    sid, tool = "A", "write_file"
    for _ in range(3):
        await breaker.record_call(sid, tool, ok=False)
    assert not await breaker.can_call(sid, tool)
    assert (await breaker.state(sid, tool)) == "OPEN"

@pytest.mark.asyncio
async def test_success_resets(breaker):
    sid, tool = "A", "write_file"
    await breaker.record_call(sid, tool, ok=False)
    await breaker.record_call(sid, tool, ok=False)
    await breaker.record_call(sid, tool, ok=True)
    assert (await breaker.state(sid, tool)) == "CLOSED"

@pytest.mark.asyncio
async def test_open_to_half_open_after_cooldown(monkeypatch):
    breaker = ToolCircuitBreaker(threshold=3, cooldown_seconds=0.1)
    sid, tool = "A", "write_file"
    for _ in range(3):
        await breaker.record_call(sid, tool, ok=False)
    assert not await breaker.can_call(sid, tool)
    await asyncio.sleep(0.15)
    # Probe allowed exactly once
    assert await breaker.can_call(sid, tool)
    assert not await breaker.can_call(sid, tool)  # second probe blocked

@pytest.mark.asyncio
async def test_per_tool_isolation(breaker):
    for _ in range(3):
        await breaker.record_call("A", "write_file", ok=False)
    assert not await breaker.can_call("A", "write_file")
    assert await breaker.can_call("A", "read_file")  # different tool, unaffected

@pytest.mark.asyncio
async def test_per_session_isolation(breaker):
    for _ in range(3):
        await breaker.record_call("A", "write_file", ok=False)
    assert not await breaker.can_call("A", "write_file")
    assert await breaker.can_call("B", "write_file")  # different session
```

#### `tests/test_p5s2_dispatch_circuit_integration.py`

```python
@pytest.mark.asyncio
async def test_dispatch_blocked_when_open(monkeypatch):
    registry = ToolRegistryV2()
    registry._breaker = ToolCircuitBreaker(threshold=1, cooldown_seconds=60.0)

    handler_invoked = []
    def _h(args, task_id):
        handler_invoked.append(args)
        return json.dumps({"ok": False, "error": "fail"})
    registry.register(name="bad", toolset="test", schema={...}, handler=_h,
                       permission_category="read_file")

    # First call → fails → opens the breaker (threshold=1)
    await registry.execute_tool("bad", {}, "task1", "sid1")
    assert len(handler_invoked) == 1

    # Second call → blocked
    raw = await registry.execute_tool("bad", {}, "task2", "sid1")
    assert len(handler_invoked) == 1, "handler must NOT be re-invoked when breaker is OPEN"
    parsed = json.loads(raw)
    assert parsed["error"] == "circuit_open"
    assert "hint" in parsed
    assert isinstance(parsed.get("available_alternatives"), list)
```

---

## Phase 4 — AutoResume

#### `tests/test_p5s2_auto_resume.py`

```python
class _StubSupervisor:
    def __init__(self, action="nudge", hint="do x"):
        self.calls = []
        self._next_action = action
        self._hint = hint
    async def diagnose(self, sid, snapshot):
        self.calls.append((sid, snapshot))
        return SupervisorAction(action=self._next_action, severity="yellow",
                                 hint_for_main_agent=self._hint, alert_id="a1")

@pytest.mark.asyncio
async def test_max_iterations_triggers_supervisor_then_spawn():
    sup = _StubSupervisor()
    spawned = []
    async def _dispatch(sid, hint, original_msgs):
        spawned.append((sid, hint))

    orch = AutoResumeOrchestrator(
        supervisor=sup, chat_dispatcher=_dispatch,
        max_attempts=2, enabled=True,
    )
    await orch.handle_failure(
        sid="s1",
        reason="max_iterations",
        snapshot={"recent_events": [], "...": ...},
        original_msgs=[{"role": "user", "content": "build x"}],
    )
    assert len(sup.calls) == 1
    assert len(spawned) == 1
    assert spawned[0][1] == "do x"

@pytest.mark.asyncio
async def test_max_attempts_caps_resume():
    sup = _StubSupervisor(action="nudge", hint="retry")
    spawn_count = 0
    async def _dispatch(*a, **k):
        nonlocal spawn_count
        spawn_count += 1
    orch = AutoResumeOrchestrator(
        supervisor=sup, chat_dispatcher=_dispatch,
        max_attempts=2, enabled=True,
    )
    # 3 failures in a row
    for _ in range(3):
        await orch.handle_failure(sid="s1", reason="max_iterations", snapshot={}, original_msgs=[])
    assert spawn_count == 2  # capped at 2

@pytest.mark.asyncio
async def test_ask_user_action_does_not_spawn():
    sup = _StubSupervisor(action="ask_user", hint="")
    spawned = []
    async def _dispatch(*a, **k): spawned.append(1)
    orch = AutoResumeOrchestrator(
        supervisor=sup, chat_dispatcher=_dispatch,
        max_attempts=2, enabled=True,
    )
    await orch.handle_failure(sid="s1", reason="max_iterations", snapshot={}, original_msgs=[])
    assert spawned == []  # ask_user → fall through to popup, no auto-spawn

@pytest.mark.asyncio
async def test_disabled_orchestrator_falls_through():
    sup = _StubSupervisor()
    spawned = []
    async def _dispatch(*a, **k): spawned.append(1)
    orch = AutoResumeOrchestrator(
        supervisor=sup, chat_dispatcher=_dispatch,
        max_attempts=2, enabled=False,
    )
    await orch.handle_failure(sid="s1", reason="max_iterations", snapshot={}, original_msgs=[])
    assert sup.calls == []  # supervisor not even called
    assert spawned == []
```

### Live E2E

`evidence/4.16-auto-resume-e2e.md` MUST show:
1. Screenshot of "agent 自愈中..." banner appearing
2. Banner disappearing on success
3. Log showing supervisor.diagnose called + chat_dispatcher invoked + final response
4. SessionDB query showing `supervisor_hints.action='auto_resumed'` row

---

## Phase 5 — Frontend

#### `src/code-panel/AutoResumeBanner.test.tsx`

```typescript
import { render, screen, act } from "@testing-library/react";
import { AutoResumeBanner } from "./AutoResumeBanner";
import { useSessionsStore } from "../stores/sessionsStore";

it("renders banner when auto_resume_started for current session", () => {
  useSessionsStore.getState().upsert("s1", { auto_resume_attempts: 1 });
  render(<AutoResumeBanner sessionId="s1" />);
  expect(screen.getByText(/自愈中/)).toBeInTheDocument();
  expect(screen.getByText(/1\/2/)).toBeInTheDocument();
});

it("clears within 500ms of succeeded event", async () => {
  // ...
});
```

---

## Phase 6 — Watchdog integration

#### `tests/test_p5s2_watchdog_signature_repeat.py`

```python
@pytest.mark.asyncio
async def test_watchdog_triggers_on_signature_repeat(store):
    sid = "s1"
    # Manually shape tool_signature_window to show 3x same call
    sa = await store.bump(sid, event_type="tool_call", name="write_file", args_hash="abc")
    await store.bump(sid, event_type="tool_call", name="write_file", args_hash="abc")
    await store.bump(sid, event_type="tool_call", name="write_file", args_hash="abc")

    triggered = []
    async def hook(sid, snap): triggered.append(sid)

    wd = WatchdogLoop(
        session_activity=store, code_mode_manager=_FakeCMM([sid]), hook=hook,
        scan_interval_seconds=0.05, stuck_threshold_seconds=900,
        dedup_seconds=720, startup_grace_seconds=0.0,
    )
    await wd._tick()
    assert triggered == [sid]
```

---

## Definition of Done (per phase)

```
DoD = {
  all_unit_tests_green: true,
  no_pytest_regression: true,                # 953 → 953+ never lower
  no_vitest_regression: true,                # 34 → 34+ never lower
  evidence_doc_committed: true,              # evidence/<task>.md
  manual_e2e_run_at_least_once: true,        # logs + screenshots
  changelog_entry_in_specs: true,            # cumulative
}
```

不允许跳任何一项。

## Falsifiable Contract（Martin Fowler 2026 推荐）

每个 phase 提交时 PR 描述里必须包含**两段**：

1. **预期挡的 case**："Phase X 应该挡 [具体场景]，证据见 evidence/X.Y.md"
2. **可能引入的 regression**："Phase X 改动了 [模块]，可能影响 [其他场景]，已通过 [test_name] 守护"

Reviewer 必须 quote 这两段。如果回答不出，patch 拒收。
