# P6 — Technical Design

## 总览

两个新模块，明确职责边界：

```
┌─────────────────────────────────────────────────────────┐
│           ChatOrchestrator (main.py 薄壳, ~50 行)        │
│   1. 解 provider_chain                                   │
│   2. 调 await ctx_mgr.prepare_chat_messages(_msgs)       │
│   3. 调 agent.run(messages, gate=gate, ctx_mgr=ctx_mgr) │
│   4. 转发 events 到 ws                                   │
└─────────────────────────────────────────────────────────┘
       │                              │
       ▼                              ▼
┌─────────────────────┐    ┌──────────────────────────┐
│  ContextManager     │    │  TerminationGate         │
│  (facade)           │    │  (state machine)         │
│                     │    │                          │
│  ┌──────────────┐   │    │  - allows_call(state)    │
│  │ TokenBudget  │   │    │  - allows_tool(state)    │
│  │ (B3)         │   │    │  - record_event(...)     │
│  └──────────────┘   │    │  - terminate(reason)     │
│  ┌──────────────┐   │    │                          │
│  │ResultStore   │◄──┼────┤  Drives:                 │
│  │(B1 + G1)     │   │    │   • max_turns hard cap   │
│  └──────────────┘   │    │   • tool_budget hard cap │
│  ┌──────────────┐   │    │   • wall_clock cap (NEW) │
│  │ Compactor    │   │    │   • permanent_tool_error │
│  │ (B2)         │   │    │   • all_providers_failed │
│  └──────────────┘   │    │   • context_block        │
│                     │    │   • user_interrupted     │
└─────────────────────┘    └──────────────────────────┘
       ▲                              ▲
       │                              │
       └──────────────┬───────────────┘
                      │
              ┌───────┴────────┐
              │   AgentLoop    │
              │   (refactored) │
              │                │
              │ while gate.    │
              │  allows_call:  │
              │   prep msgs    │
              │   call LLM     │
              │   dispatch tool│
              │   record event │
              │                │
              │ ~250 lines     │
              │ (down from 600)│
              └────────────────┘
```

---

## 模块 1: `TerminationGate`

### 文件: `backend/agent/termination.py`

### 数据结构

