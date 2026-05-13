# P6 Bugfix — Args-Aware Per-Tool Consecutive Counter (2026-05-13)

## Outcome: ✅ FIX VERIFIED LIVE

Pre-fix: 5 legitimate `read_file` calls (different paths) triggered
`HALLUCINATION_DETECTED` → unnecessary `auto_resume_engaged`.
Post-fix: same scenario completes with `stop_reason=end_turn`, zero
hallucination events in the log.

## Symptom (pre-fix, from 2026-05-12 live test)

```
15:07:41  ✅ auto_resume_engaged reason=hallucination attempt=1
15:10:37  ✅ auto_resume_engaged reason=max_iterations attempt=2
```

The first auto_resume fired ~50s into a runaway prompt — but the SAME
gate would also fire on legitimate exploration of 5+ different files,
which the user reported as "code mode 还是有问题" after the original P6
ship.

## Root cause

`TerminationGate.record_tool_call(name)` only keyed the per-tool
consecutive counter by tool name:

```python
# OLD — name-only counter
self.state.per_tool_consecutive[name] = consec + 1
```

So `read_file A.py`, `read_file B.py`, ..., `read_file E.py` all
incremented the same counter and tripped `per_tool_max_consecutive=5`.

## Fix (2-prong)

### 1. Bump default `per_tool_max_consecutive` 5 → 8

Immediate headroom for borderline legitimate sequences.

### 2. Args-aware signature

`record_tool_call(name, *, args)` now hashes args and only increments
the counter when **both** name AND args-hash match the previous call:

```python
sig = md5(json.dumps(args, sort_keys=True))[:16]
if last_sig == sig:
    counter[name] += 1   # genuine death loop
else:
    counter[name] = 1    # different work → reset
    last_sig_map[name] = sig
```

A genuine death loop (5x `read_file {"path": "stuck.txt"}`) STILL
trips the cap. Systematic exploration (5x different paths) no longer
does.

## Test coverage

Three new unit tests in `backend/tests/test_p6_termination_gate.py`:

1. `test_per_tool_consecutive_args_aware_different_args_resets` —
   5 reads of 5 different paths must NOT trigger hallucination
2. `test_per_tool_consecutive_args_aware_same_args_still_blocks` —
   3 reads of the same path WITH `per_tool_max_consecutive=3` DOES
   trigger hallucination
3. `test_per_tool_consecutive_none_args_treated_consistently` —
   legacy callers passing `args=None` still get a stable signature

Plus an existing test was tightened: `test_run_per_tool_consecutive_break`
now uses constant `STUCK_ARGS` instead of varying `{"i": n}` (the
varying args version would correctly NOT trigger the cap anymore).

Result: **1229 backend tests pass.**

## Live verification (2026-05-13 16:14)

### Setup

- Backend rebuilt from commit `e60c667` (the bugfix commit)
- Tauri dev mode started fresh, secret `d4c19b75...`
- Test script: `backend/scripts/p6_legit_exploration_test.py`
- Test prompt: legitimate exploration of 5 different files in
  `backend/agent/`

### Production log (verbatim from tauri-dev.log)

```
08:14:43  control channel connected sid=p6-legit-test
08:14:51  list_directory(G:/projects/deskpet/backend/agent, max_entries=100)
08:14:51  p4s25_stream_summary tool_calls=1 stop_reason='tool_use'
08:15:15  read_file(agent_loop.py, 0-50)
08:15:15  read_file(termination.py, 0-50)
08:15:15  read_file(context_manager.py, 0-50)
08:15:15  read_file(auto_resume.py, 0-50)
08:15:15  read_file(tool_use_shim.py, 0-50)
08:15:15  p4s25_stream_summary tool_calls=5 stop_reason='tool_use'
08:15:49  p4s25_stream_summary tool_calls=0 stop_reason='end_turn' content_chars=2863
```

### Observed

- ✅ 5 `read_file` calls with **different `path` args** in a single
  iteration
- ✅ Stream summary `stop_reason='end_turn'` (natural completion)
- ✅ `grep -i hallucination tauri-dev.log` → **0 hits**
- ✅ No `auto_resume_engaged` event at all
- ✅ Final response: 2863 chars of summary, the agent did exactly
  what was asked

## What this proves

| Claim | Pre-fix | Post-fix | Evidence |
|---|---|---|---|
| 5 different read_file in a row tolerated | ❌ HALLUCINATION_DETECTED | ✅ end_turn | tauri-dev.log 08:15:49 |
| Real death loop still trips cap | ✅ | ✅ | test_per_tool_consecutive_args_aware_same_args_still_blocks |
| Legacy `args=None` still works | n/a | ✅ | test_per_tool_consecutive_none_args_treated_consistently |
| 1229 baseline tests still green | ✅ | ✅ | pytest run 2026-05-13 |

## Files changed (commit `e60c667`)

- `backend/agent/termination.py` — args-aware counter + `_args_signature`
- `backend/agent/agent_loop.py:1074` — pass `args=tc.arguments`
- `backend/tests/test_p6_termination_gate.py` — +3 args-aware tests,
  bump default-config assertion 5→8
- `backend/tests/test_p6_agent_loop_gate.py` — use constant STUCK_ARGS

## Conclusion

The P6 Hard Gate's `HALLUCINATION_DETECTED` reason now correctly
distinguishes a real death loop (same tool + same args repeated) from
legitimate sequential exploration (same tool, different args). The fix
is verified by the same live-style test method used in the original P6
ship: real ws session → production code path → real chinzy backend →
log inspection.

**P6 + args-aware fix is production-ready.**
