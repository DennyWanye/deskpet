# STATUS — Agent Loop 架构调研档

> 本档回答一个问题：**桌宠收到一句话后，agent 是怎么把一个任务跑完的？**
> 聚焦后端 ReAct 执行引擎（P6 重构后现状）。细节散在各 `plans/` 与 `openspec/`，本档只做一页式骨架 + 关键代码引用。
>
> 最后更新：2026-06-21 ｜ 调研基线：读码核实（master）｜ 主入口 [`backend/agent/agent_loop.py`](../backend/agent/agent_loop.py)
>
> 配套优化方案见 [`AgentImprovements.md`](./AgentImprovements.md)（缺陷审计 + 业界对标 + 落地路线）。

---

## 0. 一句话概括

桌宠的任务执行是一个 **ReAct 循环**：`LLM 出招 → 要用工具就并发分发工具 → 工具结果喂回去 → 再问 LLM`，循环往复，直到 LLM 不再要工具（给最终答案）或撞到某个硬上限。核心就一个文件 `agent_loop.py` 的 `AgentLoop.run()`，外加两个协作层（终止裁决 + 上下文管理）和四道「防假装完成」守门。

---

## 1. 三层架构（P6 重构，2026-05-12 合入）

P6 把原来散落在 `main.py` 的 14 个 P5-S2 补丁收敛成三个命名层。参考 [`docs/P6-agent-loop-architecture.md`](../docs/P6-agent-loop-architecture.md)。

| 层 | 文件 | 职责 |
|---|---|---|
| **ChatOrchestrator**（chat handler） | `backend/main.py` | WS 收消息、查 session、构建初始 messages、解析 provider 链、装配并调用 AgentLoop、事件→WS 转发 |
| **AgentLoop** | `backend/agent/agent_loop.py` | ReAct 循环本体：LLM 调用 → 工具分发 → 重复。持有 gate + ctx 实例，流式 yield 事件 |
| **TerminationGate** | `backend/agent/termination.py` | 所有「该不该继续」的硬上限裁决（轮数 / 墙钟 / 工具预算 / 花费 / per-tool 幻觉） |
| **ContextManager** | `backend/agent/context_manager.py` | 所有「哪些消息进 LLM」的决策（预算检查 / 压缩 / 工具结果截断 + ref-store） |
| **ProviderAdapter** | `backend/llm/openai_compatible.py` 等 | httpx 线缆层，P6 不动 |

> 扩展规矩：新的终止原因只走 `gate.record_*()`，新的上下文优化只加到 `ContextManager` —— **不要**再往 `main.py`/`agent_loop.py` 里散逻辑（这正是 P6 要还的债）。

---

## 2. AgentLoop 主循环执行流程

`AgentLoop.run()` 是个 `async generator`，对 `range(1, max_iterations+1)` 迭代（Companion 默认 16，Code 模式 50）。yield 出 `AgentEvent` 流：`assistant_message` / `assistant_delta`（流式 token）/ `tool_call` / `tool_result` / `final` / `error` / `provider_chain_fallback`。

**循环前（一次性）**：若 session 有活跃目标，注入一条常驻 `[目标锚定]` system 消息（WI-4a always-on，永不被压缩、整轮恒 ≤1 条，防任务漂移）。见 [agent_loop.py:683-690](../backend/agent/agent_loop.py)。

**每一轮**（`agent_loop.py:728` 起 `for iteration in range(...)`）：

