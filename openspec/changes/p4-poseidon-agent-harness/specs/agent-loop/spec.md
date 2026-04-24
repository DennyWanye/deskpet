# Spec: agent-loop

## ADDED Requirements

### Requirement: Agent ReAct Main Loop

系统 SHALL 提供一个支持 ReAct（Observation → Plan → Act → Reflect）循环的 agent 执行器 `DeskPetAgent`，实现单次用户输入到最终回复的多轮工具调用编排。循环 MUST 支持迭代预算上限、早期中断、异常兜底返回。

#### Scenario: Simple chat turn without tool calls

- **WHEN** 用户输入 "你好" 这类无需工具的消息
- **THEN** agent MUST 在 1 次 LLM 调用内完成，返回 `{final_response, api_call_count=1}`，不触发 tool_calls 路径

#### Scenario: Multi-turn tool orchestration

- **WHEN** 用户输入 "查一下今天的天气，然后记下来"
- **THEN** agent MUST 至少调用 2 次 LLM（第一次选工具、第二次整合结果），迭代次数 MUST 不超过 `config.agent.max_iterations`（默认 50），每轮 tool_calls 的 result 追加进 messages

#### Scenario: Iteration budget exhausted

- **WHEN** agent 达到 `max_iterations` 上限仍未收敛
- **THEN** 系统 MUST 返回 fallback message（"到达迭代上限，请换种方式问我"），不得抛异常终止，返回 dict 必须包含 `api_call_count` 用于诊断

### Requirement: Iteration Budget Tracking

系统 MUST 维护 `IterationBudget` 对象跟踪 token 用量、api 调用次数、当前花费，并在预算耗尽时触发优雅降级（最后一次调用允许 grace call 后终止）。

#### Scenario: Budget exhaustion triggers grace call

- **WHEN** `iteration_budget.remaining <= 0` 但 `_budget_grace_call=true`
- **THEN** agent MUST 允许再一次 LLM 调用用于产出最终回复，之后终止循环

#### Scenario: Budget cap from config

- **WHEN** `config.agent.budget_cap_usd = 2.0` 且当前对话已花 $1.95
- **THEN** 下一次 LLM 调用前 MUST 检查预算，超过则走 grace call 路径不再消费

### Requirement: Interrupt Mechanism

系统 SHALL 提供 `_interrupt_requested` 标志，在 tool 调用间隙检查并可中途退出循环。中断信号 MUST 可从外部（前端 IPC、ASR 新输入）触发。

#### Scenario: Mid-loop interrupt by new user input

- **WHEN** agent 正在执行 tool chain，前端通过 IPC 设置 `_interrupt_requested=true`
- **THEN** agent MUST 在当前 tool 执行完成后、下一次 LLM 调用前退出循环，返回包含部分结果的 dict

### Requirement: Prompt Caching Integration

Agent MUST 在 LLM 调用时启用 provider 的 prompt caching（如 Anthropic `prompt-caching-2024-07-31` beta header），并在 frozen system prompt 部分打 cache breakpoint。

#### Scenario: Cache breakpoint on frozen system

- **WHEN** agent 发起 LLM 调用
- **THEN** 请求 headers MUST 包含 `anthropic-beta: prompt-caching-2024-07-31`（provider=anthropic 时），system messages 的 frozen 部分 MUST 打 `cache_control`

### Requirement: Context Engine Hook

Agent MUST 在每轮 LLM 调用前调用 `context_engine.should_compress(prompt_tokens)`，若 true 则先 `context_engine.compress(messages)` 再调用 LLM。

#### Scenario: Compression triggered on threshold

- **WHEN** 累计 prompt_tokens 超过 `config.context.threshold_percent × context_window`（默认 75%）
- **THEN** agent MUST 调用 compress 将中间 messages 合并为单条 summary assistant message，保留 first_n=3 + last_n=6

### Requirement: Tool Call Dispatch and Error Handling

Agent MUST 通过 ToolRegistry.dispatch 调用工具，MUST 捕获所有 handler 异常并转成 tool_result message（不中断循环），MUST 区分可重试错误（网络/限流）和永久错误（参数错误）。

#### Scenario: Tool handler raises exception

- **WHEN** 某个 tool handler 抛 Python 异常
- **THEN** agent MUST 捕获，通过 `error_classifier` 判断是否可重试；构造 tool_result message 内容形如 `{"error": "...", "retriable": bool}`；循环继续进入下一轮 LLM 调用让模型自行处理错误

#### Scenario: Retriable error with exponential backoff

- **WHEN** tool 返回 `retriable=true` 且 retry count < max_retries
- **THEN** agent MUST 应用 `retry_utils.with_jitter` 指数退避后重试同一次调用

### Requirement: Memory Integration

Agent MUST 在每轮开始前通过 ContextAssembler 获取 ContextBundle（含 memory_block），MUST 在最终回复生成后异步写记忆（`memory_manager.sync_all`），写入 MUST 不阻塞 TTS 输出。

#### Scenario: Async memory write after final response

- **WHEN** agent 返回 `final_response`
- **THEN** 系统 MUST 将记忆写入丢进后台 queue，立即返回给前端，不等写入完成

### Requirement: TTS Pre-narration Hook

Agent MUST 在决定调用工具时（tool_calls 非空）通过 `_should_voice_narrate(tc)` 判断是否值得预语音化，若是则 enqueue TTS 短语（如"让我查一下..."）到播放队列，这 MUST 并行于 tool 执行以降低感知延迟。

#### Scenario: Long tool triggers narration

- **WHEN** agent 决定调用 `web_crawl` 这类预期 >500ms 的工具
- **THEN** 系统 MUST 在 tool 执行前 enqueue 一条 TTS 短语；感知首字延迟（用户听到第一个音节的时间）MUST < 500ms

#### Scenario: Fast tool skips narration

- **WHEN** agent 调用 `memory_read` 这类 <50ms 的本地工具
- **THEN** 系统 SHOULD NOT 预播（避免每轮都播扰乱），直接执行
