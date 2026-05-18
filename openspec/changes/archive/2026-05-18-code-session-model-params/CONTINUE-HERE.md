# CONTINUE-HERE — code-session-model-params (handoff)

> Fresh agent: read this top-to-bottom. It is self-contained. Do NOT
> assume any memory of prior sessions. Last updated after S1 (T4.1).

## 0. TL;DR

OpenSpec change **`code-session-model-params`** (Cursor-style per-Code-
project model + params switcher). **Backend is 100% done & green &
committed. Frontend (T5) + verify/archive (T6) remain.**

Resume:
```
cd /g/projects/deskpet
git switch feat/2026-05-18-session-and-e2e          # the working branch
git log --oneline -3                                 # expect 58b1065 at/near HEAD
/opsx:apply code-session-model-params                # tasks.md is the ledger
```
Tasks ledger: `openspec/changes/code-session-model-params/tasks.md`
(checked = done). Remaining unchecked = 5.1 5.2 5.3 (frontend) + 6.1
6.2 6.3 6.4 (verify/E2E/archive).

## 1. HARD LANDMINES (read before doing anything)

1. **DO NOT use parallel worktrees / `openspec-oneshot` / `Agent`
   subagents with `isolation: worktree`.** This harness's worktree
   isolation cuts branches from a **96-commit-stale `master`
   (`c6ed551`)**; current real work is on `feat/2026-05-18-session-and-e2e`
   (HEAD `58b1065`, ~current). Worktree branches are **unmergeable**
   (would revert ~30k lines). **Work SERIAL, in the main tree, on this
   branch.**
2. **Backend interpreter is fixed:** `G:/projects/deskpet/backend/.venv/Scripts/python.exe`
   (Windows; git-bash forward-slash paths). Run pytest with this exact
   path from `backend/`.
3. **Runtime config ≠ repo config.** Backend reads
   `C:\Users\24378\AppData\Roaming\deskpet\config.toml` (a.k.a.
   `%APPDATA%/deskpet/config.toml`). Repo `config.toml` is only the
   seed/template. Any config that must take effect at runtime goes in
   **both** (repo for the template, APPDATA for the running backend).
4. **LLM endpoint** is `%APPDATA%/deskpet/llm_runtime.json`
   (currently chinzy `https://chinzy.com/v1`, model `deepseek-v4-pro`,
   OpenAI-compatible). Pet/companion/supervisor use this. Code mode
   layers `[agent] code_model` on top (see §3).
5. **New test files MUST use `asyncio.run(...)`**, never
   `asyncio.get_event_loop().run_until_complete(...)` — the latter
   passes in isolation but fails in the full suite (prior async tests
   close the loop).
6. **Schema-version / binding-dict-shape assertions are brittle.** Any
   migration bump or binding-shape change breaks tests that hard-code
   the old value; update them as a contract change (don't weaken
   behavior). (Already handled for v15→16 / +model_params.)
7. **Single clean stack** for any live run: kill `deskpet`/`python`/
   `node` zombies on ports 8100 (backend) & 5173 (Vite) before
   restart; no multi-instance. No sandbox/permission walls (single-user
   desktop pet — footgun-prevention only).
8. **Quality gate, no exceptions:** backend change → pytest +
   `py_compile`; frontend change → `npx tsc --noEmit` (EXIT=0) +
   `npm test`. "Looks like it runs" is not acceptable; T6 requires a
   real computer-use visual test + screenshot.

## 2. WHAT IS DONE (committed, green)

Branch `feat/2026-05-18-session-and-e2e`:
- `d4c0641` — OpenSpec propose (proposal/design/specs/tasks, `openspec
  validate --strict` passes).