```python
from dataclasses import dataclass, field
from enum import Enum
import time

class TerminationReason(str, Enum):
    """所有可能的终止原因 — 明确枚举，不再散落。

    继承自 Claude Code SDK 的 ResultMessage.subtype 设计，
    每个值对应一种用户可观测的状态。
    """
    # 自然终止 (好)
    SUCCESS = "success"                          # stop_reason=end_turn
    USER_INTERRUPTED = "user_interrupted"        # 用户点了停止

    # 硬限制 (我们主动 break，状态可观测)
    HARD_MAX_TURNS = "error_max_turns"
    HARD_TOOL_BUDGET = "error_tool_budget"
    HARD_WALL_CLOCK = "error_wall_clock_exceeded"
    HARD_MAX_BUDGET_USD = "error_max_budget_usd"

    # 错误状态
    PERMANENT_TOOL_ERROR = "permanent_tool_error"
    ALL_PROVIDERS_FAILED = "all_providers_failed"
    CONTEXT_BUDGET_BLOCK = "context_budget_block"
    HALLUCINATION_DETECTED = "hallucination"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"

@dataclass
class GateConfig:
    """所有 hard limits 集中在这里。可被 [supervisor] / [agent] config override."""
    max_turns: int = 50
    tool_budget_hard: int = 40
    wall_clock_seconds: float = 600.0          # NEW: 10min 强制 break
    max_budget_usd: float | None = None
    per_tool_max_consecutive: int = 5          # NEW: 同 tool 连续 5 次硬 break

@dataclass
class GateState:
    """状态机内部状态。Hermes-inspired transition tracking."""
    started_at: float = field(default_factory=time.time)
    turns_used: int = 0
    tools_used: int = 0
    cost_usd: float = 0.0
    # Per-tool consecutive counter (LangGraph 教训)
    per_tool_consecutive: dict[str, int] = field(default_factory=dict)
    # Why did last iteration continue? (Hermes-inspired)
    last_transition: str = "init"
    # 一旦 terminate() 调用后置 True，幂等
    terminated: bool = False
    terminated_reason: TerminationReason | None = None

class TerminationGate:
    """集中式终止决策。AgentLoop 在每个关键节点问一次 gate.allows_*。"""

    def __init__(self, config: GateConfig | None = None) -> None:
        self.config = config or GateConfig()
        self.state = GateState()

    # 决策入口（pure 函数风格，便于测试）
    def allows_call(self) -> tuple[bool, TerminationReason | None]:
        """LLM 调用前调一次"""
        if self.state.terminated:
            return (False, self.state.terminated_reason)
        if self.state.turns_used >= self.config.max_turns:
            return (False, TerminationReason.HARD_MAX_TURNS)
        elapsed = time.time() - self.state.started_at
        if elapsed > self.config.wall_clock_seconds:
            return (False, TerminationReason.HARD_WALL_CLOCK)
        if (self.config.max_budget_usd is not None
                and self.state.cost_usd >= self.config.max_budget_usd):
            return (False, TerminationReason.HARD_MAX_BUDGET_USD)
        return (True, None)

    def allows_tool(self, tool_name: str) -> tuple[bool, TerminationReason | None]:
        """每个 tool dispatch 前调一次"""
        if self.state.tools_used >= self.config.tool_budget_hard:
            return (False, TerminationReason.HARD_TOOL_BUDGET)
        consec = self.state.per_tool_consecutive.get(tool_name, 0)
        if consec >= self.config.per_tool_max_consecutive:
            return (False, TerminationReason.HALLUCINATION_DETECTED)  # tool stuck loop
        return (True, None)

    # 状态推进
    def record_turn(self, cost_delta_usd: float = 0.0) -> None:
        self.state.turns_used += 1
        self.state.cost_usd += cost_delta_usd

    def record_tool_call(self, tool_name: str) -> None:
        self.state.tools_used += 1
        self.state.per_tool_consecutive[tool_name] = (
            self.state.per_tool_consecutive.get(tool_name, 0) + 1
        )
        # 任何 other tool reset 当前 tool 的 consecutive counter
        for other in list(self.state.per_tool_consecutive.keys()):
            if other != tool_name:
                self.state.per_tool_consecutive[other] = 0

    def record_final_answer(self) -> None:
        """LLM 输出 stop_reason=end_turn 时调"""
        self.terminate(TerminationReason.SUCCESS)

    def record_error(self, reason: TerminationReason) -> None:
        """各种 ErrorEvent → terminate"""
        self.terminate(reason)

    def terminate(self, reason: TerminationReason) -> None:
        if not self.state.terminated:
            self.state.terminated = True
            self.state.terminated_reason = reason

    def summary(self) -> dict[str, Any]:
        """For ResultMessage / WS 事件"""
        return {
            "reason": (self.state.terminated_reason.value
                       if self.state.terminated_reason else "running"),
            "turns_used": self.state.turns_used,
            "tools_used": self.state.tools_used,
            "elapsed_seconds": time.time() - self.state.started_at,
            "cost_usd": self.state.cost_usd,
        }
```

### 关键设计选择

| 选择 | 理由 | 业界先例 |
|---|---|---|
| Per-instance gate (非 global) | 多 session 并发隔离 | Hermes `IterationBudget` per-conversation |
| Hard cap 不依赖 LLM 听话 | 模型 ignore system msg 的根本治理 | Claude Code "maxTurns is non-negotiable" |
| `tuple[bool, Reason]` 返回值 | 调用方既知道能否 continue，也知道为什么停 | LangGraph `_are_more_steps_needed` |
| `wall_clock_seconds` (新增) | 解 21 分钟卡死的 user 视角问题 | 业界未见显式，但 production 常见 |
| Per-tool consecutive | 一个 degraded tool 不该耗光全局预算 | LangGraph 教训 |
| `terminate()` 幂等 | 多处可能同时 terminate（如 supervisor + max_iter），不重复发 event | Claude Code `ResultMessage` 唯一 |

