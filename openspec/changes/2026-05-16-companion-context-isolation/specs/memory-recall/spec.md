# Spec: memory-recall (MODIFIED capability)

## MODIFIED Requirements

### Requirement: Recall applies session-affinity weighting

The memory retriever SHALL weight recalled memories by a session-affinity factor in its RRF fusion, so a companion (`default`) session's request is not dominated by strong project/task memories produced in unrelated code sessions, while still preserving cross-session person/preference memory ("the pet remembers you").

#### Scenario: Companion request not hijacked by unrelated code-session project memory

- **GIVEN** a high-salience memory "develop a C VPN tool" produced in code session `code-tyfbt62t`
- **AND** the current request is in session `default` (companion) and is unrelated ("generate a poster image")
- **AND** `config.toml [companion].memory_cross_session_decay = 0.15`
- **WHEN** the retriever fuses RRF scores
- **THEN** the VPN project memory's final score SHALL be multiplied by ≤ 0.15 (project/task-class, cross-session, companion current)
- **AND** it SHALL NOT rank above memories relevant to the current request

#### Scenario: Cross-session person/preference memory still recalled

- **GIVEN** a memory "user prefers concise Chinese answers" from a code session
- **AND** the current session is `default` (companion)
- **WHEN** the retriever fuses scores
- **THEN** that memory's affinity factor SHALL be ~0.8 (lightly reduced, NOT decayed to 0.15) so the pet still remembers the user across sessions

#### Scenario: Same-session memory unaffected

- **GIVEN** a memory produced in session `default`
- **AND** the current request is also in session `default`
- **WHEN** affinity is computed
- **THEN** the factor SHALL be 1.0 (no change to same-session recall)

#### Scenario: decay=1.0 restores legacy behavior

- **GIVEN** `config.toml [companion].memory_cross_session_decay = 1.0`
- **WHEN** the retriever fuses scores
- **THEN** every affinity factor SHALL be 1.0 (Strangler-Fig rollback — recall behaves exactly as before this change)
