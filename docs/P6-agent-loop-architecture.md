# P6 — Agent Loop Architecture (post-refactor)

> Status: production (Phase 6 merged 2026-05-12).
> Replaces 14 inline P5-S2 patches with three named layers.
> See `openspec/changes/p6-agent-loop-refactor/proposal.md` for the why.

---

## Layered overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WebSocket chat handler  (backend/main.py)                              │
│  ─ unpacks user message + session state                                  │
│  ─ builds initial messages list                                          │
│  ─ resolves provider chain                                               │
│                                                                          │
│              ┌──────────────────────────┐                                │
│              │   ContextManager (ctx)   │  preflight compaction          │
│              │   chat_prep.prepare_*    │  (history → summarized form)   │
│              └────────┬─────────────────┘                                │
│                       ▼                                                  │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  AgentLoop.run()                                                    │  │
│  │  ─ owns ContextManager + TerminationGate references                 │  │
│  │  ─ for iteration in 1..max_iterations:                              │  │
│  │      1. gate.allows_call() ─ hard cap pre-flight                    │  │
│  │      2. ctx.check_budget() ─ token budget guard                     │  │
│  │      3. LLM call (single provider OR chain walk)                    │  │
│  │      4. gate.record_turn()                                          │  │
│  │      5. if stop_reason != tool_use: gate.record_final_answer();     │  │
│  │             yield FinalEvent; return                                │  │
│  │      6. for tc in tool_calls:                                       │  │
│  │            gate.allows_tool(tc.name) ─ per-tool hallucination check │  │
│  │            gate.record_tool_call()                                  │  │
│  │            dispatch + yield ToolResultEvent                         │  │
│  │            ctx.record_tool_result() ─ truncate + ref-store          │  │
│  │  ─ on chain failure → gate.record_error(ALL_PROVIDERS_FAILED)        │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                       ▲                          ▲                       │
│           uses        │                          │   uses                │
│              ┌────────┴──────────┐    ┌──────────┴─────────────┐         │
│              │  TerminationGate  │    │    ContextManager      │         │
│              │  (hard caps)      │    │    (B1+B2+B3+G1 fix)   │         │
│              │  ─ allows_call    │    │    ─ check_budget      │         │
│              │  ─ allows_tool    │    │    ─ maybe_compact     │         │
│              │  ─ record_turn    │    │    ─ record_tool_result│         │
│              │  ─ record_error   │    │    ─ ref_store ()      │         │
│              │  ─ summary()      │    └────────────────────────┘         │
│              └───────────────────┘                                       │
│                                                                          │
│  ProviderAdapter (OpenAICompatibleProvider, anthropic_adapter)           │
│  ─ httpx wire layer; not touched by P6                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

Layer responsibilities, top-down:

| Layer | File | Job |
|---|---|---|
| **ChatOrchestrator** (chat handler) | `backend/main.py` | WS plumbing, session lookup, provider chain resolution, plan extraction, completion probe wiring |
| **ContextManager** | `backend/agent/context_manager.py` | All "what messages go into the LLM call" decisions: compaction, budget, tool-result truncation, ref-store retrieval |
| **AgentLoop** | `backend/agent/agent_loop.py` | The ReAct loop itself: LLM call → tool dispatch → repeat. Owns the gate + ctx instances and threads events out. |
| **TerminationGate** | `backend/agent/termination.py` | All "should this loop continue" decisions: hard turn cap, wall-clock, tool budget, per-tool consecutive (hallucination), cost cap |
| **ProviderAdapter** | `backend/llm/openai_compatible.py` (and friends) | Wire protocol. Out of P6 scope (`proposal.md` §"Non-goals"). |

---

## Termination flow — when and why does a run stop?

`TerminationReason` is the single enum that drives loop exit. Every code path that wants to stop the loop **must** go through `gate.record_*()` so callers reading `gate.summary()` after the run see a coherent reason.

| TerminationReason | Triggered by | Where in run() |
|---|---|---|
| `SUCCESS` | LLM returns `stop_reason != "tool_use"` (or with no tool_calls) → `gate.record_final_answer()` | Top of the "end of conversation" branch after the LLM call |
| `HARD_MAX_TURNS` | `gate.state.turns_used >= config.max_turns` at next `allows_call()` | Top of every iteration |
| `HARD_TOOL_BUDGET` | `gate.state.tools_used >= config.tool_budget_hard` at next `allows_tool()` | Before each `dispatch` inside the tool-call loop |
| `HARD_WALL_CLOCK` | `time.time() - gate.state.started_at >= config.wall_clock_seconds` at any `allows_*()` | Both gates (pre-LLM and pre-tool) |
| `HARD_MAX_BUDGET_USD` | `gate.state.cost_usd >= config.max_budget_usd` at any `allows_*()` | Both gates (pre-LLM and pre-tool) |
| `HALLUCINATION_DETECTED` | `gate.state.per_tool_consecutive[name] >= config.per_tool_max_consecutive` at next `allows_tool(name)` | Before each dispatch — per-tool counter resets when a different tool is called (LangGraph lesson) |
| `CONTEXT_BUDGET_BLOCK` | `ctx.check_budget(messages) == BLOCK` (tokens > context_window) | Top of every iteration, before LLM call. Loop records error on gate then yields `ErrorEvent`. |
| `ALL_PROVIDERS_FAILED` | Chain mode: every provider in `provider_chain` raised `LLMProviderError` | After the `for prov in chain:` walk |
| `PERMANENT_TOOL_ERROR` | A dispatched tool returned classified `PermanentToolError` | After all tools in a turn finish dispatching (in the classification pass) |

