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

- [ ] 5.1 Replace `tauri-app/src/code-panel/ChangeModelModal.tsx` free-text input with a structured picker: model dropdown (from config list), Thinking + Fast toggles, Context segmented (300K/1M), Effort segmented (Low/Medium/High/Extra High/Max); pre-fill from current binding.
- [ ] 5.2 Wire `code-panel/ws.ts` + `CodePanelRoot.tsx` session-card entrypoint to send `code_session_set_model {session_id, model, params}`; keep clear-binding semantics (empty model + default params).
- [ ] 5.3 vitest: picker state + IPC payload (new shape AND legacy back-compat); `tsc --noEmit` EXIT=0.

## 6. Verify + live visual test + archive

- [ ] 6.1 Full suite green: `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` (no regressions), `cd tauri-app && npx tsc --noEmit` EXIT=0 + `npm test` all pass.
- [ ] 6.2 Single clean stack restart (.venv backend, kill zombies); confirm migration 008 applied + code session resolves `gpt-5.5`.
- [ ] 6.3 **computer-use real E2E**: open Code Mode → a project session → open the picker, switch model to GPT-5.5 + set Effort=High + Thinking on → send a code task → confirm backend log shows that session's call used the chosen model+params and pet/`default` still deepseek-v4-pro; screenshot evidence → `evidence/`.
- [ ] 6.4 `openspec validate code-session-model-params --strict`; commit to `feat/2026-05-18-session-and-e2e`; then archive the change.
