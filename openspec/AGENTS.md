# DeskPet — OpenSpec Agent Guide

> Read this **first** when implementing any OpenSpec change in this repo. Subagents dispatched by `/opsx:oneshot` get this verbatim — they have no other context.

## Repo layout

```
deskpet/
├── backend/                    # Python FastAPI backend (Tauri sidecar)
│   ├── main.py                 # FastAPI app + lifespan + WS handlers
│   ├── agent/                  # AgentLoop, supervisor, watchdog, nudge_queue
│   ├── providers/              # OpenAI-compat LLM provider (the relay / Ollama)
│   ├── deskpet/
│   │   ├── tools/os_tools/     # run_shell, write_file, edit_file, ...
│   │   ├── memory/             # SessionDB, migrations
│   │   ├── code_mode/          # CodeModeManager
│   │   └── permissions/        # PermissionGate (single-user, NO sandbox)
│   ├── tests/                  # pytest tests
│   ├── deskpet-backend.spec    # PyInstaller spec for frozen build
│   └── .venv/Scripts/python.exe   # dev interpreter
├── tauri-app/                  # Tauri 2 + React + Live2D frontend
│   ├── src/                    # React TS sources
│   │   ├── code-panel/         # Code-mode UI (MessageBubble, SessionGridView)
│   │   ├── pet-state/          # PetStateMachine
│   │   ├── stores/             # Zustand stores (sessionsStore)
│   │   └── App.tsx             # Main pet window
│   ├── src-tauri/              # Rust Tauri backend (window mgmt only)
│   └── package.json            # vite, vitest, tauri commands
├── openspec/
│   ├── changes/                # active OpenSpec changes
│   ├── changes/archive/        # archived (completed) changes
│   ├── specs/                  # current consolidated specs
│   └── AGENTS.md               # this file
├── resources/
│   └── busybox-w32/busybox.exe # bundled portable bash for code mode
├── scripts/                    # helpers (download_busybox.ps1, etc)
├── plans/                      # informal followup plans
└── tauri-dev.log               # latest dev run log (watch this for debugging)
```

## Critical preferences (from user memory; do not violate)

These are HARD constraints — violating them = rejected diff, no exceptions.

1. **NO sandbox / permission walls** ([feedback_no_sandbox_constraints](C:/Users/you/.claude/projects/G--projects-deskpet/memory/feedback_no_sandbox_constraints.md))
   - Don't add Claude-Code-style "first-confirm" popups for tools
   - Don't extend `PermissionGate` to new tools
   - Don't add deny patterns / command whitelists
   - Single-user desktop app; "safety" = friction, not value
   - **Exception** (allowed): irreversible-action guards like `rm -rf /`, `git push --force`, `SQL DROP` — these are手滑防护, not sandboxing

2. **Real test, not just unit tests** ([feedback_real_test](C:/Users/you/.claude/projects/G--projects-deskpet/memory/feedback_real_test.md), [feedback_simulate_manual_test](C:/Users/you/.claude/projects/G--projects-deskpet/memory/feedback_simulate_manual_test.md))
   - Every slice's final handoff MUST include real Preview MCP / computer-use UI-level E2E
   - Screenshot = evidence
   - Backend smoke scripts alone don't count
   - Cross-layer contract tests via `scripts/e2e_*.py` if covering frontend↔backend boundaries

3. **No Tauri dev orphans** ([feedback_tauri_dev_cleanup](C:/Users/you/.claude/projects/G--projects-deskpet/memory/feedback_tauri_dev_cleanup.md))
   - `TaskStop` leaves orphan `deskpet.exe` + Vite processes on Windows
   - Always taskkill before restart (see "Live E2E" section below)

4. **Chinese-first communication** (global CLAUDE.md)
   - Code comments, log messages, hint strings, doc text → Chinese
   - Identifiers (function names, class names, var names) → English (PEP8 / standard JS conventions)

## How to run

### Backend tests (pytest)

```bash
cd /path/to/deskpet/backend && python -m pytest tests/ -q --tb=line --ignore=tests/test_deskpet_vector_worker.py
```

`test_deskpet_vector_worker.py` is timing-flaky under concurrent pytest load (passes in isolation). Always exclude in batch runs.

Single-test debugging:
```bash
cd /path/to/deskpet/backend && python -m pytest tests/test_p5s2_<topic>.py -v --tb=short
```

Baseline as of 2026-05-10 commit `87ee143`: **953 passed, 11 skipped, 4 deselected** in ~73s.

