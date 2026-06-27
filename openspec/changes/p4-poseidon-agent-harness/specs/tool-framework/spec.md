# Spec: tool-framework

## ADDED Requirements

### Requirement: Auto-Discovery Tool Registry

系统 SHALL 提供 `ToolRegistry` 单例，工具 MUST 通过 `registry.register(name, toolset, schema, handler, check_fn, requires_env)` 在模块加载时自动登记。import `deskpet.tools` 包时所有 `tools/*.py` 子模块 MUST 被加载，每个模块顶部调用 register 即完成发现。

#### Scenario: New tool auto-registered on import

- **WHEN** 新增文件 `deskpet/tools/weather_tool.py` 在模块顶部调用 `registry.register("weather_get", ...)` 
- **THEN** `registry.schemas()` MUST 返回含 `weather_get` 的 schema 列表，无需显式 import

### Requirement: OpenAI-Format Tool Schemas

Registry.schemas(enabled_toolsets=None) MUST 返回 OpenAI function-calling 格式 schema 列表（`{type: "function", function: {name, description, parameters: {type: "object", properties, required}}}`）。可按 toolset 白名单过滤。

#### Scenario: Schemas filtered by toolset

- **WHEN** 调用 `registry.schemas(enabled_toolsets=["memory", "todo"])`
- **THEN** 返回只含 `toolset in ("memory", "todo")` 的工具 schema

### Requirement: MVP Built-in 16 Tools

系统 MUST 内置以下 16 个工具（分 4 组）：

- **memory**: `memory_write`, `memory_read`, `memory_search`
- **todo**: `todo_write`, `todo_complete`
- **file**: `file_read`, `file_write`, `file_glob`, `file_grep`
- **web**: `web_fetch`, `web_crawl`, `web_extract_article`, `web_read_sitemap`
- **control**: `delegate`（子 agent spawn）, `skill_invoke`, `mcp_call`

#### Scenario: All 16 tools registered on startup

- **WHEN** 系统启动完成
- **THEN** `len(registry.schemas()) >= 16`，以上 16 个 name 全部存在

### Requirement: Tool Dispatch with Error Handling

Registry.dispatch(name, args, task_id) MUST 同步调用 handler 并返回 JSON 字符串。handler 抛异常 MUST 被捕获，通过 `error_classifier.classify(exc)` 判断 retriable，返回 `{"error": "...", "retriable": bool}` JSON。

#### Scenario: Handler exception becomes error JSON

- **WHEN** `handler` 抛 `ConnectionError("timeout")`
- **THEN** dispatch MUST 返回 `'{"error": "ConnectionError: timeout", "retriable": true}'`，不向上抛

### Requirement: Tool Search for Lazy Schema Loading

系统 MUST 提供 `ToolSearchTool`（name=`tool_search`），允许 agent 按关键词查询当前未激活的 tool schemas 并按需激活，用于避免 prompt 初始塞入全部 schemas 导致 token 膨胀（CCB pattern）。

#### Scenario: Agent queries for hidden tool

- **WHEN** ContextAssembler 只暴露 8 个 tools，agent 调用 `tool_search(query="weather")`
- **THEN** 系统 MUST 返回匹配 weather 关键词的 tool descriptions 和激活方式；agent 可在后续轮调用被激活的工具

### Requirement: Environment-Based Tool Gating

Tool 注册 MUST 支持 `requires_env=["BRAVE_API_KEY"]` 列表。当前环境缺少任一变量时该 tool MUST NOT 出现在 `schemas()` 返回中。

#### Scenario: Tool hidden when env missing

- **WHEN** tool `web_search_brave` 注册时 `requires_env=["BRAVE_API_KEY"]` 且环境无此变量
- **THEN** `registry.schemas()` MUST NOT 包含 `web_search_brave`

### Requirement: Check Function for Runtime Validation

系统 MUST 支持工具注册时传入可选 `check_fn`（无参返回 bool）。若已提供 `check_fn`，dispatch 前 MUST 先调用该函数；返回 False 时 dispatch MUST 返回 `{"error": "tool not ready: ...", "retriable": true}` 且 MUST NOT 执行 handler。

#### Scenario: Check fn gates unavailable tool

- **WHEN** `memory_search` 注册了 `check_fn=lambda: embedder.is_ready()`，调用时 embedder 尚在预热
- **THEN** dispatch MUST 返回 `'{"error": "tool not ready: embedder not loaded", "retriable": true}'`
