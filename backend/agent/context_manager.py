"""P6 Phase 2 — ContextManager facade.

Why
---
Before P6 the chat handler + AgentLoop reached directly into three
independent helpers — B1 ``tool_result_truncator``, B2 ``history_compactor``,
B3 ``token_budget`` — and each call site duplicated wiring (thresholds,
provider plumbing, ref-store sharing).  This module unifies them into a
single facade so the agent loop has **one** dependency for "context
optimization":

  * Truncate noisy tool results into head + tail + ref_id marker (B1)
  * Compact old turns into a summary system message when history grows
    too large (B2)
  * Estimate token usage and emit WARN / BLOCK verdicts (B3)

It also carries the **G1 fix** at the type level: the
``skip_truncation_for_tools`` set means the ``fetch_tool_result`` tool
itself cannot be truncated (the original infinite-loop root cause was
fetch returning a body that itself got truncated, so the LLM kept
fetching refs returned from inside truncated fetch responses).

Design choices
--------------
* **Facade, not orchestrator.**  This class delegates everything to
  B1/B2/B3 — it does not implement truncation/compaction itself.  That
  keeps existing test coverage in those modules intact and lets us
  evolve internals independently.
* **Global ref store reuse.**  ``ContextManager.ref_store`` is the same
  ``get_global_ref_store()`` singleton the future ``fetch_tool_result``
  tool reads from.  Identity check (``is``) is required for the G1
  round-trip to work.
* **Async only where needed.**  ``check_budget`` / ``record_tool_result``
  are sync (pure-CPU paths).  ``maybe_compact`` /
  ``prepare_chat_messages`` are async because the summarize step is an
  LLM call.
* **No provider import.**  ``llm_for_summarize`` is passed in by the
  caller.  This keeps the unit tests free of provider/transport state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Tuple

from agent.history_compactor import compact_messages, should_compact
from agent.token_budget import BudgetCheckResult, check_budget
from agent.tool_result_truncator import (
    ToolResultRefStore,
    get_global_ref_store,
    maybe_truncate_tool_result,
)


# Type alias matching what history_compactor expects.
SummarizeFn = Callable[[str], Awaitable[str]]


@dataclass
class ContextConfig:
    """Unified configuration for B1 + B2 + B3.

    Defaults match the per-module defaults — they live here so the
    AgentLoop can override any single value (e.g. raise compaction
    threshold for long-context models) without having to thread three
    separate dataclasses through the call sites.
    """

    # 2026-05-15 一次性按 4x 放大（200K → 800K context 同比例），
    # 与 config.toml [agent].context_window_tokens=800000 配套。
    # 调研依据：
    #   - Claude Code: compact at ~83% window
    #   - Cline: compact at 80% window
    #   - DeepSeek-TUI: cycle restart at 75% (768K of 1M)
    #   - Codex: per-model effective_context_window_percent = 95%
    # 当前 deskpet 是 absolute char/msg 阈值，不是按比例；中长期应该改成
    # per-model 比例触发（参考 codex-rs/models-manager/src/model_info.rs）。

    # B1 truncation
    # 4K → 16K：原阈值对应 32K context 时代，工具结果切太狠是今天 small/code
    # 模式下 50-轮爆的主因之一（小说网站 sid=code-rkjdd9vo 反复 fetch_tool_result
    # 拿切片导致循环）。Codex 的 exec/MCP 阈值是 1 MiB，16K 是保守中间值。
    tool_result_threshold: int = 16_000
    tool_result_head: int = 6_000
    tool_result_tail: int = 2_000
    # B1 self-awareness — the G1 fix lives here.  Adding a new "must keep
    # full body" tool is a one-set-edit, not a code change.
    skip_truncation_for_tools: set[str] = field(
        default_factory=lambda: {"fetch_tool_result"},
    )

    # B2 compaction
    # 20 msgs / 60K chars → 80 msgs / 300K chars：800K context 下 60K 太早压缩，
    # 反复摘要会破 prefix cache 也会丢细节。Claude Code 的触发点是
    # (window - max(out, 20K) - 13K)，800K 模型下约 767K（96%）。这里保守一些。
    compact_message_threshold: int = 80
    compact_char_threshold: int = 300_000
    # keep_recent 也从 6 → 12，避免长 agent 任务（write_file 大块文件）压缩后
    # 丢失最近上下文。
    compact_keep_recent: int = 12

    # B3 budget — 比例不动，因为 0.80/0.95 已经和行业一致；window 自动跟着
    # config.toml 的 800K 走，warn 在 640K / block 在 760K。
    budget_warn_pct: float = 0.80
    budget_block_pct: float = 0.95


class ContextManager:
    """Single entry point for all context-optimization concerns.

    Typical use::

        ctx = ContextManager()

        # chat handler entry: optional preflight compaction
        msgs = await ctx.prepare_chat_messages(
            msgs, model=provider.model, llm_for_summarize=provider,
        )

        # AgentLoop per-iteration: budget guard
        budget = ctx.check_budget(msgs, model=provider.model)
        if budget.verdict is BudgetCheck.BLOCK:
            yield ErrorEvent(reason="context_budget_block", detail=budget.advice)
            return

        # After every tool dispatch:
        content, ref_id = ctx.record_tool_result(
            tool_name=tc.name, result=result_str,
        )
        working_messages.append({..., "content": content})
    """

    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        summarize_fn: Optional[SummarizeFn] = None,
        ref_store: Optional[ToolResultRefStore] = None,
    ) -> None:
        self.config = config or ContextConfig()
        self.summarize_fn = summarize_fn
        # ``ref_store`` defaults to the module-level singleton — same one
        # the ``fetch_tool_result`` tool reads.  Pass a custom store only
        # in tests that need isolation.
        self.ref_store = ref_store if ref_store is not None else get_global_ref_store()

    # ─────────────────────── B3 token budget ───────────────────────

    def check_budget(
        self, messages: list[dict[str, Any]], *, model: str,
    ) -> BudgetCheckResult:
        """Pre-call token budget check delegating to B3.

        Returns the full ``BudgetCheckResult`` (verdict + estimated
        tokens + window + ratio + advice string).  Caller decides what
        to do with WARN/BLOCK.
        """
        return check_budget(
            messages,
            model=model,
            warn_pct=self.config.budget_warn_pct,
            block_pct=self.config.budget_block_pct,
        )

    # ───────────────────── B2 history compaction ─────────────────────

    async def maybe_compact(
        self,
        messages: list[dict[str, Any]],
        *,
        llm_for_summarize: Any,
    ) -> list[dict[str, Any]]:
        """Preflight compaction (Hermes-style, no waiting for overflow).

        Skips work if the history is small (below count + char
        thresholds).  When it does compact, asks ``llm_for_summarize``
        for a short Chinese summary of the middle range; on any
        summarize failure, returns the original list (better to keep
        a long context than silently lose history).
        """
        if not should_compact(
            messages,
            message_threshold=self.config.compact_message_threshold,
            char_threshold=self.config.compact_char_threshold,
        ):
            return list(messages)

        async def _summarize(text: str) -> str:
            r = await llm_for_summarize.chat_with_tools(
                [
                    {
                        "role": "system",
                        "content": (
                            "Compress the following conversation into a concise "
                            "Chinese summary capturing: completed steps, key "
                            "decisions, tool results' outcomes, current state. "
                            "≤ 600 chars."
                        ),
                    },
                    {"role": "user", "content": text[:50_000]},
                ],
                tools=None,
                max_tokens=800,
                temperature=0.1,
            )
            return r.get("content", "") if isinstance(r, dict) else ""

        return await compact_messages(
            messages,
            summarize_fn=_summarize,
            message_threshold=self.config.compact_message_threshold,
            char_threshold=self.config.compact_char_threshold,
            keep_recent=self.config.compact_keep_recent,
        )

    # ──────────── B1 + G1 unified tool result handling ────────────

    def record_tool_result(
        self,
        *,
        tool_name: str,
        result: str,
    ) -> Tuple[str, Optional[str]]:
        """Return ``(content_for_history, ref_id_or_None)``.

        For tools listed in ``skip_truncation_for_tools`` (G1 fix) the
        result is passed through verbatim and no ref is created.  For
        everything else, delegates to B1 ``maybe_truncate_tool_result``.
        """
        if tool_name in self.config.skip_truncation_for_tools:
            # Coerce non-string results to string for caller convenience.
            content = result if isinstance(result, str) else str(result)
            return (content, None)

        return maybe_truncate_tool_result(
            result,
            store=self.ref_store,
            threshold=self.config.tool_result_threshold,
            head_chars=self.config.tool_result_head,
            tail_chars=self.config.tool_result_tail,
        )

    # ─────────────────── High-level prepare ───────────────────

    async def prepare_chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,  # noqa: ARG002 — reserved for future per-model strategy
        llm_for_summarize: Optional[Any] = None,
    ) -> list[dict[str, Any]]:
        """Chat handler entry point.

        Currently runs preflight compaction when a summarize LLM is
        supplied.  Budget BLOCK is intentionally NOT enforced here —
        that's an ErrorEvent decision the AgentLoop owns, not a transform
        ContextManager applies silently.
        """
        if llm_for_summarize is None:
            return list(messages)

        return await self.maybe_compact(
            messages, llm_for_summarize=llm_for_summarize,
        )
