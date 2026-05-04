## Context

DeskPet 当前架构（rc3 + S19）：
- **Agent loop**: `SimpleLLMAgent`（直接代理 LLM）+ `ToolUsingAgent`（regex 检测 `[tool:xxx]` 标签触发**单次**工具调用）
- **Tool registry**: 17 个 hardcoded Python 函数（get_time / clipboard / reminder / read_clipboard / list_reminders / ...），通过 `ToolSpec` 包装
- **Skill loader**: 自家 YAML schema（`name/description/version/author/policy/script`），脚本通过 `subprocess` `python -I` 沙箱执行
- **MCP**: 已接入 filesystem MCP server，scope 限定 `%APPDATA%/deskpet/workspace`
- **LLM provider**: `OpenAICompatibleProvider.chat_stream()` — 单条流式输出，**没有** tool_use API

**用户痛点**：连"在桌面建一个 todo.txt"都做不到。根因：LLM 输出"我帮你创建 xxx 文件"但没有真工具可调，且 ToolUsingAgent 只检测 `[tool:xxx]` 标签触发单次工具，复杂任务（多次 read/write/list）走不通。

**业界标准**（Claude Code SKILL.md / agentskills.io）：
- Skill = `SKILL.md`（YAML frontmatter + markdown body）
- LLM 通过 `description` 字段决定何时触发 skill
- 工具调用走 OpenAI/Anthropic 原生 `tool_use` API
- 安全模型：`allowed-tools` 预批 + `permission-request` 用户确认 + `disable-model-invocation` 禁止自动调用

cc-haha 验证了这套标准在 TypeScript 完整可行；deskpet 用 Python 实现需要解决：
- httpx SSE 流式 + tool_use 增量解析
- LLM provider 抽象层支持双协议（OpenAI tool_calls + Anthropic content blocks）
- 跨进程 permission popup（backend ↔ Tauri 前端 IPC）

## Goals / Non-Goals

**Goals:**
- LLM 真 function calling：可一轮多工具、可链式调用直到完成（OpenAI tool_calls 协议为主，Anthropic content blocks 协议作 secondary）
- 解锁 OS 级操作：read/write/edit file、list dir、run shell（带 deny list）、web fetch、desktop create file
- 100% 兼容 Claude Code SKILL.md 标准（能直接装 [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills) 文档里的官方 example）
- 安全：每个写/执行操作必须用户审批；session-level "always allow" 缓存；deny rules 可配
- 前端 SkillStorePanel：浏览/搜索/一键安装/卸载；安装走完整审批流
- Plugin 体系：plugin = skills + 自定义 MCP servers + UI（保留扩展点，本次不做完整 marketplace 上传链路）
- 现有 679 个测试全部继续通过；旧 17 个工具继续用；旧 deskpet skill 格式继续工作；BGE-M3 子进程不受影响

**Non-Goals:**
- 不做计算机控制（mouse/keyboard 自动化）—— 留给未来 plugin
- 不做 skill 编辑器（用户写 skill 还是用文本编辑器或 Claude Code）
- 不做付费 / billing skill
- 不做 mobile 端
- 不做 plugin 自动更新（只做手动更新）
- 不实现 Claude Code 的全部高级特性（如 `context: fork` 子 agent、`hooks` PostToolUse 钩子）—— 第一版只做 inline + PreToolUse 钩子

## Decisions

### D1. tool_use 协议优先级 = OpenAI > Anthropic > regex fallback

**选择**：把 OpenAI tool_calls 协议作为内部规范化格式（因为更主流，绝大多数转发服务支持），Anthropic content blocks 作为可选适配层。Regex 标签检测保留为 fallback（用户配的本地小模型 like Qwen 不支持 tool_use 时仍能跑）。

**理由**：
- OpenAI tool_calls 是 GPT-4 / GPT-5 / Claude（通过 OpenRouter）/ DeepSeek / Qwen / Gemini 全部支持
- Anthropic content blocks 只在直连 Anthropic API 时用
- 内部统一一种格式，转换层只在 provider 边界