---

## 模块 2: `ContextManager`

### 文件: `backend/agent/context_manager.py`

### 数据结构

```python
from agent.tool_result_truncator import (
    ToolResultRefStore,
    maybe_truncate_tool_result,
    get_global_ref_store,
)
from agent.history_compactor import compact_messages, should_compact
from agent.token_budget import check_budget, BudgetCheck

@dataclass
class ContextConfig:
    """统一所有 context 策略配置."""
    # B1 truncation
    tool_result_threshold: int = 4000
    tool_result_head: int = 1500
    tool_result_tail: int = 500
    # B1 self-awareness
    skip_truncation_for_tools: set[str] = field(
        default_factory=lambda: {"fetch_tool_result"}  # 解决 G1 引入的截 fetch 自己 bug
    )
    # B2 compaction
    compact_message_threshold: int = 20
    compact_char_threshold: int = 60_000
    compact_keep_recent: int = 6
    # B3 budget
    budget_warn_pct: float = 0.80
    budget_block_pct: float = 0.95

class ContextManager:
    """统一 context 优化 facade.

    用法（AgentLoop / chat handler）：

        ctx = ContextManager(config, summarize_fn=summarize_via_llm)

        # chat handler 入口：检查 + compaction
        msgs = await ctx.prepare_chat_messages(msgs, model=provider.model)

        # AgentLoop 每 iteration:
        budget = ctx.check_budget(msgs, model=provider.model)
        if budget.verdict == BudgetCheck.BLOCK:
            return ErrorEvent(reason="context_budget_block", detail=budget.advice)

        # tool dispatch 后：
        content_for_history, ref_id = ctx.record_tool_result(
            tool_name=tc.name, result=result_str,
        )
        working_messages.append({..., "content": content_for_history})
    """

    def __init__(
        self,
        config: ContextConfig | None = None,
        summarize_fn: Callable[[str], Awaitable[str]] | None = None,
        ref_store: ToolResultRefStore | None = None,
    ) -> None:
        self.config = config or ContextConfig()
        self.summarize_fn = summarize_fn
        # 共用 global ref store —— G1 fetch_tool_result 也用同一个
        self.ref_store = ref_store or get_global_ref_store()

    # --- B3 token budget ---
    def check_budget(self, messages, *, model: str) -> "BudgetCheckResult":
        return check_budget(
            messages, model=model,
            warn_pct=self.config.budget_warn_pct,
            block_pct=self.config.budget_block_pct,
        )

    # --- B2 history compaction ---
    async def maybe_compact(self, messages, *, llm_for_summarize) -> list:
        """Preflight compaction (Hermes 风格)."""
        if not should_compact(
            messages,
            message_threshold=self.config.compact_message_threshold,
            char_threshold=self.config.compact_char_threshold,
        ):
            return messages

        async def _summarize(text: str) -> str:
            r = await llm_for_summarize.chat_with_tools(
                [
                    {"role": "system", "content":
                        "Compress the following conversation into a concise "
                        "Chinese summary capturing: completed steps, key decisions, "
                        "tool results' outcomes, current state. ≤ 600 chars."},
                    {"role": "user", "content": text[:50_000]},
                ],
                tools=None, max_tokens=800, temperature=0.1,
            )
            return r.get("content", "")

        return await compact_messages(
            messages,
            summarize_fn=_summarize,
            keep_recent=self.config.compact_keep_recent,
        )

    # --- B1 + G1 unified tool result handling ---
    def record_tool_result(
        self, *, tool_name: str, result: str,
    ) -> tuple[str, str | None]:
        """主入口 — 同时管 truncation + ref store。

        关键 G1 fix: fetch_tool_result 自己的 result 不再被截断（无限循环根因）。
        """
        if tool_name in self.config.skip_truncation_for_tools:
            return (result, None)
        return maybe_truncate_tool_result(
            result,
            store=self.ref_store,
            threshold=self.config.tool_result_threshold,
            head_chars=self.config.tool_result_head,
            tail_chars=self.config.tool_result_tail,
        )

    # --- 高层 prepare 函数（chat handler 调一次即可） ---
    async def prepare_chat_messages(
        self,
        messages: list[dict],
        *,
        model: str,
        llm_for_summarize=None,
    ) -> list[dict]:
        """chat handler 入口的统一 prep:
          1. preflight compaction
          2. (caller 自行检查 budget — 因为 budget BLOCK 应该 ErrorEvent 短路，
             不是 ContextManager 决定)
        """
        if llm_for_summarize is not None and self.summarize_fn is not None:
            messages = await self.maybe_compact(
                messages, llm_for_summarize=llm_for_summarize,
            )
        return messages
```

