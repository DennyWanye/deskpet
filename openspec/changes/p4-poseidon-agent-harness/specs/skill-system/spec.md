# Spec: skill-system

## ADDED Requirements

### Requirement: Markdown Skill Definition

系统 SHALL 支持用户定义 skill：每个 skill 是一个目录，含 `SKILL.md`（YAML frontmatter + body）+ 可选 `script.py`。Skill frontmatter MUST 包含 `name, description, version, author` 字段。目录结构 MUST 兼容 agentskills.io 标准（Hermes / Claude Code 共用格式）。

#### Scenario: Valid skill loaded

- **WHEN** 用户在 `%APPDATA%\deskpet\skills\user\my-skill\SKILL.md` 写入合法 frontmatter + body
- **THEN** skill loader MUST 发现并加载，`/my-skill` 可被识别为 slash command

#### Scenario: Invalid frontmatter rejected

- **WHEN** SKILL.md 缺少 required 字段（如无 `name`）
- **THEN** loader MUST log warning 并跳过，不得崩溃

### Requirement: Built-in Skills

仓库 MUST ship 至少以下 built-in skills：

- `recall-yesterday` — 回忆昨天的重要对话
- `summarize-day` — 汇总今天的对话
- `weather-report` — 查本地天气（依赖 mcp weather server）

#### Scenario: Built-in skills available after install

- **WHEN** 全新安装 v0.6.0-phase4
- **THEN** `%APPDATA%\deskpet\skills\built-in\` 下 MUST 有上述 3 个 skill，`/recall-yesterday` 等命令可用

### Requirement: Skill Invocation via slash command

前端 MUST 识别用户输入 `/skill-name [args]` 为 slash command，发给 backend 时 MUST 构造为 `skill_invoke(name="skill-name", args=[...])` 工具调用。Agent MUST 通过此工具调用 SkillLoader 执行对应 skill body。

#### Scenario: Slash command triggers skill_invoke

- **WHEN** 用户输入 "/recall-yesterday"
- **THEN** 前端 MUST 将其转为 `skill_invoke(name="recall-yesterday")` 工具调用；后端执行后返回结果

### Requirement: Skill Body as User Message (Cache-Safe)

SkillLoader.execute(name) MUST 将 SKILL.md body 作为 user role message 注入对话（而非 system message），以保持 system prompt 稳定不破坏 prompt cache。

#### Scenario: Skill body injected as user message

- **WHEN** skill_invoke("recall-yesterday") 被调用
- **THEN** 注入的 messages 结构 MUST 是 `{"role": "user", "content": <SKILL.md body>}`，system prompt 不变

### Requirement: Hot Reload with Watchdog

SkillLoader MUST 用 `watchdog` 监听 `%APPDATA%\deskpet\skills\user\` 目录变化，文件改动 MUST 触发 debounce 1 秒后的自动 reload（D3 决策）。reload 失败 MUST log 但不影响已加载 skill。

#### Scenario: Edit triggers reload

- **WHEN** 用户编辑 `my-skill/SKILL.md` 并保存
- **THEN** 1 秒内 loader 自动 reload；下次 `/my-skill` 调用 MUST 使用新 body

#### Scenario: Debounce coalesces rapid edits

- **WHEN** 用户 200ms 内连续保存 5 次
- **THEN** reload MUST 只触发 1 次（在最后一次保存后 1s），不得触发 5 次

### Requirement: Optional Python Script Execution

Skill 目录内若存在 `script.py`，SkillLoader MUST 在沙箱（受限 globals）下执行并把 stdout 作为 user message 注入。脚本超时 MUST ≤ 10s（默认）。

#### Scenario: Skill script stdout injected

- **WHEN** `weather-report/script.py` 执行打印 "今天 22℃ 多云"
- **THEN** stdout MUST 被注入为 user message 供 LLM 使用

#### Scenario: Script timeout killed

- **WHEN** script 执行 > 10s
- **THEN** 系统 MUST kill 进程并返回 `{"error": "skill script timeout"}`，不阻塞 agent loop

### Requirement: Assembler SkillComponent Integration

ContextAssembler 的 SkillComponent MUST 根据当前 task_type 匹配 preferred skills 自动挂载（作为 skill_prelude），无需用户显式 `/skill-name`。匹配规则 MUST 可在 YAML policy 中用 `prefer: [skill:<name>]` 声明。

#### Scenario: Policy-based skill auto-mount

- **WHEN** task_type=recall 且 policy.recall.prefer 含 `skill:recall-yesterday`
- **THEN** ContextBundle.skill_prelude MUST 含 recall-yesterday 的 body

### Requirement: Skill Listing API

系统 MUST 提供 `list_skills()` 返回已加载 skill 列表（含 name, description, version, author, source: built-in/user），用于前端 MemoryPanel 展示和用户管理。

#### Scenario: Frontend lists skills

- **WHEN** 前端通过 IPC 请求 list_skills
- **THEN** 响应 MUST 含全部 built-in + user skills 的 metadata
