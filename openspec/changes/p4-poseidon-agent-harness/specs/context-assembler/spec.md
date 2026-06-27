# Spec: context-assembler

## ADDED Requirements

### Requirement: Pre-loop Context Assembly

系统 SHALL 提供 `ContextAssembler` 组件，在 agent loop 启动之前根据用户输入识别 task_type，按策略挑选并组合记忆切片、工具 schema 子集、skill、persona、time、workspace 等组件，输出 `ContextBundle` 供 agent loop 使用。Assembler MUST 在每轮必过，不能跳过。

#### Scenario: Assembler runs before agent loop

- **WHEN** DeskPetAgent.run_conversation 被调用
- **THEN** 第一步 MUST 是 `context_assembler.assemble(user_message, history)` 返回 ContextBundle，之后才进入 while loop

#### Scenario: Assembler feeds tool_schemas to LLM

- **WHEN** ContextBundle 指定 `tool_schemas=[memory_read, memory_search]` 仅 2 个
- **THEN** agent loop 的 LLM 调用 MUST 用这 2 个 schema，而不是 registry 全部 16 个

### Requirement: Task Classifier Three-Tier Cascade

系统 MUST 提供 `TaskClassifier` 按 rule → embed → LLM 三层级联分类。rule 层 < 2ms 处理明确信号；embed 层 ≤ 15ms 用 exemplars cosine 相似度；LLM 层 ≤ 300ms 作为 fallback。D8 决策 LLM 层默认开启。

#### Scenario: Rule hit short-circuits

- **WHEN** 用户输入以 `/` 开头（slash command）
- **THEN** TaskClassifier MUST 立刻返回 `task_type=command`，不走 embed 和 LLM

#### Scenario: Embed confidence threshold

- **WHEN** embed 层相似度最高分 > 0.75
- **THEN** MUST 直接返回该 task_type，不调 LLM

#### Scenario: Low-confidence triggers LLM fallback

- **WHEN** embed 最高分 ≤ 0.75 且 `config.context.assembler.classifier_mode` 含 `llm`
- **THEN** MUST 调用 `claude-haiku-4-5` 分类，LLM 返回即为最终 task_type

### Requirement: Task Types Enumeration

系统 MUST 支持至少 8 种 task_type：`chat`、`recall`、`task`、`code`、`web_search`、`plan`、`emotion`、`command`。每种对应一组默认 AssemblyPolicy 规则。

#### Scenario: Unknown task_type falls back to chat

- **WHEN** 分类结果超出已知 8 类（如 llm 吐出 `task_type=xyz`）
- **THEN** 系统 MUST fallback 到 `chat` 策略，不得崩溃

### Requirement: Component Registry and Parallel Fan-out

系统 MUST 提供 `ComponentRegistry` 管理可插拔的 `Component` 实例（内置 6 个：memory / tool / skill / persona / time / workspace）。组件 MUST 并行调用 `.provide(ctx)`（`asyncio.gather`），总耗时 MUST 接近最慢组件而非组件耗时总和。

#### Scenario: Parallel assembly beats serial

- **WHEN** 组件耗时分别为 memory=30ms, tool=5ms, skill=10ms, persona=2ms, time=1ms, workspace=20ms
- **THEN** 总组装耗时 MUST < 40ms（max + overhead），不是 68ms（串行总和）

### Requirement: Declarative YAML Assembly Policy

系统 MUST 支持用 YAML 声明式定义 AssemblyPolicy，策略 MUST 按 `task_type` 分组指定 `must/prefer` 组件列表、`tools` 白名单、`memory.l1/l2/l3` 召回参数。用户 overrides.yaml MUST 和 default.yaml 合并，用户覆盖 > 默认；`must` 允许追加但 MUST NOT 删除核心 memory 组件（D9）。

#### Scenario: User overrides add new policy