### 关键设计选择

| 选择 | 理由 | 业界先例 |
|---|---|---|
| Facade pattern (单入口) | 调用方只学 1 个 API，不用知道 B1/B2/B3 内部 | LangChain Anatomy "Context Manager" |
| `skip_truncation_for_tools` 集合 | 治根 G1 bug + 未来加新"应该全保留的工具"只改 set | self-awareness 设计 |
| Reuse 已有 B1/B2/B3 模块 | 不重写测试已绿的代码 | Strangler Fig 原则 |
| `ref_store` global singleton | G1 fetch_tool_result 工具也用同一个 | 已实现 |
| Preflight compaction | Hermes 设计：50% 阈值就主动压，不等爆 | Hermes Agent |
| `summarize_fn` 注入 | 不导入 provider，方便测试 mock | 已实现 |

### 业界 "三层 recovery"（Phase 2.5，可选扩展）

如果 P6 完成后还有时间，可以加 Claude Code 风格的三层 recovery：

```python
class ContextManager:
    async def recover_from_overflow(
        self, messages, model, *, llm
    ) -> list[dict]:
        """Three-tier recovery (Claude Code inspired)."""
        # Tier 1: Local collapse drain (no LLM)
        m1 = self._collapse_drain(messages)
        if not self._still_over(m1, model):
            return m1
        # Tier 2: LLM-based compaction
        m2 = await self.maybe_compact(m1, llm_for_summarize=llm)
        if not self._still_over(m2, model):
            return m2
        # Tier 3: Fork session — return only summary + last user turn
        return await self._fork_with_summary(m2, llm)
```

P6 不强制实现 Tier 3，只 stub 接口；交给未来需要时再做。

---

## AgentLoop 重构后接口

`AgentLoop.__init__` 新增 2 个参数（可选，默认 None 保持 backward compat）：

```python
class AgentLoop:
    def __init__(
        self,
        llm_registry,
        tool_registry,
        *,
        max_iterations: int = 20,
        # ... 既有参数保留 ...
        termination_gate: TerminationGate | None = None,    # NEW
        context_manager: ContextManager | None = None,      # NEW
    ):
        self.gate = termination_gate or TerminationGate(GateConfig(
            max_turns=max_iterations,  # backward compat
        ))
        self.ctx = context_manager or ContextManager()
```

`AgentLoop.run` 重构后骨架（伪代码 ~200 行 vs 现 ~600 行）：

