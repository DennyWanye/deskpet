## 1. Persistence (SessionDB + migration)

- [x] 1.1 Add migration `008_p5s2_code_session_model_params.sql`: `ALTER TABLE code_session_provider ADD COLUMN model_params TEXT;` + bump `PRAGMA user_version`. (TDD: `test_deskpet_memory_migrator` covers idempotent apply / version bump)
- [x] 1.2 Extend `session_db.get_code_session_provider_binding` to also return `model_params` (parsed JSON or None); `set_code_session_provider_binding` to accept + persist `model_params` (JSON-serialized; None clears). Backward compatible with legacy rows.
- [x] 1.3 Write `backend/tests/test_p5s2_code_session_model_params.py` (RED→GREEN): round-trip set/get model+params; legacy row → params None; clear-binding removes row.

## 2. Param → request mapping (pure function)

- [x] 2.1 Add `_code_params_to_request(model_params: dict|None) -> dict` (pure) mapping `thinking`/`effort`→reasoning params, `context`→context hint, `fast`→speed hint for the chinzy OpenAI-compatible shape; unknown/legacy values omit/clamp (never raise).
- [x] 2.2 Unit-test the mapper exhaustively (all effort/context values + unknowns + None) — no network.

## 3. Code-session resolution wiring

- [x] 3.1 In the code-session LLM-call assembly (`backend/llm/resolution.py` / provider resolution + `main.py` dispatch), for code sessions only: read binding, fall back to `[agent] code_model` (default `gpt-5.5`) when no `preferred_model`, merge mapped params into the request. Flag-gate (`[agent] code_model`, `code_session_params_enabled`).
- [x] 3.2 Regression test: `default`/companion sid resolution unchanged (pet path provably untouched); unbound code session → `gpt-5.5`; flag-off → legacy.

## 4. IPC + config

- [x] 4.1 Extend `code_session_set_model` IPC handler in `main.py` to accept optional `params` (back-compat: missing params ⇒ provider defaults); persist via 1.2.
- [x] 4.2 Add config knobs: `[agent] code_model = "gpt-5.5"`, `code_session_params_enabled = true`, and a `[code_models]` extensible model list (GPT-5.5/Codex 5.3/Sonnet 4.6/Opus 4.7/Gemini…). Apply to repo `config.toml` AND runtime `%APPDATA%/deskpet/config.toml`.

## 5. Frontend Cursor-style picker

- [x] 5.1 Replace `tauri-app/src/code-panel/ChangeModelModal.tsx` free-text input with a structured picker: model dropdown (hardcoded Cursor reference set via `buildModelOptions`, `[code_models]` config deferred), Thinking + Fast toggles, Context segmented (300K/1M), Effort segmented (Low/Medium/High/Extra High/Max); pre-fill from current binding (`current_model` + `current_params`).
- [x] 5.2 Wire `code-panel/ws.ts` (`code_session_model_set`/`code_session_provider_set`/`code_sessions_list_response` propagate `model_params`, presence-gated so list refresh can't clobber optimistic state) + `CodePanelRoot.tsx`→`SessionGridView` Tile entrypoint to send `code_session_set_model {session_id, model, params}`; clear-binding ("清空 回全局链") keeps legacy `{session_id, model:null}` shape (no `params`).
- [x] 5.3 vitest: picker state (`buildModelParams`/`buildModelOptions`) + IPC payload (new structured shape AND legacy back-compat) + ws ack round-trip; full suite 147/147, `tsc --noEmit` EXIT=0.

## 6. Verify + live visual test + archive

- [x] 6.1 Full suite green: backend `1465 passed, 10 skipped, 0 failed` (`.venv` python, `--ignore=tests/test_deskpet_vector_worker.py` per project convention); `cd tauri-app && npx tsc --noEmit` EXIT=0 + vitest **147/147**. 0 regressions.
- [x] 6.2 Single clean stack restart (.venv backend, killed all node/python zombies); backend `Uvicorn running on :8100`, migration 008 applied at runtime (`state.db user_version=16`, `model_params` column present), code session resolves `gpt-5.5`.
- [x] 6.3 **computer-use real E2E**: opened Code Mode → 小说网站 session → new Cursor-style picker → GPT-5.5 + Effort=High + Thinking on → saved (tile shows `gpt-5.5` badge) → sent a code task (got reply). Definitive proof: live `code_session_provider` row persisted `{thinking,fast,context,effort}`; replicating `main.py` resolution → CODE session `model='gpt-5.5' code_params={reasoning_effort:'high',extra_body:{context_window:300000}}`, PET `default` provably untouched `model='deepseek-v4-pro' code_params={}`. Evidence → `evidence/T6.3-e2e.md` + 2 screenshots.
- [ ] 6.4 `openspec validate code-session-model-params --strict`; commit to `feat/2026-05-18-session-and-e2e`; then archive the change.
