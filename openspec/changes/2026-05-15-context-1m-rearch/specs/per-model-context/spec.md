# Spec: per-model-context (NEW capability)

## ADDED Requirements

### Requirement: Per-model context window with three-layer user/project override

ContextManager SHALL resolve each model's context window and compaction thresholds from a three-layer config chain — built-in defaults, global user override, per-project override — instead of a single hardcoded `context_window_tokens`. In code mode, the per-project layer SHALL be keyed on the project root so each project can pin its own per-model window.

#### Scenario: Built-in default used when no override exists

- **GIVEN** no `%APPDATA%/deskpet/model_overrides.toml` and no `<project_root>/.deskpet/context.toml`
- **WHEN** `resolve("deepseek-v4-pro", project_root=None)` is called
- **THEN** it SHALL return the built-in `ModelContextInfo` for `deepseek-v4-pro` (context_window=1_000_000, compact_at_pct=0.75)
- **AND** a log line `model_context_resolved model=deepseek-v4-pro window=1000000 source=builtin` SHALL be emitted

#### Scenario: Unknown model falls back to conservative default

- **GIVEN** a model name absent from the built-in table
- **WHEN** `resolve("some-local-7b", project_root=None)` is called
- **THEN** it SHALL return the `_default` entry (conservative window, e.g. 32_000)

#### Scenario: Global override beats built-in via deep merge

- **GIVEN** `%APPDATA%/deskpet/model_overrides.toml` sets `[models."deepseek-v4-pro"] context_window = 800000`
- **WHEN** `resolve("deepseek-v4-pro", project_root=None)` is called
- **THEN** the resolved `context_window` SHALL be 800000
- **AND** fields NOT present in the override SHALL keep built-in values (deep merge, not replace)
- **AND** the resolution `source` SHALL be logged as `global`

#### Scenario: Project override beats global in code mode

- **GIVEN** global override sets `deepseek-v4-pro` window to 800000
- **AND** `<project_root>/.deskpet/context.toml` sets `[models."deepseek-v4-pro"] context_window = 1000000`
- **WHEN** a code-mode session whose `project_root` is that directory resolves the model
- **THEN** the resolved `context_window` SHALL be 1000000
- **AND** the resolution `source` SHALL be logged as `project`

#### Scenario: Non-code-mode ignores the project layer

- **GIVEN** a normal (non-code) chat session
- **WHEN** the model is resolved
- **THEN** only the built-in and global layers SHALL apply (no project root, project layer skipped)

### Requirement: ContextManager thresholds derive from resolved model info

ContextManager truncation, compaction, and budget thresholds SHALL be computed as ratios of the resolved model context window, not absolute constants, so switching models requires no manual config edits.

#### Scenario: Compaction trigger scales with model window

- **GIVEN** the resolved model is `deepseek-v4-pro` (window=1_000_000, compact_at_pct=0.75)
- **WHEN** ContextConfig computes `compact_at_tokens`
- **THEN** it SHALL be 750_000
- **AND** switching the session to `claude-sonnet-4-5` (window=200_000, compact_at_pct=0.83) SHALL recompute `compact_at_tokens` to 166_000 with no config file edit

#### Scenario: v2_enabled=false falls back to legacy absolute thresholds

- **GIVEN** `config.toml [context.manager].v2_enabled = false`
- **WHEN** ContextManager initializes
- **THEN** it SHALL use the legacy absolute-value ContextConfig (Strangler-Fig rollback path) and ignore the per-model map