### Frontend tests (vitest)

```bash
cd /path/to/deskpet/tauri-app && npm test
```

Baseline: **34 passed** in ~1s. Vitest tests must end with `.test.ts` or `.test.tsx`.

### TypeScript check

```bash
cd /path/to/deskpet/tauri-app && npx tsc --noEmit
```

Must exit 0.

### Live E2E — start fresh deskpet

ALWAYS kill old instances first. The user has explicit guidance: "Tauri dev cleanup".

```bash
# 1. Find and kill orphans
wmic process where "name='deskpet.exe' or (name='python.exe' and CommandLine like '%backend%main.py%')" get ProcessId,Name 2>&1

# 2. Stop each PID via PowerShell (taskkill /F often denied by sandbox; PS works)
# Use the registered PowerShell tool: Stop-Process -Id <PID> -Force

# 3. Start backend in dev mode (REQUIRED env so it uses dev Python not frozen exe)
cd /path/to/deskpet/tauri-app && \
  DESKPET_BACKEND_DIR=/path/to/deskpet/backend \
  npm run tauri:dev > /path/to/deskpet/tauri-dev.log 2>&1 &

# 4. Wait for "Application startup complete"
until grep -qE "Application startup complete" /path/to/deskpet/tauri-dev.log; do sleep 3; done

# 5. Grep new shared secret
grep "secret=" /path/to/deskpet/tauri-dev.log | tail -1
```

Without `DESKPET_BACKEND_DIR`, the Tauri launcher prefers the bundled frozen exe (priority 2 in `backend_launch.rs`), which on this dev box fails on `torch_cuda.dll` load. Always set it.

### Live E2E — interact with pet via computer-use

```
1. Request access to "DeskPet" + "Msedgewebview2" (the code panel uses WebView2)
2. Take screenshot, locate pet window (right edge of screen, ~1300,500)
3. Click input box, type, click 发送 button
4. Wait + screenshot to verify response
```

Code mode entry: click 🔧 wrench icon at ~(1325, 380) on the pet toolbar.

## Logging conventions

Backend uses `logging` + structlog-style formatter. Search log via:

```bash
grep -E "<event_name>|<error_class>" /path/to/deskpet/tauri-dev.log | tail -10
```

Log event-name conventions (use these prefixes for new logs):

