# Spec: provider-registry (NEW capability)

## ADDED Requirements

### Requirement: LLMProviderRegistry centralizes all provider operations

Backend SHALL expose a single `LLMProviderRegistry` instance via `service_context["llm_provider_registry"]` that owns the in-memory list of providers and persists changes to `config.toml`. All chat-handler / agent-loop / supervisor code reads providers exclusively through it.

#### Scenario: Add provider persists to toml + appears in chain

- **GIVEN** registry currently has 1 enabled provider `chinzy-deepseek` (priority 1)
- **WHEN** `await registry.add_provider({"id": "openrouter-claude", "name": "Claude 4.7 via OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "anthropic/claude-4.7-sonnet", "api_key": "sk-or-...", "priority": 2, "enabled": True})` is called
- **THEN** `registry.list_providers()` returns 2 entries
- **AND** `registry.get_chain()` returns `[chinzy-deepseek, openrouter-claude]` (priority order)
- **AND** the api_key is written to OS keychain under `deskpet.provider.openrouter-claude`
- **AND** `config.toml` `[[llm.providers]]` has 2 entries with `api_key_ref = "deskpet.provider.openrouter-claude"` (NOT plaintext)
- **AND** a `providers_changed` ws event is broadcast to all control connections

#### Scenario: Remove provider drops from chain + cleans keychain

- **GIVEN** registry has 3 providers and `openrouter-claude` exists
- **WHEN** `await registry.remove_provider("openrouter-claude")`
- **THEN** `list_providers()` returns 2 entries (no openrouter-claude)
- **AND** keychain entry `deskpet.provider.openrouter-claude` is deleted
- **AND** any code_session_provider rows pointing at openrouter-claude have `provider_id = NULL` (fall back to global chain)
- **AND** `providers_changed` ws event broadcast

#### Scenario: Reorder by priority changes chain order

- **GIVEN** providers have priorities `{chinzy: 1, ollama: 2, openrouter: 3}`
- **WHEN** `await registry.reorder(["openrouter", "chinzy", "ollama"])`
- **THEN** priorities updated to `{openrouter: 1, chinzy: 2, ollama: 3}`
- **AND** `get_chain()` returns `[openrouter, chinzy, ollama]`
- **AND** persisted to toml + ws broadcast

#### Scenario: Toggle disable removes from chain but keeps config

- **GIVEN** registry has `chinzy (enabled=True), ollama (enabled=True)`
- **WHEN** `await registry.set_enabled("ollama", False)`
- **THEN** `get_chain()` returns `[chinzy]` only
- **AND** `list_providers()` still returns 2 entries (ollama with enabled=False)
- **AND** persisted + ws broadcast

#### Scenario: Empty chain raises actionable error

- **GIVEN** registry has 0 enabled providers (or 0 providers total)
- **WHEN** any caller asks `get_chain()` and tries to chat
- **THEN** chat handler emits `ErrorEvent(reason="no_provider_configured", detail="未配置任何 LLM provider。请打开设置 → LLM Providers → 添加")`
- **AND** does NOT crash backend / does NOT pop generic exception

#### Scenario: API key never returned in plaintext via list_providers

- **GIVEN** registry has provider with stored api_key
- **WHEN** any caller (including ws handler) gets `registry.list_providers()`
- **THEN** each entry's `api_key` field is the literal string `"********"` (8 stars) — NOT the actual key
- **AND** entry has `api_key_ref` (keychain reference) for internal use only

### Requirement: Conservative migration from legacy [llm.local] schema

Backend lifespan SHALL run `_migrate_legacy_provider_config()` BEFORE constructing `LLMProviderRegistry`. The migration converts the legacy `[llm.local]` single-provider schema into the new `[[llm.providers]]` list, preserving keychain api_key references.

#### Scenario: Fresh install with no legacy config

- **GIVEN** `config.toml` has no `[llm.local]` and no `[[llm.providers]]`
- **WHEN** lifespan starts
- **THEN** migration is no-op
- **AND** registry starts empty
- **AND** lifespan continues (no crash)
- **AND** UI shows "请添加你的第一个 LLM provider" empty state

#### Scenario: Legacy [llm.local] migrated to [[llm.providers]]

- **GIVEN** `config.toml` has `[llm.local] base_url=... model=... api_key="from-keychain"` and NO `[[llm.providers]]`
- **WHEN** lifespan starts
- **THEN** `[[llm.providers]]` is appended to toml with one entry:
  - `id = "legacy-default"`
  - `name = "<model> (auto-migrated)"`
  - `base_url`, `model` from legacy
  - `api_key_ref = "deskpet.cloud_api_key"` (existing keychain entry, NOT moved)
  - `priority = 1`
  - `enabled = true`
- **AND** legacy `[llm.local]` section gets a comment line `# auto-migrated to [[llm.providers]] on 2026-05-11; safe to remove`
- **AND** the file is written ATOMICALLY (write to .tmp, then rename)
- **AND** logger.info("migrated_legacy_llm_local_to_providers id=legacy-default")

#### Scenario: Migration is idempotent

- **GIVEN** `[[llm.providers]]` already exists (any number of entries)
- **WHEN** lifespan starts (or restarts)
- **THEN** migration is no-op (does NOT re-create legacy-default duplicate)
- **AND** existing entries unchanged

#### Scenario: Migration handles broken keychain

- **GIVEN** legacy `[llm.local]` exists but `_resolve_cloud_api_key()` returns None (keychain empty)
- **WHEN** migration runs
- **THEN** the entry is still created with `api_key_ref = "deskpet.cloud_api_key"`
- **AND** a comment is added: `# api_key from keychain not found, please re-enter via settings`
- **AND** UI shows a yellow warning banner on first load: "Provider 'legacy-default' 的 api_key 找不到，请到设置重新输入"

### Requirement: get_chain returns ordered enabled providers

#### Scenario: get_chain filters disabled

- **GIVEN** registry with `[A(enabled, p=1), B(disabled, p=2), C(enabled, p=3)]`
- **WHEN** `get_chain()` called
- **THEN** returns `[A, C]` (B excluded because disabled)

#### Scenario: get_chain stable order on equal priority

- **GIVEN** registry with `[X(p=2), Y(p=2), Z(p=2)]` all enabled, added in that insertion order
- **WHEN** `get_chain()` called
- **THEN** returns `[X, Y, Z]` (insertion order tie-break, stable across calls)