- **WHEN** user overrides.yaml 新增 `policy.music.tools=[spotify_play]`
- **THEN** 合并后的 policy 字典 MUST 包含 `music` 条目

#### Scenario: User cannot remove memory from must

- **WHEN** user overrides.yaml 写 `policy.chat.must=[persona]`（试图去掉 memory）
- **THEN** 合并后 `policy.chat.must` MUST 仍含 `memory`；系统 MUST 日志警告"核心组件不可删除"

### Requirement: Budget Allocator

系统 MUST 在 components 总 token 超预算时（预算 = `context_window × budget_ratio`，默认 0.6）按 `component.priority` 从低到高裁剪：memory 缩 top_k、tool 剔除 rare tools、skill/workspace 整体 drop。

#### Scenario: Over-budget memory shrink

- **WHEN** bundle 组装后 memory 占 5000 tokens、总预算 3000
- **THEN** MUST 按 priority 优先缩 L3 top_k（10→5→3），若仍超则缩 L2 top_k，核心 L1 不得删

### Requirement: ContextBundle Data Structure

系统 MUST 提供 `ContextBundle` dataclass 含字段：`task_type`、`frozen_system`（稳定部分）、`memory_block`（动态部分）、`skill_prelude`、`tool_schemas`、`decisions`、`cost_hint`。MUST 提供 `build_messages(base_system)` 方法构造 OpenAI-format messages list。

#### Scenario: Build messages preserves cache-friendly order

- **WHEN** bundle.build_messages("基础 prompt") 被调用
- **THEN** 返回的 messages MUST 顺序为：`[frozen_system（cacheable）, skill_prelude, memory_block（dynamic system）]`，frozen 部分可打 cache breakpoint

### Requirement: Prompt Cache Compatibility

Assembler 输出的 frozen_system 部分 MUST 每轮稳定（仅在策略变更 / L1 文件刷新时才变）。dynamic 部分（memory_block / skill_prelude）MUST 作为独立 system messages 排在 frozen 之后，不破坏 frozen 部分的 prefix cache。

#### Scenario: Cache hit rate over 5 chat turns

- **WHEN** 连续 5 轮 chat 任务（task_type=chat 稳定）
- **THEN** 从第 2 轮起 Anthropic 响应的 `cache_read_tokens / input_tokens` MUST ≥ 80%

### Requirement: Decisions Trace Logging

Assembler MUST 每轮写 `decisions` 对象到 session log，字段包含 `task_type`、`classifier_path`（rule/embed/llm）、`classifier_latency_ms`、`assembly_latency_ms`、`components.*.tokens`、`budget_cut`、`total_tokens`。

#### Scenario: Trace visible in Context Trace UI

- **WHEN** 前端 Context Trace Panel 请求当前会话的 decisions
- **THEN** 系统 MUST 通过 IPC 返回最近 N 轮的 decisions 数组，前端可渲染每轮组件和 tokens 分布

### Requirement: Feedback Recording for Future Learning

Assembler MUST 在每轮结束后接收 `feedback(bundle, used_tools, final_response)` 写入 decisions 尾部，用于后续离线学习（D10 v1 只记录不学习）。

#### Scenario: Feedback persisted after final response

- **WHEN** agent 返回 final_response
- **THEN** 系统 MUST 调用 `assembler.feedback(...)`，feedback 记录 MUST 含 `planned_tools=[...] vs used_tools=[...]` 对比，用于 Phase 5 online adaptive

### Requirement: Enable/Disable Flag for Rollback

Assembler MUST 可通过 `config.context.assembler.enabled=false` 完全关闭，退化到全量工具 registry + 全量 memory prefetch 模式（Hermes 原设计），作为紧急回滚路径。

#### Scenario: Disabled mode falls back to legacy

- **WHEN** config 设置 enabled=false
- **THEN** DeskPetAgent.run_conversation MUST 跳过 assembler，使用 tool_registry.schemas() 全量 + memory_manager.prefetch_all 路径
