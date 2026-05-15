"""P6 Phase 2 — ContextManager facade tests.

Tests cover:
  * ContextConfig defaults
  * Facade reuses global ref store (G1 fix prerequisite)
  * Budget check delegates to B3 with config thresholds
  * Compaction (B2) skip/call/failure paths
  * Tool result handling (B1 + G1 unified) — including
    ``skip_truncation_for_tools={"fetch_tool_result"}`` G1 core fix
  * High-level prepare_chat_messages orchestration
"""
from __future__ import annotations

import asyncio
import pytest

from agent.context_manager import ContextConfig, ContextManager
from agent.token_budget import BudgetCheck, check_budget
from agent.tool_result_truncator import get_global_ref_store


# ─────────────────────────── helpers / fakes ───────────────────────────


class FakeLLM:
    """Minimal stub matching the subset of LLM API ContextManager needs.

    ContextManager only calls ``chat_with_tools`` on the summarize LLM,
    so that's all we implement.
    """

    def __init__(self, content: str = "FAKE-SUMMARY"):
        self.content = content
        self.calls: list[dict] = []

    async def chat_with_tools(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return {"content": self.content}


class RaisingLLM:
    """Stub that raises on every call — for failure-path tests."""

    async def chat_with_tools(self, messages, *, tools=None, **kwargs):
        raise RuntimeError("simulated provider failure")


def _build_long_history(n: int = 25, body_chars: int = 200) -> list[dict]:
    """Build a long-ish message list past compaction thresholds.

    Uses a non-system head + user/assistant alternation so the compactor
    actually has a middle range to summarize.
    """
    body = "x" * body_chars
    msgs = [{"role": "system", "content": "you are deskpet"}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"turn-{i} {body}"})
    return msgs


# ───────────────── 2.1 Config + facade skeleton ─────────────────


def test_default_config_values():
    """Phase 1.1.3：ContextConfig 默认走 v2 per-model 比例（design.md D2）。

    无注入 model_info → 懒解析 ``_default`` (32K window)。阈值由
    ``@property`` 按 window 比例算，不再是 2026-05-15 stop-gap 的写死
    绝对值（proposal.md：stop-gap 配置被 per-model 化取代）。
    """
    cfg = ContextConfig()
    assert cfg.v2_enabled is True

    # B1 truncation — v2: max(8_000, 32_000 // 25) = 8_000 floor
    assert cfg.tool_result_threshold == 8_000
    # head/tail 两路共用稳定切片量
    assert cfg.tool_result_head == 6_000
    assert cfg.tool_result_tail == 2_000
    # B1 self-awareness — G1 fix core
    assert cfg.skip_truncation_for_tools == {"fetch_tool_result"}

    # B2 compaction — v2: _default 32K → compact_at 32_000*0.80=25_600；
    # message 阈值 max(20, 32_000//10_000)=20
    assert cfg.compact_at_tokens == 25_600
    assert cfg.compact_message_threshold == 20
    assert cfg.compact_keep_recent == 12

    # B3 budget — 比例两路共用
    assert cfg.budget_warn_pct == 0.80
    assert cfg.budget_block_pct == 0.95


def test_legacy_v1_config_values_when_v2_disabled():
    """Strangler-Fig 回退闸：v2_enabled=False → 2026-05-15 stop-gap 绝对值。"""
    cfg = ContextConfig(v2_enabled=False)
    assert cfg.tool_result_threshold == 16_000
    assert cfg.tool_result_head == 6_000
    assert cfg.tool_result_tail == 2_000
    assert cfg.compact_message_threshold == 80
    assert cfg.compact_char_threshold == 300_000
    assert cfg.compact_keep_recent == 12
    assert cfg.budget_warn_pct == 0.80
    assert cfg.budget_block_pct == 0.95


def test_facade_holds_ref_to_global_store():
    """ContextManager.ref_store IS the global singleton (identity check).

    G1 fix relies on fetch_tool_result tool + ContextManager both reading
    from the same store. Identity (``is``) — not equality — is required.
    """
    ctx = ContextManager()
    assert ctx.ref_store is get_global_ref_store()


