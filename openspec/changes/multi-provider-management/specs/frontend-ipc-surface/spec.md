# Spec: frontend-ipc-surface (MODIFIED)

## ADDED Requirements

### Requirement: 5 settings_providers_* + 2 code_session_set_* IPC messages

WS control channel SHALL accept and respond to:

| Inbound (frontend → backend) | Outbound (backend → frontend) |
|---|---|
| `settings_providers_list_request` | `settings_providers_list_response { providers: [...] }` |
| `settings_providers_add { id, name, base_url, model, api_key, priority?, enabled? }` | `settings_providers_added { provider }` + `providers_changed` broadcast |
| `settings_providers_update { id, patch: {name?, base_url?, model?, api_key?, priority?, enabled?} }` | `settings_providers_updated { provider }` + `providers_changed` broadcast |
| `settings_providers_remove { id }` | `settings_providers_removed { id }` + `providers_changed` broadcast |
| `settings_providers_reorder { ordered_ids: [...] }` | `settings_providers_reordered { providers: [...] }` + `providers_changed` broadcast |
| `code_session_set_provider { session_id, provider_id?: string|null }` | `code_session_provider_set { session_id, provider_id, preferred_model }` |
| `code_session_set_model { session_id, model?: string|null }` | `code_session_model_set { session_id, provider_id, preferred_model }` |

#### Scenario: list_request returns sanitized providers

- **GIVEN** registry has 2 providers with stored api_keys
- **WHEN** ws receives `{type: "settings_providers_list_request"}`
- **THEN** ws sends `settings_providers_list_response` with providers list
- **AND** each provider's `api_key` field is `"********"` (NOT plaintext)
- **AND** each provider has full metadata: `id, name, base_url, model, priority, enabled`

#### Scenario: add validates required fields + uniqueness

- **GIVEN** registry has provider `the relay`
- **WHEN** ws receives `settings_providers_add` with id=`"relay"` (duplicate) OR missing `base_url`
- **THEN** ws sends error response `{type: "settings_providers_error", reason: "duplicate_id" | "missing_field", detail: "..."}`
- **AND** registry unchanged
- **AND** NO `providers_changed` broadcast

#### Scenario: update with partial patch

- **GIVEN** provider `the relay` exists with priority=1
- **WHEN** ws receives `settings_providers_update {id: "relay", patch: {priority: 5}}`
- **THEN** only priority is updated; name/base_url/model/api_key/enabled unchanged
- **AND** if patch contains `api_key` (non-empty string), keychain entry is updated; if empty/missing, keychain unchanged
- **AND** `providers_changed` broadcast to all control connections

#### Scenario: reorder validates ordered_ids matches registry

- **GIVEN** registry has providers `[a, b, c]`
- **WHEN** ws receives `settings_providers_reorder {ordered_ids: ["b", "a"]}` (missing `c`!)
- **THEN** ws sends `settings_providers_error {reason: "incomplete_order", detail: "missing: c"}`
- **AND** order unchanged

#### Scenario: providers_changed broadcasts to ALL control conns

- **GIVEN** 2 control ws connections open (e.g., main pet panel + code panel window)
- **WHEN** any settings_providers_* mutation succeeds
- **THEN** BOTH ws receive `providers_changed {providers: [...]}` event
- **AND** both UIs re-render their provider lists

#### Scenario: code_session_set_provider with null clears binding

- **GIVEN** code session "vpn-tunnel" has `provider_id="relay"` binding
- **WHEN** ws receives `code_session_set_provider {session_id: "vpn-tunnel", provider_id: null}`
- **THEN** SessionDB binding row updated/deleted appropriately
- **AND** ws response `code_session_provider_set {session_id: "vpn-tunnel", provider_id: null, preferred_model: null}`

### Requirement: Frontend handles new outbound events

#### Scenario: providers_changed re-renders settings + code panel

- **GIVEN** Settings panel and code panel both open
- **WHEN** ws emits `providers_changed {providers: [...]}`
- **THEN** Settings panel's drag-list re-renders with new order/enabled state
- **AND** every code session card's provider dropdown re-renders with new options
- **AND** if a card's currently-selected `provider_id` is no longer in the list, the dropdown auto-falls-back to "Global Chain (default)" + emits a small toast "Provider X 已删除，本会话回到全局链"

#### Scenario: code_session_provider_changed triggered by provider removal

- **GIVEN** code session "x" pinned to provider "openrouter-claude"
- **WHEN** ws emits `code_session_provider_changed {session_id: "x", reason: "provider_removed"}` (server-initiated, not user click)
- **THEN** UI silently updates the dropdown to "Global Chain" + shows the toast above
