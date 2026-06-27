# Spec: llm-providers

## ADDED Requirements

### Requirement: Multi-Provider Adapter Abstraction

系统 SHALL 提供统一 `LLMClient` 接口，支持至少三个 cloud provider：Anthropic、OpenAI、Gemini（D1 决策）。D2 决策明确 **不支持** local LLM provider（vLLM / llama.cpp / ollama 等），v0.6.0 MVP 阶段不打进 bundle。

#### Scenario: Three providers registered

- **WHEN** 系统启动完成
- **THEN** `llm_registry.list_providers()` MUST 返回至少 `["anthropic", "openai", "gemini"]`，MUST NOT 含 `local` / `vllm` / `ollama`

#### Scenario: Unknown provider rejected

- **WHEN** config.llm.provider 配成 `"xunfei"` 等未实现 provider
- **THEN** 启动 MUST log error 并 fallback 到 `anthropic` 默认 provider，不得崩溃

### Requirement: Unified Chat Completion Interface

每个 provider adapter MUST 实现 `async def chat(messages, tools, model, **kwargs) → ChatResponse` 统一签名。`ChatResponse` MUST 含 `content, tool_calls, stop_reason, usage`（含 `input_tokens, output_tokens, cache_read_tokens, cache_write_tokens`）字段，不同 provider 的原生返回 MUST 被 normalize 到此结构。

#### Scenario: Usage fields unified

- **WHEN** 调用 Anthropic vs OpenAI vs Gemini 的 chat 完成
- **THEN** 返回 ChatResponse.usage MUST 都含相同 4 字段（input/output/cache_read/cache_write）；provider 不支持的字段 MUST 为 0，不得缺字段

#### Scenario: Tool calls normalized

- **WHEN** 任一 provider 返回 tool call（各家格式不同）
- **THEN** ChatResponse.tool_calls MUST 是统一 `[{id, name, arguments: dict}]` 列表

### Requirement: Anthropic Prompt Caching Integration

Anthropic adapter MUST 正确使用 prompt caching：system prompt 首个 block 和最后一个稳定 content block MUST 打 `cache_control={"type": "ephemeral"}` 标记。ContextAssembler 输出的 `frozen_system` 部分 MUST 是 cacheable 起点。

#### Scenario: Cache breakpoint placed at frozen boundary

- **WHEN** ContextBundle.build_messages 返回 `[frozen_system, skill_prelude, memory_block]`
- **THEN** Anthropic adapter MUST 在 frozen_system 末尾插入 cache breakpoint；memory_block 作为独立 dynamic system 消息

#### Scenario: Cache hit rate reported

- **WHEN** 连续 5 轮同 task_type 对话
- **THEN** 第 2 轮起 `response.usage.cache_read_tokens / input_tokens` MUST ≥ 80%；该比率 MUST 被记录到 decisions trace

### Requirement: Model Selection per Task

系统 MUST 支持按 task_type 或工作阶段选择模型：主对话默认 `claude-sonnet-4-5`、classifier 用 `claude-haiku-4-5`、embedder 用 BGE-M3（本地）。模型映射 MUST 在 `config.llm.models` 声明可覆盖。

#### Scenario: Haiku used for classifier

- **WHEN** TaskClassifier LLM 层触发
- **THEN** MUST 调用 `claude-haiku-4-5`，MUST NOT 使用更贵的 sonnet；latency p95 ≤ 300ms

#### Scenario: Main conversation uses sonnet

- **WHEN** agent loop 主模型调用
- **THEN** 默认 MUST 是 `claude-sonnet-4-5`（除非 config.llm.main_model 覆盖）

### Requirement: Fallback Model Chain

每次 LLM 调用 MUST 支持 fallback 链：primary 失败（5xx / rate limit / timeout）后 MUST 按 `config.llm.fallback_chain` 顺序重试下一个 provider/model。fallback 深度 MUST ≤ 2（primary + 2 backups）。

#### Scenario: Primary 5xx triggers fallback

- **WHEN** Anthropic 返回 503
- **THEN** adapter MUST 在 1s 内转到 fallback[0]（如 `openai:gpt-4o`）重试，不得让 agent loop 挂起

#### Scenario: All fallbacks exhausted returns error

- **WHEN** primary + 2 fallbacks 全部失败
- **THEN** MUST 返回明确 `LLMProviderError("all providers failed: ...")`，agent loop 的错误分类器 MUST 将其判为不可重试，以友好消息结束本轮

### Requirement: Rate Limit and Retry Handling

LLM 调用遇到 429 (rate limit) MUST 按 `Retry-After` header 或 exponential backoff（起始 1s，最多 3 次）自动重试。重试 MUST NOT 无限循环，最终失败 MUST 触发 fallback 链。

#### Scenario: 429 triggers backoff retry

- **WHEN** OpenAI 返回 429，Retry-After=2
- **THEN** adapter MUST 等 2s 后重试同一 provider；3 次仍失败 MUST 转 fallback

### Requirement: Budget Cap and Daily Quota

系统 MUST 支持 `config.llm.daily_usd_cap`（默认 10.0）软限制。累计 token 成本接近 80% MUST 在前端推 warning 通知；达 100% MUST 阻止新调用并返回 `{"error": "daily budget exceeded"}`。cost 计算 MUST 按 provider 公开单价表。

#### Scenario: Warning at 80% cap

- **WHEN** 当日累计成本 = $8 且 cap=$10
- **THEN** 下次 LLM 调用后 MUST 触发前端 IPC 事件 `llm.budget.warning`，payload 含 `used_usd, cap_usd`

#### Scenario: Block at 100% cap

- **WHEN** 累计成本已 ≥ $10
- **THEN** 任何新 LLM 调用 MUST 立即返回 error 不实际请求；次日 0 点 UTC 自动重置计数

### Requirement: Streaming Support for TTS Pre-narration

LLM adapter MUST 支持 streaming 模式（`stream=True`），MUST yield `ChatChunk` 对象含 `delta_content, delta_tool_calls, is_final`。agent loop 的 TTS 预播钩子 MUST 依赖此 stream 接口提前拿到首句播放（P4-S7 D8 决策）。

#### Scenario: First sentence yielded before full response

- **WHEN** agent 调用 `chat(stream=True)` 且 LLM 先吐出 "好的，我来查一下..." 再继续 tool call
- **THEN** stream 的第一个 chunk MUST 在 ≤ 600ms p50 到达，TTS 可开始播报

### Requirement: API Key Management and Secret Isolation

API keys MUST 通过环境变量或 keyring 读取（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`），MUST NOT 硬编码或明文存 config.toml。缺 key 的 provider MUST 被自动从 `list_providers()` 中排除。

#### Scenario: Missing key hides provider

- **WHEN** 环境无 `OPENAI_API_KEY`
- **THEN** `llm_registry.list_providers()` MUST NOT 含 `openai`；相应 fallback 链 MUST 跳过

#### Scenario: Keys never logged

- **WHEN** LLM adapter 发起请求或出错 log
- **THEN** log 中 MUST NOT 出现完整 API key，MUST 用 `sk-****last4` mask
