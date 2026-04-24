# Spec: mcp-integration

## ADDED Requirements

### Requirement: MCP Client via Official Python SDK

系统 MUST 使用官方 `mcp>=1.0` Python SDK 作为 MCP client 实现，MUST NOT 自己手写 JSON-RPC 协议层。支持的 transport：stdio（首选）、SSE、streamable HTTP。实现路径 MUST 是 clean-room rewrite（port Claude-Code-Best 的 pattern 到 Python），不得直接 lift CCB 代码（CCB 无 license）。

#### Scenario: Dependency declared in requirements

- **WHEN** 审查 `pyproject.toml` / `requirements.txt`
- **THEN** MUST 含 `mcp>=1.0` 且 MUST NOT 含任何手写 JSON-RPC 库替代品

#### Scenario: Stdio server spawn and session init

- **WHEN** 系统启动，config 声明 `mcp.servers=[{name:"filesystem", command:"npx @modelcontextprotocol/server-filesystem ..."}]`
- **THEN** MUST 用 `mcp.client.stdio.stdio_client(...)` 拉起子进程并建立长连的 `ClientSession`，初始化时 MUST 调用 `session.initialize()` 握手

### Requirement: Server Configuration in config.toml

MCP servers MUST 通过 `config.toml [mcp.servers]` 数组配置，每条含 `name, command, args, transport, env, enabled`。系统启动 MUST 按 `enabled=true` 列表拉起所有 server，各 server 失败 MUST 独立处理不得互相影响。

#### Scenario: Enabled servers started on boot

- **WHEN** config 含 filesystem + weather 两个 enabled server
- **THEN** 系统启动完成后 MUST 有 2 个对应子进程，`mcp_registry.list_servers()` MUST 返回这两者状态=connected

#### Scenario: Disabled server skipped

- **WHEN** config 某 server 标 `enabled=false`
- **THEN** 启动时 MUST 跳过该 server，不得 spawn 子进程

### Requirement: Connection Lifecycle Management

系统 MUST 维护 MCP 连接生命周期：启动即连、会话期长连、应用退出时优雅关闭子进程。子进程崩溃 MUST 被检测到并触发自动重连（exponential backoff：1s → 2s → 4s → 8s，最多 5 次）。

#### Scenario: Graceful shutdown closes sessions

- **WHEN** 应用退出 SIGTERM
- **THEN** 系统 MUST 对每个 MCP server 调用 `session.close()` + 终止子进程，不得留下孤儿进程

#### Scenario: Crash triggers reconnect

- **WHEN** filesystem server 子进程异常退出
- **THEN** MCP manager MUST 在 1s 内检测，按指数退避重连；重连成功后 MUST 重新调用 `list_tools` 刷新 schema

#### Scenario: Reconnect fails after max retries

- **WHEN** server 连续 5 次重连失败
- **THEN** 系统 MUST 标记该 server state=failed，从 `registry.schemas()` 中移除其工具，log error；不得持续无限重连阻塞 event loop

### Requirement: Dynamic Tool Discovery and Schema Registration

每个 MCP server 连接建立后 MUST 调用 `session.list_tools()` 发现其工具，把返回 schema 注入全局 `ToolRegistry`，tool name MUST 以 `mcp_{server_name}_{tool_name}` 前缀 namespace 避免冲突。

#### Scenario: Tools appear in registry

- **WHEN** filesystem server 暴露 `read_file` / `write_file` / `list_directory`
- **THEN** `registry.schemas()` MUST 含 `mcp_filesystem_read_file` 等 3 个带 namespace 前缀的 schema

#### Scenario: Name collision avoided by namespace

- **WHEN** filesystem 和 git server 都有 `read_file` 工具
- **THEN** 注册后名字 MUST 分别是 `mcp_filesystem_read_file` 和 `mcp_git_read_file`，不冲突

### Requirement: Unified mcp_call Dispatch Tool

系统 MUST 暴露一个 top-level 工具 `mcp_call(server_name, tool_name, args)` 供 agent 统一调用任一 MCP server 的任一工具。该工具 MUST 路由到对应 ClientSession 的 `call_tool(name, args)`，返回结果 MUST 转成 JSON 字符串。

#### Scenario: mcp_call routes to correct server

- **WHEN** agent 调用 `mcp_call("filesystem", "read_file", {"path": "/tmp/a.txt"})`
- **THEN** 系统 MUST 通过 filesystem server 的 session 调用，而不是 weather 的

#### Scenario: Unknown server returns error

- **WHEN** agent 调用 `mcp_call("nonexistent", "foo", {})`
- **THEN** MUST 返回 `{"error": "mcp server 'nonexistent' not registered", "retriable": false}`

### Requirement: Built-in Server Allowlist

MVP ship 阶段只配置 2 个 MCP server：`@modelcontextprotocol/server-filesystem`（scope 限定 `%APPDATA%\deskpet\workspace\`）和 `@example/weather`（free open-meteo wrapper）。MUST NOT 配置 `@modelcontextprotocol/server-brave-search` 或任何需付费 API 的 server（D5 决策）。

#### Scenario: Filesystem server scoped to workspace

- **WHEN** 审查 config.toml mcp.servers filesystem 配置
- **THEN** args MUST 含路径参数 `%APPDATA%\deskpet\workspace\`，不得是 `/` 或 `%USERPROFILE%` 等广泛路径

#### Scenario: No paid-api servers shipped

- **WHEN** 审查默认 config mcp.servers
- **THEN** MUST NOT 出现 brave-search、tavily、perplexity、bing 等付费 API server 条目

### Requirement: Tool Gating by MCP Connection State

MCP 工具 MUST 通过 tool-framework 的 `check_fn` 机制标记"仅当 session 存活时可用"。server 断连期间该 server 的工具 MUST 在 `schemas()` 中被过滤掉或在 dispatch 时返回明确错误。

#### Scenario: Disconnected tools hidden

- **WHEN** filesystem server 断连且未重连成功
- **THEN** `registry.schemas()` MUST NOT 返回 `mcp_filesystem_*` 工具

#### Scenario: Dispatch on dead session fails fast

- **WHEN** agent 在 session 尚未重连时调 `mcp_call("filesystem", ...)`
- **THEN** 系统 MUST 立即返回 `{"error": "mcp server 'filesystem' disconnected", "retriable": true}`，不得 hang

### Requirement: Resource and Prompt Access (Read-Only)

系统 MUST 支持通过 MCP 的 `list_resources()` / `read_resource()` 读取 server 暴露的资源（如文件、URL），以及 `list_prompts()` / `get_prompt()` 读取 server 暴露的 prompt template。MVP 阶段只读，写操作通过工具调用完成。

#### Scenario: Resources listable via IPC

- **WHEN** 前端通过 IPC 请求 `mcp_list_resources(server="filesystem")`
- **THEN** 后端 MUST 调用对应 session.list_resources() 并返回 URI 列表