```
1. gate.allows_call()          ── 硬上限预检（轮数/墙钟/花费超 → ErrorEvent + return）
2. ctx.check_budget()          ── token 预算守门（BLOCK→退出并记 gate；WARN→记一次日志）
   + compressor 压缩(可选)      ── 数 working_messages 真实 token，过阈值压历史中段
   |                              （压缩前可选 pre-flush 任务态进 L1 文件记忆，跨 session 记任务）
   + self-check 注入            ── 第 10/20/30 轮注入递进式「该收尾了」system 提醒
3. LLM 调用                    ── 单 provider OR provider_chain 逐个 walk
   |                              （transient 失败 yield ProviderChainFallbackEvent 切下一个；
   |                               全挂 → ALL_PROVIDERS_FAILED；stream 失败回落非流式）
4. gate.record_turn()          ── 轮数++、累加 cost；记录 relay 真实 prompt_tokens 喂下轮压缩判定
5. yield AssistantMessageEvent

   ┌─ 若 stop_reason != "tool_use"（模型想收尾）→ 走【第 4 节·四道守门】
   |    全过 → gate.record_final_answer() + yield FinalEvent + return  ✅
   |    任一不过 → 注入 system 提醒 + continue（重新迭代）
   |
   └─ 否则（要用工具）：
        6a. 签名重复检测          ── 连续 ≥3 次同名同参 → 抑制分发 + 注入 nudge，continue
        6b. asyncio.gather 并发分发所有 tool_calls
            每个走 _dispatch_one：gate.allows_tool() → execute_tool()/dispatch()
        6c. 逐个结果：tool_path 录制 → ctx.record_tool_result(截断+ref-store)
            → yield ToolResultEvent → append role=tool 回 working_messages
        6d. 分类结果：PermanentToolError → gate.record_error + ErrorEvent + return
            Transient → 落下一轮让模型重试

循环耗尽 max_iterations → gate.record_error(HARD_MAX_TURNS) + ErrorEvent(max_iterations)
```

关键点：循环**不碰网络也不碰 WS**，只 yield 事件；网络在 ProviderAdapter，WS 转发在 main.py。工具分发是**并发**的（`asyncio.gather`，`agent_loop.py:2063`）。

---

## 3. 工具注册与分发（`backend/deskpet/tools/`）

### 3.1 注册：单例 ToolRegistry + auto-discovery

- `ToolRegistry` 是模块级单例（[registry.py](../backend/deskpet/tools/registry.py)），内部 `_tools: dict[str, ToolSpec]` + 线程锁。
- `ToolSpec`（frozen）字段：`name` / `toolset`（分组，如 file/web/memory/control）/ `schema`（OpenAI function 格式）/ `handler` / `permission_category`（7 类）/ `concurrency_safe` / `timeout_seconds`（默认 60s）/ `replace_allowed`。
- **auto-discovery**：[`tools/__init__.py`](../backend/deskpet/tools/__init__.py) 用 `pkgutil.iter_modules` 遍历包，逐个 import，各工具模块在顶层 `registry.register(...)` 自注册；单个失败不中断。
- 同名冲突且双方都没 opt-in `replace_allowed` → 抛 `ToolNameConflictError`。
- `schemas(enabled_toolsets=...)` 导出 OpenAI 格式 `[{"type":"function","function":{...}}]`，多层过滤：`requires_env` → `enabled_toolsets` 白名单 → `disabled_toolsets`（严格禁用）→ `disabled_toolsets_schema_only` → `dangerous_tools_allowlist`。ContextAssembler 每轮决定 `enabled_toolsets`，只有子集对 LLM 可见。

### 3.2 分发：`execute_tool`（v2）vs `dispatch`（legacy）

AgentLoop 优先用 `execute_tool`（检测 `callable(execute_tool)`），它返回 **v2 envelope** `{"ok": bool, "result": str|None, "error": str|None}`。全链路（registry.py:618 起）：

```
查工具 → disabled_toolsets 检查 → 熔断器 can_call → 权限 gate.check
  → 会话上下文合并(_project_root 等注入) → handler 执行(async 直接 await / sync 丢 executor)
  → asyncio.wait_for 超时保护 → 结果 JSON 序列化 → envelope 包装
  → (可选)artifact 信息注入 → (可选)emit_receipt 发凭证 → 熔断器 record_call
```

`dispatch`（legacy sync）则**无权限校验、无熔断、无凭证**，直接返回 JSON 字符串 `{"error":..., "retriable": bool}`。

并发策略：`partition_dispatch` 按 `concurrency_safe` 分组 —— 安全工具 `gather` 并发，不安全工具按序串行，结果顺序与输入一致。