**备选**：内部用 Anthropic 格式作为标准 → 抛弃。Anthropic 格式 nested 复杂，不利于 prompt cache。

### D2. Permission Gate 走 IPC 弹气泡，不走 LLM 内置 confirm

**选择**：每次工具调用前，backend 发 `permission_request` IPC 给前端，前端弹气泡（Yes / Yes-always / No）。审批结果通过 `permission_response` IPC 回传，backend 等待后再执行。`Yes-always` 写到 session-level cache（重启失效）。

**理由**：
- 与 LLM 解耦：LLM 不需要知道 permission 系统存在，只是发 tool_call
- 用户能看到具体哪个 skill / 哪个工具 / 什么参数
- 与 Claude Code 行为一致

**备选**：Bot 自己提问"我要创建 todo.txt 你确认吗？" → 抛弃。LLM 容易绕过，安全性差。

### D3. SkillLoader 双解析路径（保留旧 + 加新）

**选择**：`SkillLoader._parse_skill(path)` 改成 dispatch：
- 文件名 `SKILL.md` → 走 `_parse_skill_md()` (Claude Code 标准)
- 文件名 `<name>.skill.md` 或 `<name>.skill.yaml` → 走 `_parse_legacy()` (deskpet 旧)

**理由**：
- 旧 3 个内置 skill（recall-yesterday/summarize-day/weather-report）零迁移
- 新装的 GitHub skill 是标准格式
- 用户/agent 自己写的 skill 推荐用新格式

**备选**：强制迁移旧格式 → 抛弃。会破坏现有功能 + 现有测试。

### D4. SKILL.md 第一版只实现核心字段

**选择**：

