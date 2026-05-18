## ADDED Requirements

### Requirement: Inline reasoning delimiters are stripped from content

The provider SHALL remove inline chain-of-thought delimiter blocks from the `content` it returns to downstream consumers, for both streaming and non-streaming responses. This applies to deepseek-style `<｜begin▁of▁thinking｜>…<｜end▁of▁thinking｜>` blocks and, defensively, to `<think>…</think>` blocks. Structured `reasoning_content` / `reasoning` fields SHALL NOT be altered by this sanitization.

#### Scenario: Closed deepseek thinking block is removed

- **WHEN** a response `content` contains `prefix<｜begin▁of▁thinking｜>internal reasoning<｜end▁of▁thinking｜>answer`
- **THEN** the returned `content` is `prefixanswer` (the delimiter block and its inner text removed, surrounding text preserved)

#### Scenario: Unterminated leading thinking block is removed

- **WHEN** a response `content` begins with `<｜end▁of▁thinking｜>API 测试通过…` with no matching `<｜begin▁of▁thinking｜>` (the real incident shape)
- **THEN** the leading orphan delimiter and the CoT text up to the first real answer/structured marker is removed and no `<｜` delimiter token remains in `content`

#### Scenario: Defensive think-tag stripping

- **WHEN** a response `content` contains `<think>plan</think>final`
- **THEN** the returned `content` is `final`

#### Scenario: Clean content is unchanged

- **WHEN** a response `content` contains no thinking delimiters and no DSML markup
- **THEN** the returned `content` is byte-identical to the original

### Requirement: Inline DSML tool calls are extracted to structured tool_calls

When `content` contains an inline `<｜｜DSML｜｜tool_calls>` block using the deepseek native textual tool-call protocol (`<｜｜DSML｜｜invoke name="…">`, `<｜｜DSML｜｜parameter name="…" …>` … payload), the provider SHALL parse it into one or more structured tool calls (each with a `name` and JSON-decoded `arguments`) and SHALL remove the entire DSML block from the returned `content`. The extracted tool calls SHALL be merged into the response's `tool_calls` and the `stop_reason` SHALL reflect tool use when at least one tool call was extracted and none were already present.

#### Scenario: DSML todo_write block becomes a structured tool call

- **WHEN** `content` contains `<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="todo_write"><｜｜DSML｜｜parameter name="items" string="false">[{"content":"x","status":"pending"}]`
- **THEN** the response `tool_calls` contains an entry with `name == "todo_write"` and `arguments == {"items": [{"content": "x", "status": "pending"}]}`
- **AND** the returned `content` contains no `<｜｜DSML｜｜` markup

#### Scenario: Structured tool_calls take precedence and are not duplicated

- **WHEN** a response already returns a structured `tool_calls` array AND no `<｜｜DSML｜｜tool_calls>` block is present in `content`
- **THEN** the structured `tool_calls` are returned unchanged and no DSML parsing alters them

#### Scenario: Malformed DSML payload does not crash and does not leak

- **WHEN** `content` contains a `<｜｜DSML｜｜tool_calls>` block whose parameter payload is not valid JSON
- **THEN** the provider does not raise, the DSML markup is still removed from `content`, and the failure is logged at WARNING level

### Requirement: Sanitization is reversible via configuration

The sanitization behavior SHALL be controlled by a Strangler-Fig flag `[llm] sanitize_inline_cot_dsml` that defaults to enabled. When disabled, the provider SHALL return the legacy raw `content` and structured-field behavior with no sanitization or DSML extraction.

#### Scenario: Flag disabled restores legacy passthrough

- **WHEN** `[llm] sanitize_inline_cot_dsml = false` and a response contains inline CoT/DSML markup
- **THEN** the returned `content` is the raw unmodified provider content (legacy behavior) and no DSML tool-call extraction occurs

#### Scenario: Flag enabled by default

- **WHEN** the flag is absent from configuration
- **THEN** sanitization and DSML extraction are active (default-on)

### Requirement: Existing structured-field contracts are preserved

Sanitization SHALL NOT regress existing provider behavior: structured `reasoning_content`/`reasoning` extraction, the `reasoning_content`-400 multi-turn round-trip handling, structured OpenAI `tool_calls` parsing, usage/billing accounting, and prompt-cache hit logging SHALL behave exactly as before for responses that do not contain inline CoT/DSML markup.

#### Scenario: Structured reasoning response is untouched

- **WHEN** a thinking-mode model returns its reasoning in the structured `reasoning_content` field and a clean `content`
- **THEN** `reasoning_content` and `content` are returned exactly as in the pre-change behavior and the `reasoning_content`-400 round-trip logic still applies