### 3.3 权限弹窗 gate（异步 Future + WebSocket）

- [`permissions/gate.py`](../backend/deskpet/permissions/gate.py) 的 `PermissionGate.check()` 五层决策栈：
  **auto-mode 短路** → 敏感路径升级（read_file 命中 .ssh/.env/cookies 等正则 → read_file_sensitive）→ config deny 列表 → default-allow（read_file 免弹）→ 会话缓存命中 → **用户弹窗**。
- 权限粒度：按 **category**（7 类：read_file / read_file_sensitive / write_file / desktop_write / shell / network / mcp_call / skill_install），缓存键含 **params 形状哈希**（不含值）——「允许 shell」后同形状操作免重复弹窗。
- **IPC 机制**（关键）：后端 `_permission_responder`（main.py:499 附近）建一个 `asyncio.Future` 注册进 `_permission_pending[request_id]`，via WS 发 `permission_request` 给 Tauri，然后 `await fut` 挂起协程；前端回 `permission_response` 时 main.py 查到 future 并 `set_result` 唤醒。60s 超时默认 deny。
- `auto_mode`（一键全允）持久化到 `permissions_auto_mode.json`，启动时恢复。

### 3.4 错误分类 + 熔断 + 动态搜索

- [`error_classifier.py`](../backend/deskpet/tools/error_classifier.py)：ValueError/TypeError/KeyError 等程序员错误 → `retriable=False`（PermanentToolError）；ConnectionError/TimeoutError/OSError → `retriable=True`（TransientToolError）；未知默认可重试。AgentLoop 的 `_classify_tool_result` 据此决定退出还是让模型重试。
- 熔断器 [`circuit_breaker.py`](../backend/agent/circuit_breaker.py)：CLOSED→（连续失败 3）→OPEN→（冷却）→HALF_OPEN→（probe）→CLOSED。OPEN 时返回中文 hint + 备选工具。
- `tool_search` 元工具 [`tool_search.py`](../backend/deskpet/tools/tool_search.py)：初始 curated toolset 不够时，LLM 按关键词搜全注册表（含 env-gated 隐藏工具），返回匹配 schema 供后续调用。

---

## 4. 四道「防 LLM 假装完成」守门

这是桌宠区别于裸 ReAct 的核心 —— 模型说「我做完了」时**不直接信**，`stop_reason != "tool_use"` 后依次过守门，任一不过就注入 system 提醒 + `continue`。对应 CLAUDE.md 反复强调的「压制 LLM 短路径偏置」在代码层落地。

| 守门 | 代码位置 | LLM 调用 | 输入 → 判定 | 不过怎么办 | 预算 |
|---|---|---|---|---|---|
| **completion_probe** | agent_loop.py:1325 | ✗ 纯规则 | 查 SessionDB code todos，status ∉ {completed,cancelled} 即未完成 | 注入「还剩 N 项 todo」system | 2 次 nudge |
| **VerifyGate** | agent_loop.py:1402 | ✓ 仅 ephemeral 救援 | regex 从 assistant_text 抽 claim → 对 receipt ledger 严格对账 | 注入 D8-schema rebound；2 次失败/stagnation→ephemeral 子代理；再不过 `verify_exhausted` 强退 | 2 nudge + 1 ephemeral |
| **goal_checker** | agent_loop.py（goal 块） | ✓ 每次 1 调 | LLM-as-judge：goal_text + 最近 5 轮 assistant 摘要 → `{done, hint}` | 注入 hint system | SessionGoal.max_iterations(默认10) |
| **external_evaluator** | agent_loop.py:1775 | ✓ 高后果 1 调 | 仅 high-consequence goal 触发；跨人格 QA 评质量分 0-10 + verdict | verify_exhausted 前最后救援；revise 则拦 | 成本护栏（<10% 目标触发） |

补充细节：

