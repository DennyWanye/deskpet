# Spec: code-session-provider-binding (NEW capability)

## ADDED Requirements

### Requirement: SessionDB persists per-session provider override

A new SessionDB table `code_session_provider` SHALL hold optional per-`base_session_id` provider/model overrides. NULL = use global chain.

#### Scenario: Set provider override creates row

- **GIVEN** code session "vpn-tunnel" exists with no provider binding
- **WHEN** ws message `code_session_set_provider {session_id: "vpn-tunnel", provider_id: "openrouter-claude"}` arrives
- **THEN** SessionDB row inserted: `(base_session_id="vpn-tunnel", provider_id="openrouter-claude", preferred_model=NULL)`
- **AND** subsequent chat handler invocations for "vpn-tunnel" use ONLY openrouter-claude (NO chain fallback)
- **AND** ws response `code_session_provider_set {session_id, provider_id, preferred_model}` echoed
- **AND** other sessions unaffected

#### Scenario: Set preferred_model without provider keeps chain

- **GIVEN** code session "research-helper" no binding
- **WHEN** `code_session_set_model {session_id: "research-helper", model: "anthropic/claude-4.7-opus"}`
- **THEN** row inserted: `(base_session_id="research-helper", provider_id=NULL, preferred_model="anthropic/claude-4.7-opus")`
- **AND** chat handler walks the global chain BUT each request to any provider has its `model` field overridden to `"anthropic/claude-4.7-opus"`

#### Scenario: Clear override (set provider_id to null) restores global chain

- **GIVEN** code session "vpn-tunnel" has binding `provider_id="relay"`
- **WHEN** `code_session_set_provider {session_id: "vpn-tunnel", provider_id: null}`
- **THEN** SessionDB row updated to provider_id=NULL (or row deleted if preferred_model also NULL)
- **AND** chat handler resumes walking global chain

#### Scenario: Override survives backend restart

- **GIVEN** binding `(vpn-tunnel, openrouter-claude, NULL)` exists in SessionDB
- **WHEN** backend restarts
- **THEN** on next chat for vpn-tunnel, the binding is read from SessionDB
- **AND** openrouter-claude is used (NOT global chain default)

#### Scenario: Override pointing at deleted provider falls back to chain

- **GIVEN** binding `(vpn-tunnel, openrouter-claude, NULL)` exists
- **WHEN** user removes openrouter-claude via settings
- **THEN** binding row's `provider_id` set to NULL (cascade-style cleanup at registry remove time, see provider-registry spec)
- **AND** vpn-tunnel resumes global chain
- **AND** ws broadcast `code_session_provider_changed {session_id: "vpn-tunnel", reason: "provider_removed"}` so frontend updates the dropdown

### Requirement: SessionDB migration adds the table

#### Scenario: Fresh schema upgrade from v13 to v14

- **GIVEN** SessionDB at user_version=13
- **WHEN** lifespan runs migrations
- **THEN** migration `006_p5s2_code_session_provider.sql` executes:
  ```sql
  CREATE TABLE IF NOT EXISTS code_session_provider (
      base_session_id TEXT PRIMARY KEY,
      provider_id     TEXT,
      preferred_model TEXT,
      updated_at      REAL NOT NULL DEFAULT (julianday('now'))
  );
  PRAGMA user_version = 14;
  ```
- **AND** version bumps to 14
- **AND** existing tables unchanged
- **AND** no data loss

### Requirement: Resolution algorithm

`resolve_provider_for_session(base_sid)` SHALL deterministically pick which provider(s) to try:

1. Read SessionDB for `(base_sid, provider_id, preferred_model)`
2. If `provider_id` is set + provider still exists in registry + enabled → return single-element chain `[provider]` with model overridden by `preferred_model` if set
3. If `provider_id` set but provider deleted/disabled → fall through to step 4 (auto-recovery)
4. If `provider_id` NULL → return `registry.get_chain()`; if `preferred_model` set, override model on every request
5. If chain empty → raise `NoProviderConfiguredError` (handler converts to ErrorEvent)

#### Scenario: Companion sessions always use global chain

- **GIVEN** sid is "default" (companion mode, NOT code mode)
- **WHEN** chat handler resolves provider
- **THEN** SessionDB lookup is SKIPPED (companion never has per-session bindings)
- **AND** global chain is used directly