- `p4_*` — P4 phase (memory, tools, context)
- `p4s20_*` — P4 specific sprint
- `p4s24_*` — Reasoning content roundtrip
- `p4s25_*` — Streaming + plan/cache
- `p5_*` — P5 (supervisor)
- `p5s1_*` — Pet supervisor agent
- `p5s2_*` — Self-healing harness (this lifecycle's prefix)

Error logs SHOULD include:
- `sid=<base_session_id>` for traceability
- `err_class=<TransientToolError|PermanentToolError|...>` once Phase 2 lands
- truncate long fields with `[:200]` to keep log readable

## Configuration

Live config at `%LocalAppData%\deskpet\config.toml` (user-writable). Source-of-truth schema at `/path/to/deskpet/config.toml`. Adding a new key:

1. Add default to source `config.toml` with comment
2. Add Python dataclass field in `backend/config.py`
3. Read it in `main.py` lifespan (or wherever you wire the new feature)
4. Document in the relevant OpenSpec proposal

## SessionDB schema

Currently at user_version=13 (per `005_p4s25_code_sessions.sql`). Adding a migration:

1. New file: `backend/deskpet/memory/migrations/<NNN>_<sprint>_<feature>.sql`
2. Bump `PRAGMA user_version` at the bottom
3. Add to PyInstaller spec datas (already covered by glob, just verify)
4. Test via `tests/test_deskpet_session_db.py::test_<your_migration>`

DO NOT modify existing migration files — append-only.

## LLM endpoints

Two real providers in play:

- **Primary**: your-llm-relay.example.com (`https://your-llm-relay.example.com/v1`) model `deepseek-v4-pro` — thinking-mode, returns SSE wrapped in JSON-encoded string (we have a parser for this). Flaky: gets `RemoteProtocolError("Server disconnected")` for ~5 min stretches once or twice an hour.
- **Local fallback**: ~~Ollama gemma4:e4b~~ **REMOVED** at user request 2026-05-09. Errors now surface directly. Don't re-introduce auto-fallback without explicit user OK.

API key lives in OS keychain via `_resolve_cloud_api_key()`. Don't write API keys anywhere in the repo.

## Service context

`backend/main.py` registers a `service_context` dict; common keys:

```python
service_context.get("session_db")              # SessionDB
service_context.get("session_activity")        # SessionActivityStore (P5-S1)
service_context.get("code_mode")               # CodeModeManager
service_context.get("supervisor")              # SupervisorAgent
service_context.get("watchdog")                # WatchdogLoop
service_context.get("nudge_queue")             # NudgeQueue
service_context.get("permission_gate")         # PermissionGate
service_context.get("billing_ledger")          # billing_ledger
```

Use `service_context.get(...)` (not direct module imports) so test mocks work.

## Common pitfalls

1. **`from deskpet.tools.os_tools import run_shell as rs`** resolves to the **function**, not the module (because `__init__.py` re-exports). Use `import deskpet.tools.os_tools.run_shell as rs` OR `importlib.import_module("deskpet.tools.os_tools.run_shell")`.

2. **`shutil.which("bash")` on Windows returns WSL bash** (System32\bash.exe), which errors without a WSL distro. Always resolve via `shutil.which("git")` then walk to `../usr/bin/bash.exe`. See `backend/deskpet/tools/os_tools/run_shell.py::_git_bash_path()`.

3. **busybox-w32 mangles non-ASCII args** in `-c "..."` mode (Chinese locale). Always feed via stdin: `subprocess.run([busybox, "sh"], input=command, ...)`. See `run_shell.py::run_shell()`.

4. **`pytest` doesn't pick up `test_p5s*.py` files marked async** without the `pytest.mark.asyncio` decorator. Convention: every async test starts with `@pytest.mark.asyncio`.

5. **Vitest config separate from vite config** — see `tauri-app/vitest.config.ts`. New `.test.ts` files in `src/**` auto-discovered.

6. **Don't auto-merge worktree branches** if their diffs share a file — the lead agent must do 3-way merge or ask user.

7. **the relay responses come as `sse_lines=4000+` if double-encoded** — that's the JSON-string-wrapped SSE quirk. The post-loop parser at `providers/openai_compatible.py` handles it. Don't "simplify" that code without reading the comments.

## Evidence file format

For OpenSpec live E2E or manual testing, write to `openspec/changes/<change>/evidence/<task-id>-<slug>.md`:

```markdown
# Evidence: <task-id> <slug>

**When**: 2026-05-10 16:43:44 UTC+8
**Who**: <agent or human>
**What we tested**: <one-line scenario in plain English>

## Steps
1. ...
2. ...

## Observation
- Logs: <paste 5-15 lines of relevant log; truncate secrets>
- Screenshot: <inline or path>
- Backend response: <raw JSON if applicable>

## Conclusion
- ✅ / ❌ Expected behavior <X> happened? Yes/No
- Deviations: <list any>
- Followup: <any new findings worth flagging>
```

## Phase verification commands

For `/opsx:oneshot` lead agent — run after each batch merge:

```bash
# Backend
cd /path/to/deskpet/backend && python -m pytest tests/ -q --tb=line --ignore=tests/test_deskpet_vector_worker.py 2>&1 | tail -5

# Frontend tests
cd /path/to/deskpet/tauri-app && npm test 2>&1 | tail -10

# TypeScript
cd /path/to/deskpet/tauri-app && npx tsc --noEmit 2>&1 | tail -5
```

ALL three must pass before next batch dispatches.

## Auto-fix dispatch matrix

| Failure | Subagent type | Hint |
|---|---|---|
| `pytest` fail with import error | `build-error-resolver` | "minimal fix, don't change behavior" |
| `tsc` error | `typescript-reviewer` | "type-only fix, no runtime changes" |
| Vitest assertion fail | `general-purpose` | Pass the failing test name + diff |
| Backend smoke fails to start | `python-reviewer` | Pass `tauri-dev.log` last 50 lines |
| Tauri build fails | `rust-build-resolver` | Pass `cargo build` stderr |

Max 3 attempts per failure. After that, STOP and report to user.

## Archive ceremony

After all phases done + all tests green + all evidence written:

```bash
cd /path/to/deskpet && openspec archive <change-name>
```

This moves `openspec/changes/<change>/` → `openspec/changes/archive/<date>-<change>/` and merges any new `specs/` content into the consolidated `openspec/specs/` tree.

If `openspec archive` errors → don't `mv` manually. Report the CLI error.