A run can also exit by raising — most notably `LLMBudgetExceededError` from the day-budget guard — but those bypass the gate and yield a domain-specific `ErrorEvent(reason="budget_exceeded")` instead.

---

## Context flow — how does ContextManager keep the working_messages lean?

ContextManager wraps three pre-existing helpers behind a single facade. Each `AgentLoop.run()` iteration uses three of its four methods.

```
Caller has working_messages = [...].
  │
  ▼
ctx.check_budget(messages, model=...)        ── B3, pre-flight token estimate
  │                                              returns BudgetCheckResult
  │                                              (verdict ∈ {OK, WARN, BLOCK}, advice, ratio)
  │       ↳ BLOCK → loop yields ErrorEvent and stops
  │       ↳ WARN  → loop logs once, continues
  │       ↳ OK    → continues silently
  ▼
LLM call → tool_calls
  ▼
for each tool result:
  ctx.record_tool_result(tool_name=..., result=...)  ── B1 + G1 unified
    │
    │  If tool_name ∈ ctx.config.skip_truncation_for_tools (default {"fetch_tool_result"}):
    │      → return (result_verbatim, None)            ── G1 fix
    │  Elif len(result) > ctx.config.tool_result_truncate_at:
    │      → return (head+marker+tail, ref_id) and stash full body in global ref store
    │  Else:
    │      → return (result, None)
    ▼
append to working_messages as role="tool"
```

The fourth method, `ctx.maybe_compact(messages, summarize_fn=...)`, runs **before** AgentLoop's run() is invoked, inside `chat_prep.prepare_chat_messages_for_chain`. It implements the B2 history compaction: when `len(messages) > 20` (or estimated tokens > threshold), the middle of the history is replaced with a single summarized system message. Failure returns the original list — we'd rather feed long context than break the conversation.

`get_global_ref_store()` is shared module-singleton state. The `fetch_tool_result` tool (registered in `backend/tools/code/fetch_tool_result.py`) reads from the same store; that's how the LLM follows a `[truncated, ref_id=abc12345]` marker back to the original body.

---

## Extension points

### Adding a new TerminationReason

1. Add the enum value to `TerminationReason` in `backend/agent/termination.py`.
2. Decide who fires it: a `gate.record_error(NEW_REASON)` call from inside `AgentLoop.run()`, OR a new check inside `allows_call`/`allows_tool`. The latter is preferred when the condition is geometric (counter, time) rather than event-driven (provider error, classified tool result).
3. If it's a hard cap (counter-based), add the threshold to `GateConfig` and the counter to `GateState`.
4. Update the table in this doc.

Do **not** scatter new termination logic into `main.py` or `agent_loop.py` outside `gate.*` calls — that's the architecture debt P6 set out to pay down.

### Adding a new context optimization layer

1. Add the method to `ContextManager` in `backend/agent/context_manager.py`.
2. If it has tunable thresholds, add them to `ContextConfig` with sensible defaults; never read `os.environ` directly inside the manager.
3. Call sites either go in `AgentLoop.run()` (per-iteration mutations like `record_tool_result`) or in `chat_prep.prepare_chat_messages_for_chain` (one-shot preflight mutations like `maybe_compact`).
4. The shared module-singleton `get_global_ref_store()` is the right place to stash bodies that need to live across iterations.

Avoid bolting context logic onto `AgentLoop` directly — the whole point of the manager is that the loop calls one method per concern.

### Adding a new provider error class

ProviderAdapter handles wire-level errors and surfaces them as `LLMProviderError` (transient) or `LLMBudgetExceededError`. Don't catch them inside `AgentLoop.run()` — the chain-mode walk does that already. If you need a third class, raise it from the provider, then teach the chain walker to record a new `TerminationReason` on the gate.

---

## Decision citations

See `docs/P6-migration-decisions.md` for the why of each major choice. Highlights:

* Hard caps non-negotiable → Claude Code `maxTurns` precedent.
* Per-tool consecutive counter (not global) → LangGraph lesson about retry budgets.
* Preflight compaction (not reactive) → Hermes Agent design.
* `skip_truncation_for_tools` set on ContextManager → G1 fix, "tool offloading must be self-aware" (LangChain Anatomy).
* Strangler Fig + feature flag → de-risked rollout; the flag is now permanently on (Phase 6).