def test_config_is_overridable():
    """ContextConfig 仍是真 dataclass：可注入字段可独立 override。

    Phase 1.1.3 后阈值是 @property（按 model_info 比例算），不再是可写
    字段；可 override 的是 model_info / head / tail / budget pct /
    skip set。仍守护 ``skip_truncation_for_tools`` 不是共享类级 mutable。
    """
    from llm.model_info import resolve as _resolve

    cfg1 = ContextConfig(
        model_info=_resolve("claude-sonnet-4-5"),  # 200K window
        budget_warn_pct=0.7,
        skip_truncation_for_tools={"fetch_tool_result", "my_special_tool"},
    )
    cfg2 = ContextConfig()  # defaults → _default 32K

    # 注入 sonnet：compact_at = 200_000*0.83 = 166_000；
    # tool_result = max(8_000, 200_000//25)=8_000
    assert cfg1.compact_at_tokens == 166_000
    assert cfg1.tool_result_threshold == 8_000
    assert cfg1.budget_warn_pct == 0.7
    assert "my_special_tool" in cfg1.skip_truncation_for_tools

    # cfg2's defaults are NOT contaminated by cfg1
    assert cfg2.compact_at_tokens == 25_600  # _default 32K*0.80
    assert "my_special_tool" not in cfg2.skip_truncation_for_tools

    # Mutating cfg1's set must not leak into cfg2's set (no shared default).
    cfg1.skip_truncation_for_tools.add("yet_another")
    assert "yet_another" not in cfg2.skip_truncation_for_tools


def test_config_v1_legacy_path_gives_absolute_control():
    """需要绝对值控制（旧行为）时走 v2_enabled=False 回退闸。"""
    cfg = ContextConfig(v2_enabled=False)
    # v1 写死阈值，不随 model 变
    assert cfg.tool_result_threshold == 16_000
    assert cfg.compact_message_threshold == 80


# ───────────────── 2.2 Budget check (B3 wrap) ─────────────────


def test_check_budget_delegates_to_b3():
    """ContextManager.check_budget output matches direct token_budget call.

    Same messages + same model + default thresholds → identical verdict,
    estimated_tokens, context_window. (Advice strings may match too, but
    we assert the load-bearing fields.)
    """
    ctx = ContextManager()
    msgs = [{"role": "user", "content": "hi"}]

    via_facade = ctx.check_budget(msgs, model="deepseek-v4-pro")
    via_direct = check_budget(msgs, model="deepseek-v4-pro")

    assert via_facade.verdict == via_direct.verdict
    assert via_facade.estimated_tokens == via_direct.estimated_tokens
    assert via_facade.context_window == via_direct.context_window
    assert via_facade.ratio == via_direct.ratio


def test_check_budget_uses_config_thresholds():
    """Custom warn_pct=0.5: ratio ≈0.6 → WARN.

    deepseek-v4-pro has a 64,000 token window. To hit ratio ≈ 0.6 we
    need ~38,400 tokens — roughly 153,000 chars (char/4 heuristic).
    """
    cfg = ContextConfig(budget_warn_pct=0.5, budget_block_pct=0.95)
    ctx = ContextManager(config=cfg)

    # ~153_600 chars → ~38,400 tokens → ratio ≈ 0.6 on 64k window
    big_body = "x" * 153_000
    msgs = [{"role": "user", "content": big_body}]

    result = ctx.check_budget(msgs, model="deepseek-v4-pro")
    assert result.verdict == BudgetCheck.WARN, (
        f"expected WARN at ratio {result.ratio:.2f}, "
        f"got {result.verdict.value}"
    )


# ───────────────── 2.3 Compaction (B2 wrap) ─────────────────


def test_maybe_compact_skips_below_threshold():
    """Short history → returns same list unchanged, summarize never called."""
    ctx = ContextManager()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi 1"},
        {"role": "assistant", "content": "hello 1"},
        {"role": "user", "content": "hi 2"},
        {"role": "assistant", "content": "hello 2"},
    ]
    fake = FakeLLM()
    result = asyncio.run(ctx.maybe_compact(msgs, llm_for_summarize=fake))

    # Same content (order + values preserved)
    assert [m["content"] for m in result] == [m["content"] for m in msgs]
    # summarize_fn never called
    assert fake.calls == []


def test_maybe_compact_calls_summarize_above_threshold():
    """≥ 20 messages → summarize_fn called, shape = [sys?, summary, last-N].

    Verifies:
      * FakeLLM.chat_with_tools was invoked (non-empty calls list)
      * Returned list contains a system message with "FAKE-SUMMARY"
      * Result is shorter than input (compaction actually happened)
    """
    ctx = ContextManager()
    msgs = _build_long_history(n=25)
    fake = FakeLLM(content="FAKE-SUMMARY-CONTENT")

    result = asyncio.run(ctx.maybe_compact(msgs, llm_for_summarize=fake))

    # Summarize LLM was called exactly once
    assert len(fake.calls) == 1

    # Result is meaningfully shorter
    assert len(result) < len(msgs)

    # Summary content appears somewhere in the result (as a system msg)
    joined = "\n".join(str(m.get("content", "")) for m in result)
    assert "FAKE-SUMMARY-CONTENT" in joined

    # The very last messages from the original tail are preserved
    last_orig = msgs[-1]["content"]
    assert any(m.get("content") == last_orig for m in result)


