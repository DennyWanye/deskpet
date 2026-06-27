# P6 Live E2E Handoff (context-compaction-safe)

> Snapshot taken before /compact to preserve state. Anything below survives compaction.

## Where we are

**P6 refactor 100% done + archived** (commit `c9532c1`). Master:
- 1226 pytest passed, 0 regressions
- `is_p6_gate_enabled()` default ON (env var unset → True)
- TerminationGate + ContextManager are the only paths; legacy branches deleted
- 14 inline patches (A1/A2/B1/B2/B3/C2/D1/D2/F1/G1/G2/G3) replaced by 2 abstractions

## What's next (live E2E)

Goal: prove the hard gate works in production against real the relay LLM calls.

### Test plan

1. **Restart deskpet** with default config (flag default ON now)
2. **Open Code Mode** (wrench icon top-right of pet)
3. **Push a long task** to test session — e.g. "重写整个 backend/agent/agent_loop.py，先 read 完整文件，然后逐段 edit"
4. **Watch backend log** for these markers:
   - `gate.record_tool_call` shouldn't appear (no logger.info there by design)
   - Look for `ErrorEvent reason="error_tool_budget"` if model loops
   - Look for `ErrorEvent reason="hallucination"` if same tool 5× consecutively
   - Look for `ErrorEvent reason="error_wall_clock_exceeded"` if >10min
5. **Compare with pre-P6 logs**: pre-P6 had `p5s2_tool_budget_exhausted iter=N tools_used=N+` warnings repeating but loop continued. After P6: should see ONE log line, then loop terminates within 1 iteration.

### Acceptance for live E2E

- ✅ Long task either completes (end_turn) within 50 iterations OR terminates with one of the hard-cap reasons
- ✅ NO `tools_used` count > tool_budget_hard (40) — should be impossible now
- ✅ NO 20-minute "stuck" feeling — wall_clock=600s hard cap
- ❌ If a legacy soft-cap WARN log appears → BUG, file follow-up

## Critical commands

### Restart deskpet (Windows)
```powershell
# Kill any leftover (via P6_ENABLE_TASKKILL permissions in .claude/settings.local.json)
Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq "deskpet.exe") -or
  ($_.Name -eq "python.exe" -and $_.CommandLine -match "main\.py") -or
  ($_.Name -eq "node.exe" -and $_.CommandLine -match "tauri|vite") } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 3
```
```bash
# Launch
cd /path/to/deskpet/tauri-app && DESKPET_BACKEND_DIR=/path/to/deskpet/backend \
  npm run tauri:dev > /path/to/deskpet/tauri-dev.log 2>&1 &
# Wait
until grep -qE "Application startup complete" /path/to/deskpet/tauri-dev.log; do sleep 4; done
```

### Verify P6 active
```bash
# Should see: supervisor_provider_resolved id=relay ... model=deepseek-v4-pro
grep -E "supervisor_provider_resolved|provider_registry_init" /path/to/deskpet/tauri-dev.log | tail -3
```

### Visual test sequence (computer-use)
```
1. open_application "Deskpet"   # bring to front
2. Click wrench at (1325, 381)   # enter code mode
3. Click input box (~410, 380) — type a long task
4. Click 发送
5. Wait, screenshot every 30s
6. After 5 min, check log for ErrorEvent reasons
```

## Configuration

Current the relay provider in user config:
- `~/AppData/Roaming/deskpet/config.toml` has `[[llm.endpoints]]`
- id=relay, base_url=https://your-llm-relay.example.com/v1, default_model=deepseek-v4-pro
- 88 models (probed)

Supervisor wired to same provider after A1 fix.

## Open questions for after live test

1. Does deepseek-v4-pro actually trigger the hard gate? (Models behave differently — claude-haiku might never run away)
2. Wall_clock=600s — appropriate for production? Maybe too short for legitimate long refactors?
3. per_tool_max_consecutive=5 — should it be higher for `read_file` (legitimate to read many files)? Possibly need per-tool override.

## Files to read on resume

```
docs/P6-agent-loop-architecture.md     # architecture overview
docs/P6-migration-decisions.md         # why each decision
openspec/changes/archive/2026-05-12-p6-agent-loop-refactor/proposal.md
backend/agent/termination.py           # the gate
backend/agent/context_manager.py       # the ctx
backend/tests/test_p6_integration.py   # end-to-end test patterns
```

## Recent commits (master tip)

```
c9532c1 chore(p6): Batch 6 — Phase 7 archive p6-agent-loop-refactor
081086e feat(p6): Batch 5 — Phase 6 flag default ON + remove deprecated + docs
6b263da feat(p6): Batch 4 — Phase 5 integration tests + dev flag-on docs
1bb6d4f feat(p6): Batch 3 — Phase 4 AgentLoop+ChatHandler integrate ContextManager
2f42480 feat(p6): Batch 2 — Phase 3 AgentLoop integrates TerminationGate
ed1c0e4 feat(p6): Batch 1 — Phase 0+1+2 feature flag + TerminationGate + ContextManager
b5d9eb1 docs(p6): OpenSpec proposal for AgentLoop pipeline refactor
```
