## Why

Code mode already persists a per-session **provider + preferred_model** binding
(P5-S2, table `code_session_provider`; UI = a free-text "改 model" modal). But
users can't tune **how** a model runs per project — there's no Cursor-style
picker for Thinking / Fast / Context window / reasoning Effort, and switching
requires typing a raw model id. The default code-mode model is also still
`deepseek-v4-pro` (shared with the pet); the team wants `gpt-5.5` for coding.
This change brings Cursor-parity: pick a model AND its parameters per project
session, switch anytime, applied immediately to that session's next agent call.

## What Changes

- Extend the per-code-session binding to also persist **model parameters**:
  `thinking` (on/off), `fast` (on/off), `context` (`300k`|`1m`), `effort`
  (`low`|`medium`|`high`|`extra_high`|`max`). Stored as one JSON column
  (`model_params`) so future knobs need no migration.
- New DB migration (008) adding `model_params TEXT` to `code_session_provider`;
  SessionDB get/set API extended to round-trip params (backward compatible —
  NULL = provider defaults).
- Resolution path: when a code session dispatches an agent LLM call, merge its
  bound model + params into the request (model id, plus
  reasoning/thinking/effort/context knobs mapped to the the relay
  OpenAI-compatible request shape). Pet / companion / supervisor paths are
  **untouched** (only code sessions read this binding).
- Default code-mode model becomes `gpt-5.5` (config knob, Strangler-Fig flag);
  pet / companion / supervisor stay `deepseek-v4-pro`.
- Frontend: replace the free-text `ChangeModelModal` with a Cursor-style picker
  — model dropdown (GPT-5.5 / Codex 5.3 / Sonnet 4.6 / Opus 4.7 / Gemini …,
  list config-extensible) + Thinking/Fast toggles + Context (300K/1M) + Effort
  (Low…Max), reachable from each session card, switchable anytime; sends an
  extended `code_session_set_model` IPC carrying params.
- All behavior flag-gated (`[code_e2e]`/`[agent]` Strangler-Fig knobs) so the
  whole feature reverts to the current single-model behavior.

## Capabilities

### New Capabilities
- `code-session-model-params`: per-code-session, runtime-switchable model **and**
  model parameters (thinking/fast/context/effort), persisted in SessionDB,
  applied to that session's subsequent agent LLM calls; includes the
  config-driven code-mode default model (`gpt-5.5`) distinct from the
  pet/companion/supervisor model.

### Modified Capabilities
<!-- None in openspec/specs/. The pre-existing P5-S2 per-session
     provider/preferred_model binding lives under the un-archived
     `multi-provider-management` change, not consolidated specs — this
     change supersets it via the new capability above; no consolidated
     spec requirement changes. -->

## Impact

- **Backend**: `backend/deskpet/memory/migrations/008_*.sql` (new),
  `backend/deskpet/memory/session_db.py` (get/set binding +params),
  `backend/llm/resolution.py` + provider/model resolution for code sessions,
  `backend/main.py` (`code_session_set_model` IPC schema extension; code-mode
  default-model wiring), `config.toml` (`code_model`, model-list knobs).
- **Frontend**: `tauri-app/src/code-panel/ChangeModelModal.tsx` →
  Cursor-style picker; `CodePanelRoot.tsx` session-card provider/model UI;
  `code-panel/ws.ts` IPC payload.
- **Tests**: pytest (SessionDB params round-trip, resolution merges
  params/model for code sessions, pet path unchanged, default-model flag),
  vitest (picker state/IPC), tsc.
- **No** new sandbox/permission walls (single-user desktop pet). Pet /
  companion / supervisor model paths explicitly out of scope.
- Deps: none new (reuses the relay OpenAI-compatible endpoint).
