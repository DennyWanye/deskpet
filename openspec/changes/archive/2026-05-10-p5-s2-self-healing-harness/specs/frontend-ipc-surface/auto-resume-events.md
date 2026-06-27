# Spec: frontend-ipc-surface / auto-resume-events (MODIFIED)

## ADDED Requirements

### Requirement: Frontend handles three auto-resume ws events

The frontend WebSocket dispatcher SHALL recognize and route three new outbound events related to AutoResume.

#### Scenario: auto_resume_started shows non-blocking banner

- **GIVEN** the backend orchestrator decides to auto-resume session "s1"
- **WHEN** ws emits `{type: "auto_resume_started", payload: {session_id: "s1", attempt: 1, hint_preview: "..."}}`
- **THEN** the frontend SHALL render a non-blocking toast/banner via `AutoResumeBanner.tsx` showing "🔄 agent 自愈中... (尝试 1/2)"
- **AND** the banner SHALL NOT block the input box or other UI

#### Scenario: auto_resume_succeeded clears the banner

- **GIVEN** an `AutoResumeBanner` is currently showing for session "s1"
- **WHEN** ws emits `{type: "auto_resume_succeeded", payload: {session_id: "s1"}}` OR a regular `chat_v2_final` arrives
- **THEN** the banner SHALL be dismissed within 500 ms
- **AND** the session UI SHALL display the new final response normally

#### Scenario: auto_resume_exhausted escalates to existing popup

- **GIVEN** the orchestrator has hit max_attempts
- **WHEN** ws emits `{type: "auto_resume_exhausted", payload: {session_id: "s1", final_error: "...", attempts: 2}}`
- **THEN** the banner SHALL be dismissed
- **AND** the existing P5-S1 supervisor_alert popup SHALL fire with severity=red, user_message containing the cumulative attempt count

#### Scenario: tool_circuit_opened shows a tile-level badge

- **GIVEN** a circuit breaker opens for some tool on session "s1"
- **WHEN** ws emits `{type: "tool_circuit_opened", payload: {session_id: "s1", tool_name: "write_file", cooldown_seconds: 60}}`
- **THEN** the session tile in the multi-project dashboard SHALL show a small red dot badge labeled "write_file 熔断"
- **AND** clicking the badge SHALL open a tooltip explaining "该工具连失败 3 次，60 秒后允许重试"
- **AND** the badge SHALL auto-clear when the breaker transitions back to CLOSED (signaled via a future `tool_circuit_closed` event OR by passing time)

### Requirement: Settings panel exposes auto-resume toggle

A new toggle "自动自愈失败任务" SHALL appear in the Settings panel, controlling the backend's `auto_resume_enabled` config knob.

#### Scenario: Toggle persists to config

- **GIVEN** user opens Settings and toggles "自动自愈失败任务" off
- **WHEN** user clicks save
- **THEN** the frontend SHALL send `{type: "settings_update", payload: {supervisor: {auto_resume_enabled: false}}}` over ws
- **AND** the backend SHALL persist to `config.toml [supervisor]` section
- **AND** the next failure SHALL fall through to the existing P5-S1 popup behavior