```python
async def run(self, messages, *, session_id, stream=False, provider_chain=None):
    working = list(messages)

    while True:
        # ─── PreCall stage ───
        ok, reason = self.gate.allows_call()
        if not ok:
            yield ErrorEvent(reason=reason.value, ...)
            return

        budget = self.ctx.check_budget(working, model=...)
        if budget.verdict == BudgetCheck.BLOCK:
            self.gate.record_error(TerminationReason.CONTEXT_BUDGET_BLOCK)
            yield ErrorEvent(reason="context_budget_block", detail=budget.advice)
            return
        elif budget.verdict == BudgetCheck.WARN:
            # 预防式 compaction
            working = await self.ctx.prepare_chat_messages(
                working, model=..., llm_for_summarize=provider_chain[0]
            )

        # ─── LLM Call stage ───
        response = await self._call_llm_with_chain(working, provider_chain)
        self.gate.record_turn(cost_delta_usd=response.cost or 0.0)

        # ─── Stop conditions ───
        if response.stop_reason == "end_turn":
            self.gate.record_final_answer()
            yield FinalEvent(...)
            return

        if response.has_permanent_error:
            self.gate.record_error(TerminationReason.PERMANENT_TOOL_ERROR)
            yield ErrorEvent(reason="permanent_tool_error", ...)
            return

        # ─── Tool Dispatch stage ───
        for tc in response.tool_calls:
            ok, reason = self.gate.allows_tool(tc.name)
            if not ok:
                yield ErrorEvent(reason=reason.value, ...)
                return
            self.gate.record_tool_call(tc.name)
            result = await self._dispatch_tool(tc)
            content_for_history, ref_id = self.ctx.record_tool_result(
                tool_name=tc.name, result=result,
            )
            working.append({"role": "tool", ..., "content": content_for_history})
```

**净减少 ~400 行**，每个 stage 职责单一可测试。

---

## 迁移路径（Strangler Fig）

```
Phase 0 (Day 1)         : 新建 termination.py + context_manager.py + tests
                          (旧代码完全不动)
Phase 1 (Day 2-3)       : 新建 P6_ENABLE_GATE 环境变量。AgentLoop 检测变量，
                          ON 时走新代码，OFF 走旧代码。Feature flag 默认 OFF。
Phase 2 (Day 4-5)       : 在 dev 开 flag。E2E 测试 + 修 bug。
Phase 3 (Day 6-7)       : flag 默认 ON。旧路径标 deprecated 但保留。
                          1 周观察期。
Phase 4 (Day 8-9)       : 移除旧路径（_TOOL_BUDGET_HARD_MSG 等 dead code）。
Phase 5 (Day 10)        : doc + archive change.
```

每 phase 独立 mergeable + 可回滚（关 flag）。

---

## 测试策略

参见 `tasks.md` 的 TDD 计划。原则：

1. **termination.py 单测**: ≥ 20 个 case，覆盖每个 TerminationReason 的触发条件 + 状态机 transition
2. **context_manager.py 单测**: ≥ 15 个 case，特别测 `skip_truncation_for_tools` (G1 fix)
3. **AgentLoop 集成测**: ≥ 10 个 case，仿真长任务 / tool budget 超 / wall_clock 超 / context block 等
4. **回归测**: 跑现有 1149 pytest，必须全绿
5. **Live E2E**: 仿真长任务（35+ iter），断言 ≤ 3 分钟收敛

---

## 参考文档（cite）

1. [Claude Code Agent Loop](https://code.claude.com/docs/en/agent-sdk/agent-loop) — `maxTurns` non-negotiable, `ResultMessage.subtype` 枚举
2. [Hermes Agent vs Claude Code](https://kenhuangus.substack.com/p/chapter-3-the-query-agent-loop-claude) — `IterationBudget`, dual-gate, 7 continue sites
3. [LangGraph ReAct create_react_agent](https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent) — `remaining_steps`, per-tool counter
4. [The Anatomy of an Agent Harness — LangChain](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness) — Context Manager 三件套（compaction + offloading + skills）
5. [Cline Context Windows](https://docs.cline.bot/model-config/context-windows) — auto-compact feature flag
6. [OpenAI Codex Agent Loop](https://openai.com/index/unrolling-the-codex-agent-loop/) — turn 概念 + stateless + prompt cache