| Field | 第一版 | 后续 |
|---|---|---|
| `description` | ✅ | — |
| `when_to_use` | ✅ | — |
| `name` | ✅ | — |
| `allowed-tools` | ✅ (allowlist) | — |
| `disable-model-invocation` | ✅ | — |
| `argument-hint` | ✅ | — |
| `paths` glob | ✅ | — |
| `context: inline` | ✅ | — |
| `context: fork` | ❌ | v2 |
| `hooks: PreToolUse` | ✅ | — |
| `hooks: PostToolUse` | ❌ | v2 |
| `model` 覆盖 | ❌ | v2 |
| `effort` 覆盖 | ❌ | v2 |
| `${CLAUDE_SKILL_DIR}` / `$ARGUMENTS` / `$N` | ✅ | — |
| `` !`shell` `` inline injection | ✅ | — |
| ` ```! ` fenced injection | ❌ | v2 |

**理由**：第一版必须能装现成的简单 skill（占社区 90%）。复杂特性（fork agent / model override）留给 v2。

### D5. Skill 安装走 git clone，不走 npm/pip

**选择**：`skill_install_from_url(github_url)` 用 `git clone --depth 1` 拉到 `%APPDATA%/deskpet/skills/<extracted_name>/`。

**理由**：
- GitHub 是社区 skill 的事实集中地
- `git clone --depth 1` 比 download zip 慢 ~2x 但拿到完整提交信息
- 后续 `git pull` 更新简单
- 不引入 npm/pip 这种额外依赖管理

**备选**：requests + zip download → 没有版本控制信息。npm-style registry → 太重。

### D6. SkillStorePanel 数据源 = 静态 JSON，不做服务端

**选择**：官方 skill 索引托管在一个 GitHub repo 的 `skills-registry.json`，前端 fetch raw URL。用户也可输入任意 GitHub URL 安装。

**理由**：
- 零服务器成本
- 透明（用户能看到 registry 内容）
- PR 添加 skill = 走 GitHub PR 流程

**备选**：自建 registry server → 服务器 + 维护 + 审核成本，第一版不值得。

### D7. Permission category 7 类

**选择**：

| Category | 描述 | 默认 |
|---|---|---|
| `read_file` | 读本地文件 | 自动通过（read 不破坏） |
| `write_file` | 写/编辑/删本地文件 | 弹气泡 |
| `desktop_write` | 写桌面 | 弹气泡 + 高亮 |
| `shell` | 执行 shell 命令 | 弹气泡 + 显示 cmd |
| `network` | 网络请求（web_fetch / API） | 弹气泡 + 显示 URL |
| `mcp_call` | 调外部 MCP server | 弹气泡 + 显示 server+tool |
| `skill_install` | 装 skill | 弹气泡 + 显示 manifest 声明的所有 tool |

**理由**：read 不弹是为了不打扰用户（LLM 经常 read 几十次）；write/shell/network 是高风险必弹。

**备选**：每个 tool 自己定弹不弹 → 没标准化。全弹 → 用户烦疯。

### D8. tool_use loop 上限 = 25 轮

**选择**：每个 chat_stream 调用最多跑 25 轮 tool_use（参考 Claude Code 默认）。超出报错"Maximum tool turns exceeded"。

**理由**：
- 防止 LLM 死循环（grep → grep → grep ...）
- 25 轮够 99% 真实任务
- 可通过 config 调大

**备选**：无上限 → 用户看到费用爆炸。10 轮 → 复杂任务跑不完。

### D9. Permission cache scope = session-level，不持久化

**选择**：`Yes-always` 缓存在 backend memory，**仅本次 backend 进程生命周期**有效。重启 backend 清空。

**理由**：
- 防止 skill 长期获得权限被忘记
- session 级足够覆盖单次工作流
- 持久化需要做权限管理 UI（撤销 always grant），增加复杂度

**备选**：写 sqlite 持久化 → 需要 UI 让用户撤销 → 第一版不做。

### D10. Plugin 第一版 = "scaffolding"，不做完整 marketplace

**选择**：第一版 plugin 体系只做：
- plugin manifest 解析（plugin.json）
- plugin/skills/ 自动加载
- plugin/mcp.json 注册到 MCPManager
- plugin enable/disable 命令

**Non-goal**：plugin marketplace UI、plugin 自动更新、plugin 间依赖管理（v2 做）

**理由**：plugin 是 skill 的超集，第一版让用户能"git clone 一个完整 plugin"就够。marketplace UI 复杂度高，等 skill marketplace 验证后再做。

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| **R1**: LLM 调 shell 执行恶意命令 → 用户机器中招 | (a) shell 工具有 deny pattern (`rm -rf /`, `format c:`, etc); (b) 每次 shell 必弹气泡显示完整 cmd; (c) skill manifest 声明 `shell` 权限时安装时高亮; (d) `disable-model-invocation: true` 推荐给所有 shell skill |
| **R2**: 第三方 skill 含恶意脚本（窃取 cookie / key / 上传文件） | (a) skill 安装时 manifest tool allowlist 必须用户确认; (b) `network` 权限单独弹气泡显示目标 URL; (c) `read_file` 默认通过但 sensitive path（`.env` / `id_rsa` / `Cookies`）拦下来弹气泡; (d) 文档强烈推荐只装 verified 来源 |
| **R3**: tool_use loop 死循环烧 token | D8 的 25 轮上限 + chat 总 token budget cap (沿用现有) |
| **R4**: 用户拒绝 permission 后 LLM 卡住不知怎么继续 | tool_result 注入 `{"error": "user denied permission"}` 让 LLM 自然回应 |
| **R5**: Permission popup 阻塞 backend，影响其他 chat session | 每个 permission_request 异步等待，使用 asyncio.Future + per-request id；popup 拒绝/超时 (60s) 自动 deny |
| **R6**: Skill hot-reload 时正在用旧 skill 的 chat 崩 | reload 用 copy-on-write：旧 skill 引用保持有效，新 chat 用新 skill |
| **R7**: 装 skill 的 git clone 很慢/网络受限 | (a) 默认走 GitHub HTTPS； (b) 用户可在 settings 改 mirror（gitee/codeup）； (c) 失败显示具体网络错误 |
| **R8**: 旧 deskpet skill 格式 + 新 SKILL.md 同名冲突 | SkillLoader 加载时检测重名 → 高优先级胜（按 D5 的优先级表）+ 日志 warn |
| **R9**: tool_use API 行为在不同 provider 略有差异 | 抽象 `chat_with_tools()` 接口，每个 provider impl 自己做 normalization；测试覆盖 OpenAI / chinzy.com / DeepSeek / Anthropic 4 个真实 provider |
| **R10**: 测试时 chat_with_tools 烧 token | 集成测试用 fake LLM provider；真 LLM 测试限于一次端到端 smoke + bench |

## Migration Plan

**Stage A** (real function calling) deployable first，向后兼容（旧 chat 不受影响）。

**Phase 1 — Stage A landing**:
1. 新增 `chat_with_tools()` 到 `OpenAICompatibleProvider`，旧 `chat_stream` 不动
2. 新增 `ToolUseAgent`（独立类，不替换 `ToolUsingAgent`）
3. main.py 加配置 flag：`agent.tool_use_mode = "auto"|"on"|"off"`，"auto" 时检测 LLM model 名字决定
4. 旧 17 个 tool 加 `description_for_llm` + `input_schema_json` 字段
5. 新增 7 个 OS 工具（先无 permission gate，跑通基础协议）
6. 加 permission_gate（IPC popup + session cache）
7. 端到端 smoke：「在桌面建 todo.txt」

**Phase 2 — Stage B landing (SKILL.md compat)**:
8. 新增 `deskpet/skills/skill_md.py` 解析器
9. `SkillLoader._parse_skill()` dispatch
10. Inline shell injection + 变量替换
11. Hot-reload watcher
12. 装 1 个 GitHub 上现成 skill 验证

**Phase 3 — Stage C landing (Marketplace)**:
13. 后端 marketplace IPC handlers
14. 前端 SkillStorePanel
15. 一键安装流程 + 权限审批
16. 静态 registry.json 上线

**Phase 4 — Stage D landing (Plugin scaffolding)**:
17. plugin manifest 解析
18. plugin/skills 自动加载
19. plugin/mcp.json 注册到 MCPManager

**Rollback strategy**:
- 每个 Phase 单独 commit + tag。失败可 `git revert` 单个 commit
- Backend 启动时 catch all in P4-S20 wire-in 块（仿 P4-S13 模式），任何 init 失败降级到 deskpet rc3 行为

## Open Questions

- **Q1**: tool_use loop 中如果 user 拒绝某次 permission，LLM 看到 `{"error":"denied"}` 后会怎么做？需要测试不同 LLM 反应。预期：好的 LLM (GPT-5.x / Claude) 会问用户"我没法做 X，你想怎么办？"，差的 LLM 会重试。**决策**：第一版直接传 `denied` 给 LLM 看它表现，根据 4 个 provider 测试结果决定要不要 prompt engineering hint。
- **Q2**: `desktop_create_file` 默认目录 = `Desktop` 还是 `~/Desktop`？Windows 用 `%USERPROFILE%\Desktop`，需要 OS 检测。**决策**：写 helper `resolve_user_desktop()` 处理 Win/Mac/Linux 差异。
- **Q3**: skill registry.json 位置？是 deskpet 主仓库 `docs/skills-registry.json`，还是单独 repo `deskpet-skills-registry`？**决策**：第一版放主仓 `docs/skills-registry.json`，社区 PR 加 skill 走主仓 PR 审核。后续如果 skill 数 >50 再拆。
- **Q4**: Plugin 的 MCP server 配置怎么和现有 `[mcp]` 段共存？**决策**：MCPManager 启动时 merge config.toml 的 `[mcp]` + 所有已 enabled plugin 的 mcp.json。同名 server 后者覆盖前者并日志 warn。
- **Q5**: 给 LLM 的 tool description 长度有没有 cap？太长占 prompt 空间。**决策**：每个 tool description ≤ 300 字符（仿 Claude Code）；input schema description 字段 ≤ 100 字符；超出截断 + 日志 warn。