- `461daf3` — backend T1–T3:
  - `backend/deskpet/memory/migrations/008_p5s2_code_session_model_params.sql`
    — `ALTER TABLE code_session_provider ADD COLUMN model_params TEXT;`
    `PRAGMA user_version=16`. `migrator.py TARGET_SCHEMA_VERSION=16`.
  - `session_db.py` `get/set_code_session_provider_binding` round-trip
    `model_params` (JSON; legacy/None-safe; corrupt JSON → None).
  - `backend/llm/code_params.py` — **pure total** mapper
    `code_params_to_request(model_params) -> dict` (thinking/fast/
    context/effort → `reasoning_effort` + `extra_body`; unknown →
    omit/clamp; None/{} → {}).
  - `backend/llm/resolution.py` — code sessions: read `model_params`,
    fall back to `[agent] code_model` when no `preferred_model`,
    attach `entry.code_params`; `_ChainEntry` gained `code_params`
    slot; pinned path normalized to `_ChainEntry`. **Companion/pet
    path (`is_code_session=False`) provably untouched.**
  - `backend/providers/openai_compatible.py` — ctor `code_params=`;
    `_merge_code_params()` spliced into ALL 3 request builders
    (`chat_stream`, `chat_with_tools`, `chat_stream_with_tools`).
  - `backend/main.py` (~line 3294) — passes `code_default_model`
    (from `[agent] code_model`, code sessions only) +
    `code_params=getattr(_entry,"code_params",None)` into the provider.
- `58b1065` — T4.1: `main.py` `code_session_set_model` IPC accepts
  optional `payload.params` (dict) → persisted; legacy
  `{session_id,model}` (no `params`) ⇒ `None` (provider defaults);
  provider-only path preserves params; response echoes `model_params`.
  Updated 11 schema-bump-stale assertions; hardened 2 new test files.

Tests (all green, run with the `.venv` python from `backend/`):
- `tests/test_p5s2_code_session_model_params.py` (5)
- `tests/test_code_params_mapper.py` (23)
- `tests/test_code_session_resolution_params.py` (4)
- `tests/test_p5s2_ipc_providers.py` (12, incl. new params round-trip)
- Full suite previously: **1456 passed** + the above; 0 regressions
  (one known-flaky `test_deskpet_vector_worker.py` is `--ignore`d per
  project convention).

## 3. CONFIG TRUTH (what actually exists — no guessing)

Added to **both** repo `config.toml` AND `%APPDATA%/deskpet/config.toml`:
```toml
[agent]
code_model = "gpt-5.5"   # code-mode default; "" = revert to legacy shared model
```
`[code_e2e]` section exists (Playwright MCP etc.) — unrelated to this
change except it lives nearby.

**NOT added** (tasks.md 4.2 mentioned them; deliberately deferred —
decide in S2): there is **no** `code_session_params_enabled` flag and
**no** `[code_models]` model-list config. Consequences:
- Revert path for this feature = set `[agent] code_model = ""` AND/OR
  clear per-session bindings (no separate enable flag exists). This is
  acceptable Strangler-Fig (code_model empty ⇒ resolution passes
  `code_default_model=None` ⇒ legacy).
- The frontend model dropdown (S2) has **no config list to read** yet.
  Decide: (a) hardcode a sensible list in the picker (GPT-5.5 / Codex
  5.3 / Sonnet 4.6 / Opus 4.7 / Gemini — the Cursor reference set),
  or (b) add an optional `[code_models]` list to both configs and read
  it. (a) is the minimal path and fine for S1-scope; (b) is a small
  add if you want user-extensible without a rebuild.

## 4. THE CONTRACT (frontend S2 must match this exactly)

IPC the picker sends (additive; legacy still accepted):
```
ws.send  { type: "code_session_set_model",
           payload: { session_id, model, params } }
```
`params` is a dict (or omitted = provider defaults):
```
{ "thinking": bool,
  "fast":     bool,
  "context":  "300k" | "1m",
  "effort":   "low" | "medium" | "high" | "extra_high" | "max" }
```
Backend response (handle in ws.ts to update the session card):
```
{ type: "code_session_model_set",
  payload: { session_id, provider_id, preferred_model, model_params } }
```
Clear-binding semantics preserved: model empty + (provider_id null) +
no params ⇒ row deleted ⇒ session back to global chain.
Effort `extra_high`/`max` clamp to `high` server-side (OpenAI only has
low/medium/high) — the UI may still show all 5 rungs.

