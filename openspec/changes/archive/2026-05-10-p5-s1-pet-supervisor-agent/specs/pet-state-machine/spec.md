## ADDED Requirements

### Requirement: Per-session severity score

The frontend SHALL compute a numeric `severity_score` for each known Code-mode session as the sum of five components: `base_status_weight` (idle=0, running=10, permission=25, error=60), `age_penalty` (`min(30, log2(max(1, age_seconds/60)) * 6)`), `repeat_penalty` (`min(40, max_signature_count * 10)`), `supervisor_severity_boost` (green=0, yellow=20, red=50), and `iteration_pressure` (`(current_iteration / max_iterations) * 10`). Scores SHALL be recomputed whenever any input field changes (via zustand selector or memoized derivation).

#### Scenario: Idle session has low score

- **GIVEN** a session with `status=idle`, no recent activity, no supervisor alert
- **THEN** `severity_score = 0`

#### Scenario: Repeated tool calls bump repeat penalty

- **GIVEN** a session whose tool_signature_window shows `bash_run:<hash>=4`
- **THEN** `repeat_penalty = min(40, 4*10) = 40`
- **AND** total severity_score is at least 40 plus other components

#### Scenario: Score recomputes on supervisor alert

- **GIVEN** a session currently scoring 30
- **WHEN** a `supervisor_alert` arrives with `severity=yellow`
- **THEN** `supervisor_severity_boost` becomes 20
- **AND** the session's score is updated to 50 (= 30 + 20)

### Requirement: Pet visual state machine

The pet UI SHALL implement a 5-state machine with states `idle`, `working`, `worried`, `alert`, `intervening`. State selection SHALL be derived from the maximum `severity_score` across all known Code-mode sessions (the "pet focus session"). Mapping rules:
- score < 30 → `idle` (or `working` when at least one session has `status=running` AND score < 30)
- 30 ≤ score < 60 → `working`
- 60 ≤ score < 100 → `worried`
- score ≥ 100 → `alert`
- transient state `intervening` enters only when supervisor action is `nudge` for the focused session and lasts ~3 seconds before returning to the score-derived state

#### Scenario: Single quiet session puts pet in idle

- **GIVEN** only one session exists with `status=idle`, score=0
- **THEN** pet state is `idle`

#### Scenario: Multiple sessions, max score wins

- **GIVEN** session A scores 25, session B scores 75
- **THEN** pet state is `worried` (driven by B)
- **AND** the focus session id is B

#### Scenario: Intervening overlay on nudge

- **GIVEN** pet state is `worried` (score=70)
- **WHEN** a `supervisor_alert` arrives with `action=nudge` for the focus session
- **THEN** pet enters `intervening` state for ~3 seconds
- **AND** then returns to score-derived state (recomputed at that moment)

### Requirement: Hysteresis and minimum dwell time

State transitions SHALL apply hysteresis: entering `worried` requires score ≥ 60, but exiting `worried` requires score < 50 (10-point band). Same +10 / −10 hysteresis SHALL apply at the worried/alert boundary (enter alert at 100, exit alert at 90). Additionally, a state change SHALL NOT happen until the current state has persisted for at least 10 seconds. The `intervening` overlay does NOT count as a state change for dwell purposes.

#### Scenario: Hysteresis prevents flapping

- **GIVEN** pet state is `worried` with score=58
- **WHEN** the score briefly drops to 55 then back to 65
- **THEN** the state remains `worried` throughout (because 55 ≥ 50, no exit)

#### Scenario: Minimum dwell holds state

- **GIVEN** pet state just transitioned to `worried` 3 seconds ago
- **WHEN** score drops to 25
- **THEN** state remains `worried` for at least 7 more seconds before potentially returning to `idle`

### Requirement: Live2D motion mapping

The pet state machine SHALL drive Live2D motion using only the existing Hiyori model resources (`Idle: m01..m10`, `TapBody: m04`). For each state, the system SHALL pick from a configured `motion_pool` and SHALL respect the configured `switch_period_seconds`. Initial mapping (subject to S3 calibration spike):
- `idle`: pool `{m01, m03, m05, m06, m09, m10}`, switch period 30±10s
- `working`: pool `{m02, m07}`, switch period 15±5s
- `worried`: pool `{m05, m08}`, switch period 45±15s, blink rate 0.5 Hz, head tilt -5°
- `alert`: pool `{m08}`, switch period 60s, blink rate 0.6 Hz, head tilt -8°, on-entry TapBody once
- `intervening`: pool `{m02}`, on-entry TapBody once, blink rate 0.3 Hz, head tilt +3°

