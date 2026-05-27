# Spec: code-mode (MODIFIED)

## MODIFIED Requirements

### Requirement: Code session response includes provider binding

`code_sessions_list_response` and `code_mode_state` ws messages SHALL include `provider_id` and `preferred_model` fields per session, so the frontend dropdown can render the current selection.

#### Scenario: code_sessions_list_response includes binding

- **GIVEN** SessionDB has 2 code sessions, "vpn-tunnel" pinned to "the relay", "research" with no binding
- **WHEN** frontend requests `code_sessions_list_request`
- **THEN** ws response includes:
  ```json
  {
    "type": "code_sessions_list_response",
    "sessions": [
      {"base_session_id": "vpn-tunnel", ..., "provider_id": "the relay", "preferred_model": null},
      {"base_session_id": "research", ..., "provider_id": null, "preferred_model": null}
    ]
  }
  ```

#### Scenario: code_mode_state on enter populates binding

- **GIVEN** previously-bound code session "vpn-tunnel" exists in SessionDB
- **WHEN** user re-enters code mode for that project (after restart)
- **THEN** `code_mode_state` ws event includes the saved `provider_id="the relay"` + `preferred_model=null`
- **AND** UI dropdown defaults to "the relay" (NOT "Global Chain")

### Requirement: SessionGridView card has provider/model dropdown

Each code session card in `SessionGridView.tsx` SHALL render a compact dropdown above its chat input showing:
- Selected provider name (or "Global Chain" if `provider_id == null`)
- All available providers from the latest `providers_changed` event
- A "✏️ 改 model..." sub-action that opens a small modal to set `preferred_model`
- Visual indicator when overriding global chain (small "🔒" icon)

#### Scenario: Selecting a provider sends ws + updates UI

- **GIVEN** card for "research" currently shows "Global Chain"
- **WHEN** user picks "openrouter-claude" from dropdown
- **THEN** ws sends `code_session_set_provider {session_id: "research", provider_id: "openrouter-claude"}`
- **AND** UI optimistically shows "openrouter-claude 🔒"
- **AND** confirmation `code_session_provider_set` echoes back, no UI change needed (optimistic was correct)

#### Scenario: Picking "Global Chain" clears binding

- **GIVEN** card pinned to "the relay"
- **WHEN** user picks "Global Chain (default)" from dropdown
- **THEN** ws sends `code_session_set_provider {session_id: ..., provider_id: null}`
- **AND** UI shows "Global Chain"
- **AND** lock icon disappears

### Requirement: CodeModeManager exposes provider binding

`CodeModeManager` SHALL expose:
- `await get_provider_binding(base_sid) -> {provider_id?, preferred_model?}`
- `await set_provider_binding(base_sid, provider_id?, preferred_model?)`

These are pass-throughs to SessionDB, used by chat handler and ws message handlers.

#### Scenario: get_provider_binding returns dict

- **GIVEN** SessionDB has binding for "vpn-tunnel"
- **WHEN** `await cmm.get_provider_binding("vpn-tunnel")`
- **THEN** returns `{"provider_id": "the relay", "preferred_model": None}`

#### Scenario: get_provider_binding for unbound session returns nulls

- **GIVEN** no row for "research"
- **WHEN** `await cmm.get_provider_binding("research")`
- **THEN** returns `{"provider_id": None, "preferred_model": None}`