- **VerifyGate**：`mode ∈ {off, shadow, strict}`，off=总 pass（BC）。claim 提取基线见 [`STAGE0-claim-baseline.md`](../plans/2026-05-23-tool-last-mile-upgrade/STAGE0-claim-baseline.md)，RegexExtractor 带 ReDoS 防护。对账规则：claim 的 `pattern_id` 反查 `tool_hint`，ledger 须有 `tool_name ∈ tool_hint 且 ok=True` 的 receipt。
- **N1 信任面**：[`receipt_store.py`](../backend/deskpet/tools/receipt_store.py) `load_session` 时对每条 receipt 强制 **HMAC 验签**，sig-invalid 整条剔除（防伪造凭据骗过对账）。HMAC key 走 OS keystore（DPAPI/Keychain）→ 裸文件 fallback。
- **goal_checker** 与 **external_evaluator** 都是 safe-fail：LLM 异常/parse 失败 → 降级（goal_checker 返 skipped 不默认通过；external_evaluator `conservative_on_error=True` 高后果时返 revise 保守拦）。
- 守门相关源码：[`verify_gate.py`](../backend/deskpet/agent/verify_gate.py)、[`goal_checker.py`](../backend/deskpet/agent/goal_checker.py)、[`external_evaluator.py`](../backend/deskpet/agent/external_evaluator.py)、[`goal_store.py`](../backend/deskpet/agent/goal_store.py)。

---

## 5. TerminationGate 内部实现

[`termination.py`](../backend/agent/termination.py)。`TerminationReason` 是唯一退出枚举，任何想停循环的路径都得调 `gate.record_*()`，这样事后读 `gate.summary()` 拿到连贯原因。

**GateConfig 阈值**（注意默认值经多轮调参后基本「禁用上限、靠幻觉检测兜底」）：

| 字段 | 默认 | 含义 |
|---|---|---|
| `max_turns` | 10000 | 轮数硬上限（原意禁用，保留硬切手段） |
| `tool_budget_hard` | 10000 | 工具调用总数硬上限 |
| `wall_clock_seconds` | `None` | 墙钟上限，None=禁用（用户要长任务一直跑） |
| `max_budget_usd` | `None` | 花费上限，None=无限 |
| `per_tool_max_consecutive` | **8** | 同工具**同参数**连续上限 → 真死循环防御主力 |

**GateState 计数器**：`turns_used` / `tools_used` / `cost_usd` / `per_tool_consecutive`（dict）/ `_per_tool_last_sig`（args 16 字符 MD5）/ `started_at` / `terminated` / `terminated_reason`。

**核心方法**：
- `allows_call()`：LLM 调用前查 已终止 / max_turns / wall_clock / max_budget。
- `allows_tool(name)`：分发前查 已终止 / tool_budget_hard / per-tool 连续 ≥8 → `HALLUCINATION_DETECTED`。
- `record_tool_call(name, args)`：**args-aware** —— 同工具同 args 签名 → 计数++；不同 args/首次 → 重置为 1；**调任何其他工具 → 其余工具计数全清零**（LangGraph 教训：「读 5 个不同文件」不该被误判死循环，只有「读同一文件 8 次」才算）。
- `record_turn(cost_delta)` / `record_final_answer()`（=terminate SUCCESS）/ `record_error(reason)` / `terminate()`（幂等）/ `summary()`。

**TerminationReason 全枚举**：`SUCCESS` / `USER_INTERRUPTED` / `HARD_MAX_TURNS` / `HARD_TOOL_BUDGET` / `HARD_WALL_CLOCK` / `HARD_MAX_BUDGET_USD` / `PERMANENT_TOOL_ERROR` / `ALL_PROVIDERS_FAILED` / `CONTEXT_BUDGET_BLOCK` / `HALLUCINATION_DETECTED` / `CIRCUIT_BREAKER_OPEN`。

真死循环三层防御：① per-tool args-aware 连续 8 次 ② ContextManager token budget（上下文过大天然中止）③ supervisor watchdog（盯 running 但无事件的真卡死）。

---

## 6. ContextManager 内部实现

[`context_manager.py`](../backend/agent/context_manager.py)，把 B1/B2/B3/G1 四个上下文优化收在一个 facade 后。阈值多按 model 的 context_window 动态算（v2 模式）。

