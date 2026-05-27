## ADDED Requirements

### Requirement: Per-code-session model+params binding is persisted

The system SHALL persist, per code-mode base session, a binding of
`provider_id`, `preferred_model`, and `model_params` where `model_params`
is a JSON object with keys `thinking` (boolean), `fast` (boolean),
`context` (one of `300k`,`1m`), `effort` (one of `low`,`medium`,`high`,
`extra_high`,`max`). Absent/NULL `model_params` MUST mean "use provider
defaults". Companion/pet/supervisor sessions MUST NOT write or read this
binding.

#### Scenario: Set model + params round-trips
- **WHEN** `set_code_session_provider_binding(sid, provider_id=None, preferred_model="gpt-5.5", model_params={"thinking":true,"fast":false,"context":"1m","effort":"high"})` is called for a code session
- **THEN** a subsequent `get_code_session_provider_binding(sid)` returns exactly that `preferred_model` and `model_params`

#### Scenario: Legacy row without params stays valid
- **WHEN** a `code_session_provider` row exists from before migration 008 (no `model_params`)
- **THEN** `get_code_session_provider_binding(sid)` returns `model_params` as `None` and resolution falls back to provider defaults without error

#### Scenario: Clear binding restores global chain
- **WHEN** the binding is cleared (no provider, no model, default params)
- **THEN** the row is removed and the session resolves via the global provider chain (pre-existing P5-S2 semantics preserved)

### Requirement: Bound model+params apply to the code session's next agent call

When a code session dispatches an agent LLM call, the system SHALL merge
the session's bound `preferred_model` and `model_params` into the
the relay OpenAI-compatible request: model id → wire `model`; `thinking`/
`effort` → reasoning parameters; `context` → context-window hint;
`fast` → speed hint. Switching the binding SHALL take effect on the
next call for that session without restart. Unknown/legacy param values
MUST clamp to the provider default and MUST NOT error the turn.

#### Scenario: Switching mid-session affects the next call
- **WHEN** a code session has made a call on model A, then the user changes the binding to model B with `effort=max`
- **THEN** the next agent call for that session uses model B and the max-effort reasoning parameter (no backend restart)

#### Scenario: Pet/companion path is unaffected
- **WHEN** the `default` (companion) or any non-code session dispatches an LLM call
- **THEN** resolution does NOT read `code_session_provider` and the model/params are unchanged from current behavior

#### Scenario: Param→request mapping is total
- **WHEN** `model_params` contains an unrecognized `effort` or `context` value
- **THEN** the mapper omits/clamps that key to the provider default and the request still succeeds

### Requirement: Code-mode default model is configurable and distinct

The system SHALL resolve the code-mode default model from a config knob
(`[agent] code_model`, default `gpt-5.5`) when a code session has no
explicit `preferred_model`, while pet/companion/supervisor continue to
use their existing model. Setting `code_model` empty SHALL revert
code sessions to the legacy shared model (Strangler-Fig).

#### Scenario: Unbound code session uses code_model
- **WHEN** `[agent] code_model = "gpt-5.5"` and a code session has no `preferred_model`
- **THEN** that session's agent calls use `gpt-5.5`, while the pet `default` session still uses `deepseek-v4-pro`

#### Scenario: Flag revert
- **WHEN** `[agent] code_model = ""` (or the params flag is off)
- **THEN** code sessions resolve the legacy shared model and params are ignored — no behavior change vs pre-change

### Requirement: UI exposes a Cursor-style per-session model+params picker

The code panel SHALL provide, per session card, a picker to choose the
model (from a config-extensible list) and toggle Thinking, Fast, set
Context (300K/1M) and Effort (Low/Medium/High/Extra High/Max),
switchable at any time, sending an additive `code_session_set_model`
IPC carrying `{session_id, model, params}`. The legacy
`{session_id, model}` payload MUST remain accepted.

#### Scenario: Picker persists and reflects current binding
- **WHEN** the user opens the picker for a session with an existing binding
- **THEN** the picker pre-fills the current model + params, and saving sends `{session_id, model, params}` which is persisted and reflected on reopen

#### Scenario: Back-compat IPC
- **WHEN** an IPC `code_session_set_model {session_id, model}` arrives without `params`
- **THEN** the backend persists model with `model_params` = provider defaults (no error)