def test_maybe_compact_failure_returns_original():
    """summarize_fn raise → ContextManager.maybe_compact returns input list."""
    ctx = ContextManager()
    msgs = _build_long_history(n=25)
    bad = RaisingLLM()

    result = asyncio.run(ctx.maybe_compact(msgs, llm_for_summarize=bad))

    # Length preserved → history not lost
    assert len(result) == len(msgs)
    assert [m["content"] for m in result] == [m["content"] for m in msgs]


# ───────────────── 2.4 Tool result handling (B1 + G1) ─────────────────


def test_record_tool_result_truncates_long():
    """12000 char read_file result → returns (truncated_string, ref_id).

    Phase 1.1.3：v2 默认 _default(32K) 的 tool_result_threshold=8_000，
    用 12_000 确保超阈值触发切片（保留本测原意：长 body 被截断）。
    """
    ctx = ContextManager()
    big = "a" * 12000

    content, ref_id = ctx.record_tool_result(tool_name="read_file", result=big)

    assert ref_id is not None
    assert isinstance(ref_id, str) and len(ref_id) > 0
    # Truncated string is shorter than the original
    assert len(content) < len(big)
    # And it carries the ref marker
    assert ref_id in content


def test_record_tool_result_keeps_short():
    """1000 char result → returns (original_unchanged, None). No ref created."""
    ctx = ContextManager()
    short = "b" * 1000

    content, ref_id = ctx.record_tool_result(tool_name="read_file", result=short)

    assert ref_id is None
    assert content == short


def test_record_tool_result_skips_fetch_tool_result():
    """G1 FIX CORE — fetch_tool_result body must never be truncated.

    The fetch_tool_result tool is itself the way to retrieve a truncated
    body. If we truncate its response, the LLM loops trying to fetch
    refs from inside truncated fetch responses — the original G1 bug.
    """
    ctx = ContextManager()
    big = "c" * 6000

    content, ref_id = ctx.record_tool_result(
        tool_name="fetch_tool_result", result=big,
    )

    # Returned unchanged, no ref created.
    assert ref_id is None
    assert content == big
    assert len(content) == 6000


def test_record_tool_result_custom_skip_list():
    """Custom ContextConfig.skip_truncation_for_tools honored."""
    cfg = ContextConfig(skip_truncation_for_tools={"my_tool"})
    ctx = ContextManager(config=cfg)
    big = "d" * 12000  # > v2 _default tool_result_threshold (8_000)

    content, ref_id = ctx.record_tool_result(tool_name="my_tool", result=big)

    assert ref_id is None
    assert content == big

    # Sanity: a different tool name still gets truncated
    content2, ref_id2 = ctx.record_tool_result(
        tool_name="some_other_tool", result=big,
    )
    assert ref_id2 is not None
    assert content2 != big


def test_record_tool_result_uses_global_ref_store():
    """After record_tool_result returns ref_id, the global store has the
    full body — proving facade + fetch_tool_result share the same store.
    """
    ctx = ContextManager()
    big = "e" * 12000  # > v2 _default tool_result_threshold (8_000)

    _content, ref_id = ctx.record_tool_result(tool_name="read_file", result=big)

    assert ref_id is not None
    fetched = get_global_ref_store().get(ref_id)
    assert fetched == big


# ───────────────── 2.5 High-level prepare ─────────────────


def test_prepare_chat_messages_with_compaction():
    """Long history + a summarize provider → prepare returns shorter list."""
    ctx = ContextManager()
    msgs = _build_long_history(n=30)
    fake = FakeLLM(content="PREPARED")

    result = asyncio.run(
        ctx.prepare_chat_messages(
            msgs, model="deepseek-v4-pro", llm_for_summarize=fake,
        )
    )

    assert len(result) < len(msgs)
    # summarize was actually called
    assert len(fake.calls) == 1


def test_prepare_chat_messages_no_summarize_fn_skips_compact():
    """llm_for_summarize=None → returns original unchanged, no compaction."""
    ctx = ContextManager()
    msgs = _build_long_history(n=30)

    result = asyncio.run(
        ctx.prepare_chat_messages(
            msgs, model="deepseek-v4-pro", llm_for_summarize=None,
        )
    )

    # Verbatim length + content preserved
    assert len(result) == len(msgs)
    assert [m["content"] for m in result] == [m["content"] for m in msgs]