**关键配置**（默认）：`tool_result_head=2500` / `tool_result_tail=800` / `skip_truncation_for_tools={"fetch_tool_result"}`（**G1 fix**）/ `compact_keep_recent=12` / `budget_warn_pct=0.80` / `budget_block_pct=0.95`。动态属性：`compact_at_tokens = window×0.75`、`tool_result_threshold = clamp(window//60, 6k, 12k)`、`compact_message_threshold = max(20, window//10k)`。

**三个主方法**：
- `check_budget(messages, model)` → `BudgetCheckResult{verdict(OK/WARN/BLOCK), estimated_tokens, context_window, ratio, advice}`。CJK-aware token 估算（汉字≈1 token，ASCII≈3.5 char/token）；window 解析：显式注入 → BUILTIN per-model 表 → legacy 表 → 8192 兜底。`ratio≥0.95→BLOCK`、`≥0.80→WARN`。
- `record_tool_result(tool_name, result)` → `(content_for_history, ref_id|None)`。若 tool ∈ skip 白名单（fetch_tool_result）→ 原样返回不截断（**G1 fix**：否则 fetch 回来的全文又被截，无限循环）；否则超 `tool_result_threshold` 就 `head + [truncated N chars; ref_id=xxx — use fetch_tool_result] + tail`，全文进 ref-store。
- `maybe_compact(messages, llm_for_summarize)`：在 **AgentLoop 之前**于 `chat_prep.prepare_chat_messages_for_chain` 调一次。`should_compact`（消息>阈值 或 字符>阈值）→ 保留 system 头 + 最近 keep_recent 尾，中段用 LLM 压成一条中文摘要 system 消息（≤600 字）。失败 → 返回原列表（宁可长也不丢历史）。

**全局 ref-store**（[`tool_result_truncator.py`](../backend/agent/tool_result_truncator.py)）：`get_global_ref_store()` 模块单例（LRU 256 + 磁盘 spill 到 `<user_data>/cache/tool_refs/<ref>.txt`）。两个用方共享：`record_tool_result` 写入截断全文；`fetch_tool_result` 工具按 ref_id 读回（支持切片）。单例而非按 session 隔离 → fetch 工具无需 session 参数，随机 8 字符 ref_id 即足够安全。

---

## 7. main.py 如何装配这一切

WS handler `/ws/control`（main.py:3976），`chat` / `chat_v2` 消息走统一 tool_use loop。

**用户消息 → AgentLoop 之间的预处理链**（`_run_chat`，main.py:5446 起）：
用户消息持久化(SessionDB+向量库) → **能力门控**（classify_request 拒绝图像/视频/3D 等无能力请求，防漂移）→ **ContextAssembler.assemble**（长期记忆+技能+MCP 工具描述装入消息栈）→ 哨兵文本处理（`<<auto_resume>>` 等换成明确续跑指令）→ supervisor hint 注入 → LLM 提示调优 → ContextManager 构造 → **provider 链解析** → pre-flight 历史压缩（`prepare_chat_messages_for_chain`）。

**provider 链解析**（main.py:5551 起，[`resolution.py`](../backend/llm/resolution.py)）：`resolve_provider_for_session()` 读 SessionDB 的 per-session binding 行 → 有绑定且 provider 启用 → 单元素链 `[provider]`；无绑定 → `registry.get_chain()` 全局链（按优先级）；binding 被删/禁用 → 自动恢复全局链；`preferred_model` 覆盖 entry.model（仅内存）。每个 entry 包成 `OpenAICompatibleProvider`。registry/sdb 为空 → 降级单 provider（`_provider_chain=None`）。

**装配工厂 `build_agent()`**（main.py:851 起，调用处 6279 附近）。注入参数开/关：

