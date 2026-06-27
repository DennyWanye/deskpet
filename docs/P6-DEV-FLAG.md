# P6_ENABLE_GATE — dev flag-on workflow

The P6 agent-loop refactor (TerminationGate + ContextManager) is rolled out
behind the `P6_ENABLE_GATE` environment variable. Phase 0–5 keep the flag
**default-off** so the existing 1220+ test baseline is preserved
byte-for-byte. Phase 6 will flip the default to on and remove the
deprecated soft-cap code paths.

This doc covers how to flip the flag **manually** during Phase 5 dev
shadow testing — runs locally against the real deskpet sidecar, no
production rollout.

---

## What the flag does

When `P6_ENABLE_GATE` is truthy (`1`, `true`, `yes`, case-insensitive),
`backend/config.py::is_p6_gate_enabled()` returns `True`. `AgentLoop`
reads this at construction time and:

1. Builds a default `TerminationGate(max_turns=self.max_iterations)`
   that hard-caps tool budget, wall-clock, and per-tool consecutive
   calls (set `self._gate`).
2. Builds a default `ContextManager()` that owns budget checks,
   compaction, and (critically) the **G1 fix** — `fetch_tool_result`
   bodies bypass truncation so retrieval-loop bugs are impossible
   (set `self._ctx`).

When the flag is off, `self._gate` and `self._ctx` are `None` and the
loop runs the legacy inline `_TOOL_BUDGET_HARD` / `maybe_truncate_tool_result`
paths — unchanged from before P6.

## Flipping the flag

### pytest (one-off test session)

PowerShell:

```powershell
$env:P6_ENABLE_GATE="1"
python -m pytest tests/test_p6_integration.py -v
```

bash / zsh:

```bash
P6_ENABLE_GATE=1 python -m pytest tests/test_p6_integration.py -v
```

To compare flag-off vs flag-on baselines side-by-side:

```powershell
# flag off
python -m pytest tests/ -q --ignore=tests/test_deskpet_vector_worker.py

# flag on
$env:P6_ENABLE_GATE="1"
python -m pytest tests/ -q --ignore=tests/test_deskpet_vector_worker.py
Remove-Item Env:P6_ENABLE_GATE   # clean up after
```

### Deskpet runtime (local dev)

The flag must be set in the *environment of the FastAPI sidecar
process*. The Tauri shell launches `backend.exe` / `backend/main.py`
as a child process, so:

* **From a dev shell** — start the backend manually with the env var:

  ```powershell
  $env:P6_ENABLE_GATE="1"
  python backend/main.py
  ```

  Then run the Tauri frontend (`pnpm tauri dev`) against the
  already-running sidecar (`DESKPET_SKIP_SIDECAR=1` if your launcher
  supports it; otherwise close the auto-spawned child).

* **From Tauri auto-spawn** — set the env var at the OS level so the
  child inherits it. On Windows:

  ```powershell
  [System.Environment]::SetEnvironmentVariable("P6_ENABLE_GATE", "1", "User")
  # restart the deskpet shell so the new value is read
  ```

  Or modify the `tauri.conf.json` `sidecar` env block to inject it
  per-launch (do not commit this for production rollout).

### Phase 6 rollout

In Phase 6, `config.py::is_p6_gate_enabled()` will flip its default to
`True` and the legacy soft-cap code in `agent_loop.py` /
`main.py::chat_handler` will be deleted. At that point the env var
becomes a no-op (or a kill-switch — TBD; see Phase 6 tasks).

## How to verify it took effect

In a Python REPL inside the same env:

```python
from config import is_p6_gate_enabled
print(is_p6_gate_enabled())   # True if the flag is on
```

In the AgentLoop, check `self._gate is not None` (gate path active)
and `self._ctx is not None` (ctx path active). Both flip together
under the same flag.

## Known shadow-test observations

See `openspec/changes/p6-agent-loop-refactor/evidence/5.6-integration-test-runs.md`
for the targeted Phase 5 test results (1224 passed flag-off + flag-on,
no regressions, all 4 new integration tests green).
