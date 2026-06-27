## Why

DeskPet 当前是一个"语音/对话桌宠"，但用户对它的期待是"通用 AI 助手 + 可扩展技能平台"。**现状无法满足最基本的"在桌面创建一个 todo.txt 文件"** —— 因为 `ToolUsingAgent` 用 regex 检测 `[tool:xxx]` 标签触发工具，不是真 function calling；同时也没有 OS 级文件操作工具暴露给 LLM。更深的问题是 deskpet 自家 YAML skill 格式与业界 Claude Code SKILL.md / agentskills.io 标准不兼容，**用户没法装 GitHub 上现成的社区 skill**。这次升级把 deskpet 从"封闭桌宠"演进成"开放 Skill 平台"。

## What Changes

- **真 function calling**：替换 `ToolUsingAgent` 的 regex 检测为 OpenAI/Anthropic 原生 tool_use API loop。LLM 可主动多次调用工具直到任务完成。**BREAKING**: `ToolUsingAgent.chat_stream` 行为改变（仍向后兼容旧 regex 路径作为 fallback）。
- **OS 级工具集**：新增 `read_file` / `write_file` / `edit_file` / `list_directory` / `run_shell` / `web_fetch` / `desktop_create_file` 七个工具。每个写/执行类工具都有**用户审批 popup**（Yes / Yes-always / No）。
- **Claude Code SKILL.md 标准兼容**：`SkillLoader` 新增 SKILL.md 解析器，识别 `description` / `when_to_use` / `allowed-tools` / `disable-model-invocation` / `context: inline|fork` / `paths` glob / `hooks` 字段。支持 `` !`shell` `` inline injection、`${CLAUDE_SKILL_DIR}` / `$ARGUMENTS` 占位符。Hot-reload watcher 监控 skill 目录。**BREAKING**: SkillLoader API 增加 `parse_skill_v2` 路径，旧 `parse_skill` 仍工作（backward compat）。
- **Skill 加载优先级**：`bundled > %APPDATA%/deskpet/skills > <project>/.claude/skills > plugin`（仿 Claude Code）。同名 skill 高优先级覆盖低优先级。
- **Skill Marketplace UI**：新前端 panel `SkillStorePanel.tsx`，浏览/搜索/一键安装/卸载。数据源：官方 `skills-registry.json`（GitHub 托管）+ 用户提交 GitHub URL。安装走「下载 → manifest 安全检查 → 用户审批 tool 权限 → SkillLoader hot-reload」全流程。
- **Plugin 体系**：plugin = skills + 自定义 MCP servers + UI 组件。Plugin 格式遵循 Claude Code 约定（`<plugin>/skills/`、`<plugin>/mcp.json`、`<plugin>/manifest.json`）。
- **Permission 系统**：所有写操作（`write_file` / `run_shell` / `desktop_create_file` / 安装 skill）走统一的 `permission_request` IPC，弹气泡让用户决定。审批结果可缓存（per-session "always allow"）。

## Capabilities

### New Capabilities

- `tool-use`: LLM 真 function calling 协议。负责 tool 描述生成（OpenAI / Anthropic schema）、tool_use 循环管理、tool_result 注入对话上下文、permission gate 集成。
- `os-tools`: OS 级工具实现。包括 filesystem（read/write/edit/list/desktop）、shell（run_shell with deny-list）、network（web_fetch）。每个工具有清晰的 input schema 和 permission category。
- `skill-md-parser`: Claude Code SKILL.md 标准解析器。frontmatter parsing（description / when_to_use / allowed-tools / disable-model-invocation / context / paths / hooks 字段）、变量替换（`${CLAUDE_SKILL_DIR}` / `$ARGUMENTS` / `$N`）、inline shell injection（`` !`cmd` `` 和 fenced ` ```! ` 块）。
- `skill-marketplace`: 前端 SkillStorePanel + 后端 IPC handlers。Browse / search / install / uninstall。registry.json 解析、GitHub URL 解析、本地 skill 元数据缓存。
- `permission-gate`: 统一 permission 系统。permission category 定义、`permission_request` IPC、popup UI、per-session "always allow" 缓存、deny rules。
- `plugin-system`: Plugin 加载机制。plugin manifest 解析、skills 子目录加载、自定义 MCP server 注册到 MCPManager、plugin enable/disable。

### Modified Capabilities

- `agent-loop`: chat_stream 现在跑 tool_use loop 直到 LLM 不再请求工具，而非检测 `[tool:xxx]` 标签后单次调用。
- `skill-loader`: 加 SKILL.md 解析路径（保留旧 `.skill.md` 路径），加 hot-reload watcher，加优先级解析。
- `tool-registry`: ToolSpec 新增 `permission_category` / `description_for_llm` / `input_schema_json` 字段；新增 `to_openai_schema()` / `to_anthropic_schema()` 方法。

## Impact

**Affected code (backend)**:
- `agent/providers/tool_using.py` — 改造为 tool_use loop
- `agent/providers/simple_llm.py` — chat_stream 接口加 `tools` 参数
- `providers/openai_compatible.py` — 加 `chat_with_tools()` 方法
- `deskpet/tools/registry.py` — ToolSpec 扩展
- `deskpet/tools/` 下新增 `os_tools.py` / `shell_tools.py` / `web_tools.py`
- `deskpet/skills/loader.py` — SKILL.md parser
- `deskpet/skills/skill_md.py`（新建）— 标准解析器
- `deskpet/permissions/` （新建）— permission gate
- `main.py` — 注册新 IPC handler `permission_request_response` / `skill_install` / `skill_uninstall`
- `p4_ipc.py` — 加 marketplace 相关 handlers

**Affected code (frontend)**:
- `tauri-app/src/components/SkillStorePanel.tsx`（新建）
- `tauri-app/src/components/PermissionPopup.tsx`（新建）
- `tauri-app/src/components/SettingsPanel.tsx` — 加入口
- `tauri-app/src/types/messages.ts` — 加 marketplace + permission 类型

**APIs**:
- 新 IPC: `permission_request` / `permission_response` / `skill_list_installed` / `skill_install_from_url` / `skill_uninstall` / `skill_marketplace_list`
- 新 LLM provider 协议: `chat_with_tools` 必须实现

**Dependencies**:
- 后端：可能需 `httpx` 升级支持 SSE chunked tool_calls；watchdog 已在用
- 前端：无新 npm 依赖

**Tests**:
- 必须保持 679 现有测试通过
- 每个新 capability 至少 5+ TDD-first 单元测试
- 新增 Stage A 端到端 smoke：「给 LLM 真 key + tools，让它创建 desktop/todo.txt」必须通过
- 新增 Stage B SKILL.md 兼容性测试：能正确加载 Claude Code 官方 example skill

**Backward compatibility**:
- 旧 deskpet skill 格式（`<name>.skill.md` + YAML schema）继续工作
- 旧 17 个内置工具继续工作
- `ToolUsingAgent` 旧 regex 路径作为 LLM 不支持 tool_use 时的 fallback
- BGE-M3 子进程（P4-S19）不受影响
