# Evidence: 6 — config + watchdog rule (d) integration

**When**: 2026-05-10 UTC+8
**Who**: subagent (Phase 6 implementer)
**What we tested**: Config keys for the self-healing harness flow from
`config.toml [supervisor]` into the runtime components
(AutoResumeOrchestrator / ToolCircuitBreaker / AgentLoop / WatchdogLoop),
and watchdog gains a 4th trigger rule (d) that fires on consecutive
same-signature tool calls.

## Phase 6 task status

- [x] 6.1 `config.toml` `[supervisor]` extended with 5 new P5-S2 keys
      (auto_resume_enabled / max_auto_resume_attempts /
      circuit_breaker_threshold / circuit_breaker_cooldown_seconds /
      tool_signature_repeat_threshold) — defaults + Chinese comments.
- [x] 6.2 `main.py` wires those keys into the runtime:
      - `auto_resume_enabled` + `max_auto_resume_attempts` already wired in
        Phase 4 (`AutoResumeOrchestrator(enabled=..., max_attempts=...)`).
      - `circuit_breaker_threshold` + `circuit_breaker_cooldown_seconds`
        instantiate a `ToolCircuitBreaker` and call
        `deskpet_tool_registry_v2.set_circuit_breaker(_breaker)` so the
        registry's existing `can_call` / `record_call` plumbing
        (Phase 3.2) starts gating dispatch.
      - `tool_signature_repeat_threshold` reaches both
        `WatchdogLoop(tool_signature_repeat_threshold=...)` (rule d) and
        `AgentLoop(signature_repeat_threshold=...)` (in-loop nudge).
- [x] 6.3 `watchdog.py::_should_trigger` gains rule (d): scans
      `session_activity.tool_signature_window` (a `dict[str, int]` of
      consecutive same-sig counts) and triggers when any value ≥
      `tool_signature_repeat_threshold`.
- [x] 6.4 New tests in `tests/test_p5s1_watchdog.py`:
      - `test_watchdog_triggers_on_tool_signature_repeat` — 3 consecutive
        identical `write_file({"path":"/sys/foo"})` → rule (d) fires even
        with fresh `last_event_ts` and high `stuck_threshold`.
      - `test_watchdog_signature_repeat_below_threshold_no_trigger` —
        2 consecutive same-sig calls (below threshold=3) must NOT fire.
- [x] 6.5 This evidence file.
- [ ] 6.6 archive — left to lead agent per task instructions.

## Implementation notes

- `tool_signature_window` is a `dict[str, int]` (consecutive count per
  signature), not a list. `SessionActivityStore.bump` resets the dict
  to `{}` whenever a different signature lands or any non-tool event
  comes through, so any value ≥ threshold genuinely means "the LLM
  emitted this exact (name, args_hash) N times in a row". Rule (d)'s
  check is therefore `max(window.values()) >= threshold`.
- AgentLoop's existing in-loop suppression (Phase 3.3) already short-
  circuits at `prior >= _REPEAT_THRESHOLD - 1` so rule (d) is the
  *safety net* — if the LLM somehow gets past that suppression (e.g.
  the activity_store wasn't injected, or main.py-side bumping ran
  ahead of the loop's read) the watchdog still catches it.
- Made `_REPEAT_THRESHOLD` overridable via a new
  `signature_repeat_threshold` ctor kwarg on `AgentLoop` so the same
  config knob drives both AgentLoop and Watchdog.
- Registered two new `ServiceContext` keys (`auto_resume`,
  `tool_circuit_breaker`); the previous Phase 4
  `service_context.register("auto_resume", ...)` call had been
  silently swallowed by an outer `except Exception` because the key
  wasn't in `_VALID_SERVICES`.

## Verification

### Watchdog tests (focused)

```
$ python -m pytest tests/test_p5s1_watchdog.py -v --tb=short
============================== 14 passed in 0.21s ==============================
```

All 14 watchdog tests green, including the 2 new rule-(d) tests.

### P5-S2 module suite

```
$ python -m pytest \
    tests/test_p5s1_watchdog.py \
    tests/test_p5s2_circuit_breaker.py \
    tests/test_p5s2_agent_loop_signature_repeat.py \
    tests/test_p5s2_auto_resume.py \
    tests/test_p5s2_dispatch_circuit_integration.py -v
============================== 45 passed in 0.53s ==============================
```

### main.py import smoke

```
$ python -c "from main import app; print('main.py imports OK')"
... (startup logs) ...
main.py imports OK
```

No import errors; the new ToolCircuitBreaker import + `set_circuit_breaker`
wire-in is exercised at lifespan start.

### Full backend regression

```
$ python -m pytest tests/ -q --tb=line --ignore=tests/test_deskpet_vector_worker.py
1040 passed, 14 skipped, 4 deselected in 74.45s
```

Pre-Phase-6 baseline (re-measured on this worktree, P5-S2 Batch 4
landed): **1038 passed**. Adding 2 new rule-(d) tests yields
**1040 passed** — no regression, two new tests added, count delta
matches additions exactly.

## Conclusion

- Watchdog rule (d) implemented with both positive and negative-
  threshold tests, isolated from the existing 3 trigger paths.
- All 5 new `[supervisor]` config keys wired end-to-end:
  - 2 reach `AutoResumeOrchestrator` (Phase 4 carryover, confirmed)
  - 2 reach `ToolCircuitBreaker` (newly instantiated at lifespan start)
  - 1 reaches both `AgentLoop` (per-iteration suppression) and
    `WatchdogLoop` (rule d safety net)
- No backend regression; main.py imports cleanly.
- Live E2E (computer-use Tauri smoke) deferred to lead agent's pre-
  archive verification — Phase 6 is plumbing-only and the live behaviour
  was verified end-to-end during Phase 4 (see `4.16-auto-resume-unit.md`)
  and Phase 3 (see `3.16-circuit-recovery.md`).

## Followup / deviations

- README.md self-healing intro (task 6.5) is not in this subagent's
  allowed file targets per the dispatch contract — left for the lead
  agent's archive ceremony to add alongside the spec consolidation.
- `service_context.register("auto_resume", ...)` had been failing
  silently since Phase 4 because of the missing whitelist entry. Fixed
  here as part of Phase 6 wiring; no functional impact since the
  orchestrator was already constructed and the chat handler doesn't
  read it via `service_context.get` (it captures `_orch` via closure).