## 5. REMAINING WORK

### S2 — T5 frontend (own session; ~medium)
Files:
- `tauri-app/src/code-panel/ChangeModelModal.tsx` — replace the
  free-text `<input>` with a structured Cursor-style picker: model
  **dropdown** (hardcode the list per §3a unless you add `[code_models]`),
  **Thinking** toggle, **Fast** toggle, **Context** segmented
  (300K / 1M), **Effort** segmented (Low / Medium / High / Extra High /
  Max). Pre-fill from the session's current binding
  (`current_model` prop already exists; you'll also need current
  `model_params` — extend the prop/IPC fetch as needed).
- `tauri-app/src/code-panel/ws.ts` — send `params` in
  `code_session_set_model`; handle `code_session_model_set`'s
  `model_params` echo.
- `tauri-app/src/code-panel/CodePanelRoot.tsx` — the session-card
  provider/model entrypoint that opens the modal (see existing
  "provider: Global Chain" dropdown + ChangeModelModal trigger).
- Reference UI: Cursor's model picker (MAX Mode / Thinking / Fast /
  Context 300K·1M / Effort Low…Max / model list). Match the existing
  code-panel dark visual style.
Gate: `cd tauri-app && npx tsc --noEmit` EXIT=0 + `npm test` all pass
(add vitest for picker state + the IPC payload shape, new AND legacy).
Then check 5.1/5.2/5.3 in tasks.md, commit to this branch.

### S3 — T6 verify + live + archive (own session)
1. `cd backend && G:/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_deskpet_vector_worker.py` → 0 regressions; `cd tauri-app && npx tsc --noEmit` + `npm test`.
2. Clean single-stack restart (kill zombies first). Dev stack launch
   (PowerShell, background):
   ```
   $env:DESKPET_PYTHON="G:\projects\deskpet\backend\.venv\Scripts\python.exe";
   $env:DESKPET_BACKEND_DIR="G:\projects\deskpet\backend";
   $env:DESKPET_DEV_ROOT="G:\projects\deskpet";
   Set-Location G:\projects\deskpet\tauri-app; npm run tauri dev
   ```
   Backend log lives in the task output file; wait for
   `startup complete` + `Uvicorn running on http://127.0.0.1:8100`.
   Confirm migration 008 applied (`user_version=16`) and a code
   session resolves `gpt-5.5`.
3. **computer-use real E2E** (mandatory, screenshot = evidence):
   open Code Mode → a project session → open the new picker → set
   model = GPT-5.5, Effort = High, Thinking on → send a code task →
   grep backend log: that session's chinzy call used the chosen model
   + `reasoning_effort` while the pet `default` session still uses
   `deepseek-v4-pro`. Save screenshot → `openspec/changes/
   code-session-model-params/evidence/`.
4. `openspec validate code-session-model-params --strict`; commit;
   then `openspec archive code-session-model-params`.

## 6. OPEN DECISION (needs the user, not the agent)

`feat/2026-05-18-session-and-e2e` has accumulated a large body of
session work (pet left panel / anti-jitter / sanitizer / history /
image fix / Playwright MCP / dormant browser-use+computer-use tools /
this change's backend). It has **not** been merged to `master`.
Recommended: finish this change on the branch, then open one PR /
merge as a single reviewable, revertible unit. Dormant flags
`[code_e2e].browser_use_enabled` / `computer_use_enabled` stay `false`
until the user explicitly enables (autonomous browser / OS control —
needs `cd backend && uv sync` first).

## 7. METHOD (so this doesn't blow context again)

One OpenSpec phase ≈ one session + a checkpoint commit; `tasks.md` is
the durable ledger. Don't try to one-shot a 16-task cross-stack change
in a single session.
