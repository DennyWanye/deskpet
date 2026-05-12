# P6 — Migration Decisions

> One-page decision record for the AgentLoop / ContextManager / TerminationGate
> refactor merged in Phase 6 (2026-05-12). Each entry: the choice, the
> alternative, and the source we cribbed from. Read alongside
> `openspec/changes/p6-agent-loop-refactor/proposal.md` (the "why we
> needed the refactor at all") and `docs/P6-agent-loop-architecture.md`
> (the "what we built").

---

## 1. Hard caps are non-negotiable (system code enforces, not the LLM)

**Decision.** TerminationGate's `max_turns`, `tool_budget_hard`,
`wall_clock_seconds`, `max_budget_usd`, and `per_tool_max_consecutive`
are all enforced by Python code in `allows_call()` / `allows_tool()`.
When the gate refuses, the loop yields `ErrorEvent` and `return`s. The
LLM cannot override or argue.

**What we rejected.** The P5-S2 era approach: inject a "[TOOL BUDGET
EXHAUSTED — stop now]" system message and hope the next LLM response
sets `stop_reason="end_turn"`. In practice the model frequently ignored
the message and kept calling tools — `tools_used` reaching 65 in one
observed 21-minute runaway loop.

**Source.** Anthropic's Claude Code SDK documents `max_turns` as a
hard cap. Termination yields `ResultMessage.subtype ∈ {success,
error_max_turns, error_max_budget_usd, error_during_execution,
error_max_structured_output_retries}` — explicit enumerated states,
not "the LLM eventually decided to stop."
<https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-headless>

Hermes Agent (chinzy/Hermes-405) reinforces this with `IterationBudget` —
thread-safe, **non-negotiable**, shared by parent + subagent runs.
<https://github.com/NousResearch/Hermes-Function-Calling> (see
`IterationBudget` in the agent loop module).

---

## 2. Per-tool consecutive counter, not a global retry counter

**Decision.** `gate.state.per_tool_consecutive` is a `dict[str, int]`.
When `record_tool_call("write_file")` is called five times in a row, the
sixth `allows_tool("write_file")` returns `(False, HALLUCINATION_DETECTED)`.
A different tool name resets the counter to zero.

**What we rejected.** A single global "retries since last success"
counter. Simple, but it misclassifies legitimate work: an agent that
alternates `read_file` → `grep` → `read_file` × 20 over a debug session
is fine; we don't want to interrupt it. Only **same tool, consecutively**
points at the model getting stuck.

**Source.** LangGraph's ReAct prebuilt has documented this trade-off:
their `recursion_limit` is global but they recommend per-tool retry
budgets for hallucination detection.
<https://langchain-ai.github.io/langgraph/concepts/agentic_concepts/#recursion-limit>
The "agent hits the same tool with the same args three times in a row"
pattern is the canonical infinite-tool-loop signature in their issue
tracker.

We applied this to `per_tool_max_consecutive=5` (default) — a generous
bound that still catches the "write_file × 30 trying to fix one syntax
error" failure mode we observed in P5-S2.

---

## 3. Preflight compaction, not reactive compaction

**Decision.** `ContextManager.maybe_compact()` runs **before** the API
call, gated on `len(messages) > 20` (or estimated tokens > threshold).
Inside `AgentLoop.run()`, `ctx.check_budget()` runs at the top of each
iteration so we surface `context_budget_block` as our own
`ErrorEvent` rather than waiting for the provider to return a
`context_length_exceeded` 400.

**What we rejected.** Reactive compaction (run only when the provider
rejects the request). Simpler, but it costs a wasted round-trip every
time, and on chinzy specifically the rejection comes with a 30-60 s
delay before the error surfaces. Preflight saves both.

**Source.** Hermes Agent's "Preflight compression at 50% threshold"
design — compression runs in the API call wrapper, before the wire
send, not in an exception handler.
<https://github.com/NousResearch/Hermes-Function-Calling>

Claude Code SDK does the same: it emits a `compact_boundary` event
before the next request when the context approaches the model's
window, and CLAUDE.md is **re-injected every request** (never
compacted away) so the agent never loses its system context.
<https://docs.anthropic.com/en/docs/claude-code/memory>

---

## 4. `skip_truncation_for_tools` set on the ContextManager (G1 fix)

**Decision.** `ContextManager.config.skip_truncation_for_tools` is a
`frozenset[str]` defaulting to `{"fetch_tool_result"}`. When
`record_tool_result(tool_name=..., result=...)` is called with a name
in that set, the body passes through verbatim — no truncation, no
ref-store stashing, no marker.

**What we rejected.** The pre-G1 approach: truncate everything > 4 KB
unconditionally, on the assumption that the LLM could always re-fetch
via the `fetch_tool_result` tool. This created an infinite loop —
fetch_tool_result returns its own body, B1 truncates that body too,
LLM sees another `[truncated, ref_id=...]` marker, fetches again,
B1 truncates again, ad infinitum.

**Source.** LangChain "Anatomy of Context Engineering" explicitly
calls this out: "tool offloading must be self-aware" — the truncation
layer needs to know which tools are *retrieval* tools that exist to
undo the truncation. The text-contract (a marker string the LLM is
supposed to follow back to a tool) must have a tool-registration
backstop.
<https://blog.langchain.dev/context-engineering/>

The G1 patch in P5-S2 added this skip-set inline at the call site;
P6 lifted it to ContextManager config so the rule lives next to the
truncation logic it constrains. Adding a new "retrieval tool" in the
future is now a config edit, not a code archaeology dig.

---

## 5. Strangler Fig pattern with `P6_ENABLE_GATE` feature flag

**Decision.** Phases 0-5 shipped the new code paths behind a
`P6_ENABLE_GATE` env-var flag (default off). Phase 5 flipped the dev
default to on for a one-week observation period. Phase 6 flipped the
production default to on and removed the legacy code paths
(`if self._gate is None:` branches in `AgentLoop`, the 60-line inline
B2 block in `main.py chat handler`).

**What we rejected.** A big-bang merge that replaced the old paths in
one commit. Faster, but it would have meant rolling back the entire
refactor on any single bug — and we had real users on chinzy hitting
the old paths daily.

**Source.** Martin Fowler's Strangler Fig pattern — gradually replace
an old system by routing a growing share of traffic to the new system
behind a flag, until the old system has zero traffic and can be
deleted safely.
<https://martinfowler.com/bliki/StranglerFigApplication.html>

The flag itself is now permanently on (Phase 6: unset env var returns
`True`); an explicit `P6_ENABLE_GATE=0` opt-out is preserved for one
release as a fallback before P7 removes the function entirely.

---

## Summary

The five decisions above share a theme: **system code, not the LLM,
owns the loop's exit conditions and context budget.** P5-S2's 14 inline
patches were each correct fixes, but they delegated too much of the
"when do we stop" decision to soft messages the model could ignore. P6
re-centred those decisions in `TerminationGate` and `ContextManager`
where they can be inspected, configured, and (most importantly) tested.

Want to add a new termination rule or a new context optimization?
Two single-file edits, both with high test coverage. See
`docs/P6-agent-loop-architecture.md` §"Extension points" for the
recipe.