| 注入项 | 状态 | flag / 来源 |
|---|---|---|
| llm_registry / tool_registry / context_manager | ✅ 总是 | — |
| max_iterations | ✅ 总是 | Companion 16 / Code 50 |
| completion_probe / max_completion_nudges=2 | ✅ 总是 | 闭包查 SessionDB todos |
| signature_repeat_threshold | ✅ 总是 | `[supervisor] tool_signature_repeat_threshold`（默认 3） |
| verify_gate / receipt_store / max_verify_nudges | 条件 | `[tools.verifier] verify_gate_mode`（默认 off）+ `[tools.receipt] emit_receipts`；receipt getter 失败 → verify_gate 也强制 None |
| structured_reflection | 条件 | `[tools.verifier] structured_reflection`（默认 False） |
| external_evaluator | 条件 | `[tools.verifier] external_evaluator`（默认 False） |
| session_goal_store / goal_checker / compressor / skill_loader / skill_matcher / tool_path_recorder | 条件 | 从 `service_context.get(...)` 取，启动时 lifespan 构造 |
| file_memory | 条件 | 模块级全局 |

> 默认配置（flag 多 off）下，agent 跑的是「裸 ReAct + 硬 gate + 上下文管理 + completion_probe」；verify_gate / goal_checker / external_evaluator 是逐步加固的可选守门。

**事件 → WS 转发**（main.py:6317 起 `async for ev in _agent.run(...)`）：`AssistantDeltaEvent→chat_v2_delta`、`AssistantMessageEvent→chat_response`（带 tool_calls 才发）、`ToolCallEvent→tool_use_event+tool_call`、`ToolResultEvent→tool_use_event+tool_result`、`FinalEvent→chat_v2_final`、`ErrorEvent→`（先判 auto-resume，不可恢复才 `chat_v2_error`）。各事件同步入 SessionDB。

**Recovery 三层防守**：① WS 连接异常捕获（benign ws-close 静默返回）② Auto-Resume（[`auto_resume.py`](../backend/agent/auto_resume.py)：ErrorEvent reason 命中可恢复集 → orchestrator 起新 task，suppress 弹窗）③ Supervisor hint follow-up（task done 后 nudge_queue 有料 → 起 `<<supervisor_followup>>` 合成 task）。所有 chat turn 都 fire-and-forget 跑在后台 task，保证 WS recv loop 能持续处理 permission_response。

---

## 8. 关键文件速查

| 关注点 | 文件 |
|---|---|
| ReAct 主循环 | [`backend/agent/agent_loop.py`](../backend/agent/agent_loop.py) |
| 终止裁决 | [`backend/agent/termination.py`](../backend/agent/termination.py) |
| 上下文管理 | [`backend/agent/context_manager.py`](../backend/agent/context_manager.py) ｜ [`token_budget.py`](../backend/agent/token_budget.py) ｜ [`tool_result_truncator.py`](../backend/agent/tool_result_truncator.py) ｜ [`history_compactor.py`](../backend/agent/history_compactor.py) |
| 工具注册/分发 | [`backend/deskpet/tools/registry.py`](../backend/deskpet/tools/registry.py) ｜ [`error_classifier.py`](../backend/deskpet/tools/error_classifier.py) ｜ [`tool_search.py`](../backend/deskpet/tools/tool_search.py) |
| 权限 gate | [`backend/deskpet/permissions/gate.py`](../backend/deskpet/permissions/gate.py) |
| 守门 | [`verify_gate.py`](../backend/deskpet/agent/verify_gate.py) ｜ [`goal_checker.py`](../backend/deskpet/agent/goal_checker.py) ｜ [`external_evaluator.py`](../backend/deskpet/agent/external_evaluator.py) ｜ [`receipt_store.py`](../backend/deskpet/tools/receipt_store.py) |
| 装配 + WS | `backend/main.py`（`_run_chat` / `build_agent` / provider 解析 / 事件转发） |
| 架构原文档 | [`docs/P6-agent-loop-architecture.md`](../docs/P6-agent-loop-architecture.md) ｜ [`docs/P6-migration-decisions.md`](../docs/P6-migration-decisions.md) |

> ⚠️ 本档行号为调研当时（2026-06-20）的近似定位，`main.py` 体量大、改动频繁，引用 main.py 处请以函数名（`_run_chat` / `build_agent` / `_permission_responder`）grep 为准。
