# Spec: capability-gate (NEW capability)

## ADDED Requirements

### Requirement: Unfulfillable requests are gracefully refused, not drifted

Before a request enters the agent loop, a capability gate SHALL detect requests that require a capability deskpet has no tool for (image/video/audio/3D generation, etc.) and return a graceful refusal with an alternative suggestion, instead of letting the LLM drift into unrelated work.

#### Scenario: Image-generation request with no image tool is refused

- **GIVEN** the ToolRegistry exposes no image-generation tool
- **AND** the user (companion session) says "你能帮我生成一个海报图片嘛？"
- **WHEN** the request hits the capability gate
- **THEN** the gate SHALL return `REFUSE` with an honest reason ("我没有图像生成能力") and an alternative
- **AND** the request SHALL NOT enter the agent loop
- **AND** zero `write_file` / `mkdir` / `mcp_filesystem_create_directory` tool calls SHALL be produced

#### Scenario: Normal code request passes the gate

- **GIVEN** a code-mode request "重构 server/db.js 的连接池"
- **WHEN** it hits the capability gate
- **THEN** the gate SHALL return `PASS` and the request proceeds into the agent loop normally

#### Scenario: Gate auto-adapts when a capability is added

- **GIVEN** an image-generation tool is later registered in the ToolRegistry
- **WHEN** an image request hits the gate
- **THEN** the gate SHALL return `PASS` (it reads live tool availability, not a hardcoded blocklist)

#### Scenario: Gate disabled restores legacy behavior

- **GIVEN** `config.toml [companion].capability_gate_enabled = false`
- **WHEN** any request is made
- **THEN** the gate SHALL always return `PASS` (Strangler-Fig rollback)

### Requirement: Companion session writes are scoped to the workspace root

In a companion (`default`) session, file-writing tools SHALL only write within the resolved workspace root; attempts to write arbitrary repository paths SHALL be rejected with a message directing the user to code mode. Code sessions (bound to a project root) are unaffected.

#### Scenario: Companion session blocked from writing into a code repo

- **GIVEN** the current session is `default` (companion)
- **AND** `config.toml [companion].write_scope_enforced = true`
- **WHEN** a tool attempts `mkdir /path/to/deskpet\backend\vpn-cli`
- **THEN** the tool SHALL return `{ok:false, error:"companion session 写盘限定在 workspace；要写项目代码请进 code 模式并选择项目"}`
- **AND** no directory or file SHALL be created outside the workspace root

#### Scenario: Companion session may write inside workspace

- **GIVEN** the current session is `default`
- **WHEN** a tool writes to `<workspace_root>/notes.md`
- **THEN** the write SHALL succeed (workspace is the companion session's allowed area)

#### Scenario: Code session unaffected by write-scope

- **GIVEN** a code session bound to project root `G:\projects\小说网站`
- **WHEN** it writes `G:\projects\小说网站\server\db.js`
- **THEN** the write SHALL succeed (code sessions keep their existing project-root boundary; this change does not touch them)

#### Scenario: write_scope_enforced=false restores legacy behavior

- **GIVEN** `config.toml [companion].write_scope_enforced = false`
- **WHEN** a companion session writes any path
- **THEN** no scope check SHALL apply (Strangler-Fig rollback)
