# Spec: tool-registry / circuit-breaker

## ADDED Requirements

### Requirement: Per-(session, tool) three-state circuit breaker

The tool registry SHALL maintain an independent circuit breaker for each `(session_id, tool_name)` pair, with three states `CLOSED` / `OPEN` / `HALF_OPEN`, blocking calls when OPEN to prevent reflexive retry storms.

#### Scenario: 3 consecutive failures open the breaker

- **GIVEN** breaker for `(sid="A", tool="write_file")` is in `CLOSED` state
- **WHEN** the same tool fails 3 times in a row (regardless of args)
- **THEN** breaker transitions to `OPEN`
- **AND** the next call to `can_call("write_file")` returns `False`

#### Scenario: A success resets the failure count

- **GIVEN** breaker has 2 consecutive failures recorded
- **WHEN** the next call succeeds
- **THEN** failure_count resets to 0 AND state stays `CLOSED`

#### Scenario: After cooldown OPEN auto-promotes to HALF_OPEN

- **GIVEN** breaker is `OPEN` and `cooldown_seconds=60` has elapsed since last failure
- **WHEN** `can_call` is queried
- **THEN** state transitions to `HALF_OPEN` AND `can_call` returns `True` exactly ONCE (probe)
- **AND** subsequent `can_call` returns `False` until probe resolves

#### Scenario: HALF-OPEN probe success closes the breaker

- **GIVEN** breaker is `HALF_OPEN` and probe call is in flight
- **WHEN** probe completes successfully
- **THEN** state transitions to `CLOSED` AND failure_count resets to 0

#### Scenario: HALF-OPEN probe failure re-opens immediately

- **GIVEN** breaker is `HALF_OPEN` and probe is in flight
- **WHEN** probe fails
- **THEN** state transitions back to `OPEN` AND cooldown timer resets (NO need for 3 more failures)

#### Scenario: Breakers are isolated across tools

- **GIVEN** breaker for `(sid="A", "write_file")` is `OPEN`
- **WHEN** `can_call("read_file")` is queried for the same sid
- **THEN** it returns `True` (read_file's breaker is unaffected)

#### Scenario: Breakers are isolated across sessions

- **GIVEN** breaker for `(sid="A", "write_file")` is `OPEN`
- **WHEN** `can_call("write_file")` is queried for `sid="B"`
- **THEN** it returns `True` (B's breaker is independent)

### Requirement: Dispatch enforces circuit breaker

When a circuit breaker is `OPEN`, the tool dispatch layer SHALL refuse to invoke the tool's handler and instead return a synthetic error result containing remediation guidance.

#### Scenario: OPEN breaker blocks dispatch

- **GIVEN** breaker for `(sid, "write_file")` is `OPEN`
- **WHEN** the agent invokes `dispatch("write_file", args, sid)`
- **THEN** dispatch SHALL NOT call the underlying handler
- **AND** dispatch SHALL return:
  ```json
  {
    "ok": false,
    "error": "circuit_open",
    "hint": "write_file 连续失败 3 次已熔断 (剩余 N 秒)。检查参数或换工具。",
    "available_alternatives": ["edit_file", "desktop_create_file"]
  }
  ```

#### Scenario: Dispatch records every outcome

- **GIVEN** dispatch executes a tool that returns `{"ok": true, ...}`
- **WHEN** dispatch completes
- **THEN** breaker SHALL record success (resetting failure_count)
- **GIVEN** dispatch executes a tool that returns `{"ok": false, ...}` or raises
- **THEN** breaker SHALL record failure (incrementing failure_count)