The state machine SHALL NOT attempt to call `setExpression` (Hiyori has no expressions) and SHALL silently no-op if asked.

#### Scenario: Worried state slows motion cadence

- **WHEN** pet enters `worried` state
- **THEN** the next motion picked is from `{m05, m08}`
- **AND** the next motion switch is scheduled 30..60 seconds later

#### Scenario: Alert state triggers TapBody on entry

- **WHEN** pet transitions from `worried` to `alert`
- **THEN** `playMotion("TapBody")` is invoked once immediately
- **AND** then the system schedules the next Idle motion switch 60 seconds later

### Requirement: Supervisor bubble UI

The pet UI SHALL render a bubble component (`PetSupervisorBubble`) overlay above the Hiyori model when pet state is `worried`, `alert`, or `intervening`. The bubble SHALL display: a colored background (yellow / red / blue respectively), the supervisor's `user_message` (or a default message if none), up to 2 `suggested_buttons`, and the focused session's id (truncated, e.g. "code-a1b2c3...."). Clicking the bubble (anywhere outside buttons) SHALL emit a `pet_focus_session_clicked` event that opens the code-panel window and sets that session as the active session in the SessionGridView. The bubble SHALL fade in over 300ms and fade out over 400ms.

#### Scenario: Bubble appears on worried state with message

- **GIVEN** pet enters `worried` state due to supervisor_alert with `user_message="脑子转不过弯了，我让它换个 pip 源试试"`
- **THEN** a yellow bubble appears above Hiyori
- **AND** displays the message text
- **AND** displays buttons "让它继续" and "我自己看看"

#### Scenario: Click on bubble jumps to session

- **GIVEN** the bubble is visible for focused sid `code-a1b2c3d4`
- **WHEN** the user clicks the bubble background (not a button)
- **THEN** the code-panel window is opened (via existing `open_code_panel` IPC)
- **AND** `code-a1b2c3d4` is set as the active session in SessionGridView

#### Scenario: Idle state hides bubble

- **WHEN** pet returns to `idle` state
- **THEN** the bubble fades out over 400ms then unmounts

### Requirement: Bubble button click protocol

When the user clicks a button inside the supervisor bubble, the frontend SHALL send a `supervisor_user_choice` ws message with payload `{session_id, button_index, button_text, alert_id}`. The frontend SHALL immediately hide the bubble (optimistic UI). The backend handler is out of scope for the state-machine spec (handled in pet-supervisor capability). Subsequent supervisor_alert events for the same sid SHALL re-render a fresh bubble.

#### Scenario: Click "让它继续" sends choice

- **WHEN** the user clicks the first button "让它继续"
- **THEN** ws sends `{type: "supervisor_user_choice", payload: {session_id, button_index: 0, button_text: "让它继续", alert_id}}`
- **AND** the bubble hides immediately

### Requirement: SessionGridView tile severity indicator

The SessionGridView component SHALL render each tile with a border color reflecting that session's `severity_score`: green (<30), neutral/blue (30..60), yellow (60..100), red (≥100). Tiles SHALL show a small severity icon next to the project name. The most-dangerous session's tile SHALL pulse subtly when score ≥ 100 (1 Hz, 30% opacity wave).

#### Scenario: Tile color reflects current score

- **GIVEN** a tile's session has score=72
- **THEN** the tile's border is yellow
- **AND** the icon shows a "warning" affordance

#### Scenario: Top tile pulses at red

- **GIVEN** the most-dangerous tile has score=110
- **THEN** that tile's border pulses (1 Hz wave between 100% and 70% opacity)

### Requirement: Pet state observability

The state machine SHALL expose its current state and focus session via the zustand store (or equivalent observable). A debug-only overlay (visible only when `localStorage.deskpet_debug == "1"`) SHALL display: current state, current focused sid, current score, and the per-session score breakdown.

#### Scenario: Debug overlay shows breakdown

- **GIVEN** `localStorage.deskpet_debug = "1"`
- **WHEN** a session has score=75 with breakdown `{base:10, age:30, repeat:0, sup:20, iter:15}`
- **THEN** the debug overlay shows that breakdown
- **AND** updates in real time as scores change

#### Scenario: Debug overlay invisible by default

- **GIVEN** `localStorage.deskpet_debug` is unset
- **THEN** the overlay is not rendered
