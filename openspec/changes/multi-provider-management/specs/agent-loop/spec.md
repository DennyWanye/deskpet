# Spec: agent-loop (MODIFIED)

## MODIFIED Requirements

### Requirement: Chat handler walks the provider chain

`AgentLoop.run()` SHALL accept an optional `provider_chain: list[OpenAICompatibleProvider]` parameter. When set, each LLM call inside the loop tries providers in order, falling back on transient errors only.

#### Scenario: First provider succeeds — others not tried

- **GIVEN** chain `[A, B, C]` all enabled
- **WHEN** AgentLoop call to provider A returns valid `ChatResponse`
- **THEN** loop yields events from A's response
- **AND** B and C are NEVER called for this iteration
- **AND** ws optionally emits `provider_used {session_id, provider_id: "A"}` (front-end displays small "via A" badge)

#### Scenario: Transient error → fallback to next provider

- **GIVEN** chain `[A, B, C]`
- **WHEN** A raises `LLMProviderError` containing "ConnectTimeout" (or any transient httpx exception)
- **THEN** loop logs `provider_chain_fallback from=A to=B reason=ConnectTimeout`
- **AND** retries the SAME prompt against B
- **AND** if B succeeds → loop continues from B's response (not retried against A again)
- **AND** ws emits `provider_chain_fallback {session_id, from: "A", to: "B", reason: "ConnectTimeout"}` for diagnostic banner

#### Scenario: Permanent error from a provider does NOT fallback

- **GIVEN** chain `[A, B]`
- **WHEN** A returns `tool_call.args_parse_error` (or any P5-S2 PermanentToolError signal)
- **THEN** loop does NOT fall back to B
- **AND** behaves identically to single-provider case (Phase 2 short-circuit, structured tool_result back to LLM)

#### Scenario: All providers fail → ErrorEvent

- **GIVEN** chain `[A, B, C]` all enabled
- **WHEN** A, B, C all raise transient errors in sequence
- **THEN** loop emits `ErrorEvent(reason="all_providers_failed", detail="tried 3, last_error: <X>")`
- **AND** AutoResume orchestrator (P5-S2 Phase 4) receives the ErrorEvent and decides nudge / ask_user / exhausted normally

#### Scenario: Empty chain raises actionable error before first call

- **GIVEN** chain is `[]` (no providers configured or all disabled)
- **WHEN** AgentLoop iteration starts
- **THEN** emit `ErrorEvent(reason="no_provider_configured", detail="未配置任何 LLM provider。请打开设置 → LLM Providers → 添加")`
- **AND** loop returns immediately (no LLM call attempted)

### Requirement: Per-session provider_id resolves to single-element chain

#### Scenario: Code session pinned to single provider

- **GIVEN** code_session_provider for sid "vpn-tunnel" has `provider_id="the relay"`
- **WHEN** chat handler resolves chain
- **THEN** chain = `[the relay]` only — even if global chain has 3 providers
- **AND** transient error in the relay yields `ErrorEvent` immediately (no fallback to global chain by default)
- **AND** UI for that card shows "已固定到 the relay（无 fallback）" hint

#### Scenario: preferred_model overrides per-call model

- **GIVEN** code_session has `preferred_model="claude-4.7-opus"` (no provider_id)
- **AND** global chain has `[the relay(model=deepseek-v4-pro), openrouter(model=claude-4.7-sonnet)]`
- **WHEN** AgentLoop calls each provider
- **THEN** the request sent to the relay has `model="claude-4.7-opus"` (NOT deepseek-v4-pro)
- **AND** the request sent to openrouter has `model="claude-4.7-opus"` (NOT claude-4.7-sonnet)
- **AND** the provider's own configured model is overridden ONLY for this session's requests

### Requirement: Backwards compat with single-provider callers

#### Scenario: Existing tests passing chat_with_fallback work

- **GIVEN** legacy code path passes a single `OpenAICompatibleProvider` (not a chain)
- **WHEN** AgentLoop is invoked the old way
- **THEN** internally wraps it as `chain=[single]` and behaves identically
- **AND** all P5-S2 tests (errors / circuit / auto_resume) keep passing
