# agent-context-assembly — P4-S21 delta

## ADDED Requirements

### Requirement: ContextBundle MUST expose L2 history as OpenAI messages array
The system SHALL surface the recent L2 conversation rows as a `history`
field on `ContextBundle` containing real OpenAI-format message dicts
(`{role, content}`), in chronological order. The chat handler
SHALL pass this list into `build_messages(history=...)` so the LLM sees
prior turns as bona-fide `user` / `assistant` / `system` messages, not
as a textual summary embedded in the system prompt.

#### Scenario: Multi-turn chat retains memory of prior user statements
- **WHEN** the user says "我喜欢喝可乐" in turn N, then "我喜欢喝什么?" in turn N+1
- **THEN** the LLM's input includes turn N as a real `user` message, and the LLM answers "可乐"

#### Scenario: Empty history when SessionDB is fresh
- **WHEN** a new session has zero prior messages
- **THEN** `bundle.history == []` and `build_messages` skips the history block

### Requirement: Memory_block MUST NOT duplicate L2 content
The system SHALL render only L3 (RRF semantic recall) into the system
memory_block. L2 (recent session) is exclusively delivered via
`bundle.history`. This avoids double-charging tokens and prevents the
LLM from treating the same content as both "instruction" (system
prompt) and "conversation" (messages array).

#### Scenario: L2 row is not visible in memory_block text
- **WHEN** `MemoryComponent.gather()` runs with non-empty L2 rows
- **THEN** none of those rows' content appears as substring in the slice's `text_content`
- **AND** the same content appears in `slice.meta["l2_history"]`

### Requirement: build_messages threads history at the canonical position
The system SHALL place history (when provided) AFTER the
frozen/skill/dynamic system blocks but BEFORE the final user message,
preserving prompt-cache friendliness (frozen prefixes stay stable;
only the tail invalidates).

#### Scenario: Message order with full bundle
- **WHEN** caller provides `frozen_system`, `skill_prelude`, `memory_block`, `history=[u, a, u]`, `user_message="hi"`
- **THEN** the resulting messages array is `[system(frozen), system(skill), system(memory_block), u, a, u, user("hi")]`
