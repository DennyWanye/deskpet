# Spec: long-run-context (NEW capability)

## ADDED Requirements

### Requirement: File-read deduplication supersedes stale reads

When the same file path is read more than once in a session, ContextManager SHALL replace the older tool_result bodies in history with a short superseded marker, keeping only the most recent read, without altering the message array length (tool_call/tool_result pairing stays intact).

#### Scenario: Re-reading a file supersedes the earlier copy

- **GIVEN** `read_file("App.jsx")` was recorded at iteration 5 (6KB body in history)
- **WHEN** `read_file("App.jsx")` is recorded again at iteration 12
- **THEN** the iteration-5 tool_result content SHALL be replaced with `<file App.jsx was re-read at iteration 12; superseded — see the later read>`
- **AND** the iteration-12 tool_result SHALL retain its full (post-truncation) body
- **AND** the total message count SHALL be unchanged

#### Scenario: Path normalization handles Windows casing

- **GIVEN** `read_file("G:\\proj\\App.jsx")` then `read_file("g:/proj/App.jsx")`
- **WHEN** dedup runs
- **THEN** both SHALL be treated as the same path (resolved + case-normalized) and the first SHALL be superseded

#### Scenario: Write/exec tools are not deduplicated

- **GIVEN** `run_shell` and `edit_file` results in history
- **WHEN** dedup runs
- **THEN** only read-class tools (read_file / mcp_filesystem_read_text_file / configured whitelist) SHALL be deduplicated; write/exec results SHALL be untouched

### Requirement: tool_result bodies are SHA-256 content-addressed

tool_result bodies at or above 1024 chars SHALL be stored on disk keyed by content SHA-256 (under `%APPDATA%/deskpet/tool_results/`, never inside the project tree) and referenced in history by a compact ref marker, so identical content produced multiple times occupies storage once and survives a backend restart.

#### Scenario: Identical content reused across reads

- **GIVEN** a 6KB file body is produced by `read_file` at iteration 3
- **WHEN** the identical body is produced again at iteration 20
- **THEN** both history entries SHALL reference the same `sha:` id
- **AND** only one file SHALL exist under `%APPDATA%/deskpet/tool_results/`

#### Scenario: Reference survives a backend restart

- **GIVEN** a sha-addressed tool_result was written before a backend restart
- **WHEN** the model calls `fetch_tool_result(ref="sha:xxxx", start, end)` after restart
- **THEN** the byte range SHALL be served from disk (legacy in-memory LRU refs would have been lost)

#### Scenario: Disk write failure falls back inline

- **GIVEN** the tool_results directory is not writable
- **WHEN** a large tool_result is recorded
- **THEN** the body SHALL be kept inline (truncated per existing B1 rules) and SHALL NOT produce a dangling sha reference

### Requirement: Checkpoint-Restart Cycle replaces in-place summarization for long runs

When estimated context tokens reach the resolved model's `compact_at_tokens`, ContextManager SHALL archive the current cycle and start a fresh homogeneous cycle seeded with structured state plus a model-authored carry-forward briefing, instead of summarizing the middle range in place.

#### Scenario: Reaching the per-model watermark triggers a cycle restart

- **GIVEN** the resolved model gives `compact_at_tokens = 750_000`
- **AND** estimated context tokens reach 750_000
- **WHEN** ContextManager runs its pre-call check
- **THEN** the current cycle SHALL be archived to `SessionDB.context_cycles(sid, cycle_n, jsonl, ts)`
- **AND** a new cycle SHALL be started containing: the original system message, tools schema, structured state (TodoList / PlanState / WorkingSet), and a model-authored `<carry_forward>` briefing capped at ~1% of the window
- **AND** previously stored sha-addressed tool_result refs SHALL remain fetchable

#### Scenario: Archived cycles remain queryable

- **GIVEN** two cycles have been archived for session "s1"
- **WHEN** `recall_archived_cycle("s1", query)` is called
- **THEN** it SHALL return BM25-ranked snippets from the archived JSONL via SessionDB FTS5

#### Scenario: v2_enabled=false keeps legacy in-place summarize

- **GIVEN** `config.toml [context.manager].v2_enabled = false`
- **WHEN** the compaction threshold is reached
- **THEN** the legacy B2 in-place summarize path SHALL run and Checkpoint-Restart SHALL NOT activate (rollback safety)
