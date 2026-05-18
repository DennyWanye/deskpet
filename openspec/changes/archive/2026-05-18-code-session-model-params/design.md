## Context

P5-S2 added `code_session_provider(base_session_id PK, provider_id,
preferred_model, updated_at)` + SessionDB
`get/set_code_session_provider_binding` + a free-text `ChangeModelModal`
(IPC `code_session_set_model {session_id, model}`). Code-mode agent LLM
calls resolve provider/model via `backend/llm/resolution.py` /
`provider_registry.py`; pet/companion/supervisor resolve separately.
Code-mode default model is currently `deepseek-v4-pro` (shared, via the
chinzy OpenAI-compatible proxy `https://chinzy.com/v1`). Constraints:
single-user desktop (no sandbox walls), backend interpreter
`backend/.venv/Scripts/python.exe`, TDD, current branch
`feat/2026-05-18-session-and-e2e`, **no parallel worktrees** (harness
isolation cuts a 96-commit-stale master).

## Goals / Non-Goals

**Goals:**
- Per code session, persist & runtime-switch model **and** params
  (thinking/fast/context/effort), Cursor-parity, effective on the
  session's next agent call.
- `gpt-5.5` becomes the code-mode default (config knob).
- Fully Strangler-Fig revertible.

**Non-Goals:**
- No change to pet/companion/supervisor model resolution.
- No new provider adapters/deps (reuse chinzy OpenAI-compatible).
- No sandbox/permission walls.
- No multi-account/billing rework.

## Decisions

- **One JSON column, not N columns.** Add `model_params TEXT` to
  `code_session_provider` (migration 008) holding
  `{"thinking":bool,"fast":bool,"context":"300k|1m","effort":"low|medium|high|extra_high|max"}`.
  Why: future Cursor knobs need zero migration; NULL/absent = provider
  defaults (backward compatible with existing rows). Alternative (one
  column per knob) rejected — migration churn + rigid.
- **Resolution merge point is the code-session LLM-call assembly**, not
  the global provider chain. The binding (model + params) is read for
  code sessions only and merged into the chinzy OpenAI request:
  `model` → wire model; `thinking`/`effort` → reasoning params;
  `context` → max context hint; `fast` → speed/streaming hint. Pet path
  never reads the binding (provably untouched — covered by a regression
  test asserting `default`/companion sid resolves unchanged).
- **gpt-5.5 default via existing code-model knob.** Reuse a single
  `[agent] code_model` config (Strangler-Fig): code sessions with no
  explicit binding fall back to `code_model` (=`gpt-5.5`); everyone
  else uses the construction-time model. Empty/flag-off ⇒ legacy.
- **Param→request mapping is a pure function** (`_code_params_to_request`)
  unit-tested independently of the network; unknown/legacy values clamp
  to provider default (never error the turn).
- **Frontend: replace free-text modal with a structured picker** —
  model dropdown (list from a config knob, free-extensible), Thinking/
  Fast toggles, Context segmented (300K/1M), Effort segmented
  (Low…Max). Reachable from each session card; “保存” sends extended
  `code_session_set_model {session_id, model, params}`. Empty model +
  default params = “clear binding” (restore global chain) — preserves
  existing clear semantics.
- **IPC is additive/back-compat**: old `{session_id, model}` still
  valid (params optional → provider defaults). Backend tolerates
  missing `params`.

## Risks / Trade-offs

- [chinzy param name drift: thinking/effort/context request keys may
  differ per upstream model] → map via a small per-known-model table +
  a safe generic fallback (omit unknown keys rather than send bad ones);
  document; covered by mapping unit tests.
- [gpt-5.5 not served by chinzy] → only code sessions error; pet path
  isolated; `code_model=""` instantly reverts.
- [Stale binding for a deleted code session] → app-layer cleanup already
  the contract (migration 007 note); unchanged; out of scope.
- [UI/IPC drift old↔new] → additive payload + backend default-tolerant;
  vitest covers both shapes.

## Migration Plan

1. Add migration 008 (`model_params TEXT`, `PRAGMA user_version` bump);
   migrator applies idempotently on startup (existing migrator path).
2. Ship behind flags: `[agent] code_model="gpt-5.5"`,
   `[code_e2e]`/`[agent]` `code_session_params_enabled=true`.
3. Rollback: set `code_model=""` and `code_session_params_enabled=false`
   → resolution ignores params + default; rows remain harmless. No
   down-migration needed (extra nullable column is inert).

## Open Questions

- Exact chinzy request keys for `thinking`/`effort` per model — resolve
  empirically during apply; default to OpenAI-style
  `reasoning_effort` + omit-if-unknown.
