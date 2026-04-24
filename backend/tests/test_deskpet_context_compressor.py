"""Tests for the P4-S8 Context Compressor.

Spec — openspec/changes/p4-poseidon-agent-harness/tasks.md §13:

- 13.1 lift Hermes context_engine.py → ``backend/deskpet/agent/context_compressor.py``
- 13.2 trigger: prompt_tokens ≥ context_window * 0.7
- 13.3 rolling summary: keep last K turns, summarize older ones via haiku
- 13.4 summary MUST land in dynamic memory_block, NOT frozen_system
- 13.5 unit test: >40% token reduction + key info (names / times / decisions) preserved

These tests exercise the module via its public surface only
(:class:`ContextCompressor` + :class:`CompressionResult`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from deskpet.agent.context_compressor import (
    CompressionResult,
    ContextCompressor,
)


# ---------------------------------------------------------------------------
# Fakes — mock LLM registry returning deterministic content
# ---------------------------------------------------------------------------
@dataclass
class _FakeResponse:
    content: str
    model: str = "claude-haiku-4-5"


class _FakeLLM:
    """Mock of LLMRegistry exposing ``chat_with_fallback``."""

    def __init__(self, content: str = "[SUMMARY]") -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "",
        max_tokens: int = 0,
        temperature: float = 0.0,
        **_: Any,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return _FakeResponse(content=self._content)


class _RaisingLLM:
    """Mock LLM that always raises — exercises failure path."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def chat_with_fallback(self, *_a: Any, **_kw: Any) -> None:
        self.calls += 1
        raise self._exc


def _messages(count: int, *, include_system: bool = True) -> list[dict[str, Any]]:
    """Build a conversation with ``count`` non-system messages, alternating user/assistant."""
    out: list[dict[str, Any]] = []
    if include_system:
        out.append({"role": "system", "content": "You are DeskPet."})
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"msg-{i:02d}: payload number {i}"})
    return out


# ---------------------------------------------------------------------------
# Threshold gate — §13.2
# ---------------------------------------------------------------------------
class TestShouldCompress:
    def test_below_threshold_returns_false(self) -> None:
        cc = ContextCompressor(context_window=1000, threshold_percent=0.75)
        assert cc.threshold_tokens() == 750
        assert cc.should_compress(100) is False
        assert cc.should_compress(749) is False

    def test_at_threshold_returns_true(self) -> None:
        cc = ContextCompressor(context_window=1000, threshold_percent=0.75)
        assert cc.should_compress(750) is True

    def test_above_threshold_returns_true(self) -> None:
        cc = ContextCompressor(context_window=1000, threshold_percent=0.75)
        assert cc.should_compress(9000) is True

    def test_zero_window_disables_compressor(self) -> None:
        cc = ContextCompressor(context_window=0, threshold_percent=0.75)
        assert cc.should_compress(999_999) is False

    def test_zero_threshold_disables_compressor(self) -> None:
        cc = ContextCompressor(context_window=10_000, threshold_percent=0.0)
        assert cc.should_compress(999_999) is False

    def test_threshold_is_configurable(self) -> None:
        # Spec uses 0.75 default; ensure custom values are honoured.
        cc = ContextCompressor(context_window=10_000, threshold_percent=0.5)
        assert cc.threshold_tokens() == 5_000
        assert cc.should_compress(4_999) is False
        assert cc.should_compress(5_000) is True


# ---------------------------------------------------------------------------
# No-op paths — nothing to summarise OR no LLM available
# ---------------------------------------------------------------------------
class TestNoOpPaths:
    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty_result(self) -> None:
        cc = ContextCompressor(llm_registry=_FakeLLM())
        result = await cc.compress([])
        assert isinstance(result, CompressionResult)
        assert result.messages == []
        assert result.compressed is False

    @pytest.mark.asyncio
    async def test_short_conversation_noop(self) -> None:
        # first_n=3 + last_n=6 = 9, and we have 5 non-system → no middle.
        cc = ContextCompressor(
            llm_registry=_FakeLLM(),
            first_n=3,
            last_n=6,
        )
        msgs = _messages(5)
        result = await cc.compress(msgs)
        assert result.compressed is False
        assert result.messages == msgs
        assert result.meta.get("reason") == "no_middle_to_summarize"

    @pytest.mark.asyncio
    async def test_no_llm_registry_returns_original(self) -> None:
        cc = ContextCompressor(llm_registry=None, first_n=1, last_n=1)
        msgs = _messages(10)
        result = await cc.compress(msgs)
        assert result.compressed is False
        assert result.messages == msgs
        assert result.error == "no_llm_registry"


# ---------------------------------------------------------------------------
# Partition layout — §13.3 "keep first_n + last_n, summarise middle"
# ---------------------------------------------------------------------------
class TestPartitionLayout:
    @pytest.mark.asyncio
    async def test_system_messages_always_kept_verbatim(self) -> None:
        llm = _FakeLLM(content="compressed prose")
        cc = ContextCompressor(
            llm_registry=llm,
            first_n=2,
            last_n=2,
        )
        msgs = [
            {"role": "system", "content": "SYSTEM-1"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
            {"role": "system", "content": "SYSTEM-2"},
        ]
        result = await cc.compress(msgs)
        assert result.compressed is True
        # System messages live at the head and are preserved verbatim.
        assert result.messages[0] == {"role": "system", "content": "SYSTEM-1"}
        assert result.messages[1] == {"role": "system", "content": "SYSTEM-2"}

    @pytest.mark.asyncio
    async def test_first_n_and_last_n_preserved_exactly(self) -> None:
        llm = _FakeLLM(content="ROLLING SUMMARY")
        cc = ContextCompressor(
            llm_registry=llm,
            first_n=2,
            last_n=3,
        )
        # 10 non-system messages, no system message → first 2 + middle 5 + last 3.
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert result.compressed is True

        # Expected shape: [first_2, summary, last_3]
        assert len(result.messages) == 2 + 1 + 3
        assert result.messages[0:2] == msgs[0:2]
        assert result.messages[-3:] == msgs[-3:]

        # Summary is the injected assistant message, in between.
        summary = result.messages[2]
        assert summary["role"] == "assistant"
        assert "[压缩摘要" in summary["content"]
        assert "ROLLING SUMMARY" in summary["content"]

    @pytest.mark.asyncio
    async def test_middle_message_count_recorded(self) -> None:
        llm = _FakeLLM(content="sum")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        # 10 − 1 − 1 = 8 middle messages rolled up.
        assert result.messages_summarized == 8

    @pytest.mark.asyncio
    async def test_summary_injected_after_first_before_last(self) -> None:
        """Task 13.4: summary MUST live in the dynamic zone."""
        llm = _FakeLLM(content="S")
        cc = ContextCompressor(llm_registry=llm, first_n=2, last_n=2)
        msgs = [
            {"role": "system", "content": "FROZEN_SYSTEM"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]
        result = await cc.compress(msgs)
        assert result.compressed is True

        # Frozen system MUST NOT be mutated.
        system_msgs = [m for m in result.messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "FROZEN_SYSTEM"

        # Summary must be a NEW assistant message, injected after first_n=2
        # (i.e. after the system and the first 2 non-system) and before last_n=2.
        summary_idxs = [
            i
            for i, m in enumerate(result.messages)
            if m["role"] == "assistant" and "[压缩摘要" in (m.get("content") or "")
        ]
        assert len(summary_idxs) == 1
        # Layout: [system, u1, a1, SUMMARY, u3, a3] — summary at index 3.
        assert summary_idxs[0] == 3


# ---------------------------------------------------------------------------
# Failure recovery — §13 "on failure return original messages"
# ---------------------------------------------------------------------------
class TestFailureRecovery:
    @pytest.mark.asyncio
    async def test_llm_raises_returns_original(self) -> None:
        llm = _RaisingLLM(RuntimeError("boom"))
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert llm.calls == 1
        assert result.compressed is False
        assert result.messages == msgs
        assert "boom" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_summary_returns_original(self) -> None:
        llm = _FakeLLM(content="   ")  # whitespace only → treated as empty
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert result.compressed is False
        assert result.messages == msgs
        assert result.error == "empty_summary"

    @pytest.mark.asyncio
    async def test_llm_returns_bare_string_tolerated(self) -> None:
        """Response object shape varies — must not crash on odd .content values."""

        class _OddResponse:
            def __init__(self) -> None:
                # None-ish content → treat as empty summary.
                self.content = None

        class _OddLLM:
            async def chat_with_fallback(self, *a: Any, **kw: Any) -> _OddResponse:
                return _OddResponse()

        cc = ContextCompressor(llm_registry=_OddLLM(), first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert result.compressed is False
        assert result.messages == msgs


# ---------------------------------------------------------------------------
# Token reduction — §13.5 ">40% reduction"
# ---------------------------------------------------------------------------
class TestTokenReduction:
    @pytest.mark.asyncio
    async def test_reduction_ratio_meets_40_percent_floor(self) -> None:
        # Middle chunk built from long-ish messages; summary is short.
        middle_count = 20
        long_msgs = [
            {"role": "system", "content": "sys"},
        ] + [
            {
                "role": ("user" if i % 2 == 0 else "assistant"),
                # ~200 chars per line → ~50 tokens under the 4-char heuristic.
                "content": (
                    f"round {i}: "
                    + "filler content " * 15
                    + "end."
                ),
            }
            for i in range(middle_count + 2)  # ensure enough for first_n=1,last_n=1
        ]
        short_summary = "Brief third-person recap."
        llm = _FakeLLM(content=short_summary)
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        result = await cc.compress(long_msgs)
        assert result.compressed is True
        assert result.reduction_ratio > 0.4, (
            f"reduction_ratio={result.reduction_ratio:.2f} fails >40% spec "
            f"(input_tokens={result.input_tokens}, output_tokens={result.output_tokens})"
        )

    @pytest.mark.asyncio
    async def test_input_and_output_tokens_populated(self) -> None:
        llm = _FakeLLM(content="S")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert result.input_tokens > 0
        assert result.output_tokens > 0


# ---------------------------------------------------------------------------
# Key-info preservation — §13.5 "names / times / decisions preserved"
# ---------------------------------------------------------------------------
class TestKeyInfoPreservation:
    """We can't test that Haiku preserves key info (that's a model-quality
    problem), but we CAN test that:

      - the transcript fed to the LLM contains the key info verbatim
      - the summary comes back as a single assistant message with a marker
      - surrounding messages that mention the same key info are KEPT
        verbatim in first_n / last_n so info isn't lost even if Haiku
        misses it.
    """

    @pytest.mark.asyncio
    async def test_transcript_sent_to_llm_preserves_key_facts(self) -> None:
        llm = _FakeLLM(content="ok")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = [
            {"role": "user", "content": "Hi I'm Alice"},
            {"role": "assistant", "content": "Hello Alice"},
            {"role": "user", "content": "My birthday is 2020-01-15"},
            {"role": "assistant", "content": "Noted 2020-01-15"},
            {"role": "user", "content": "Decision: use Postgres"},
            {"role": "assistant", "content": "Decision recorded: Postgres"},
            {"role": "user", "content": "follow-up?"},
            {"role": "assistant", "content": "yes"},
        ]
        result = await cc.compress(msgs)
        assert result.compressed is True

        # Key facts from the middle MUST appear in the transcript the
        # summariser received — otherwise Haiku never sees them.
        assert len(llm.calls) == 1
        summariser_input = llm.calls[0]["messages"]
        assert summariser_input[0]["role"] == "system"
        summariser_user = summariser_input[1]["content"]
        for key_fact in (
            "Hello Alice",
            "2020-01-15",
            "Postgres",
            "Decision recorded",
        ):
            assert key_fact in summariser_user

    @pytest.mark.asyncio
    async def test_last_n_messages_not_mutated(self) -> None:
        """If a key decision was made in the very last turn, it stays verbatim."""
        llm = _FakeLLM(content="(middle summary)")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=2)
        msgs = _messages(10, include_system=False) + [
            {"role": "user", "content": "FINAL DECISION: ship rc1 Friday"},
            {"role": "assistant", "content": "Confirmed: rc1 Friday"},
        ]
        result = await cc.compress(msgs)
        assert result.compressed is True
        # last_n=2 → those exact two messages at tail.
        assert result.messages[-1]["content"] == "Confirmed: rc1 Friday"
        assert result.messages[-2]["content"] == "FINAL DECISION: ship rc1 Friday"

    @pytest.mark.asyncio
    async def test_first_n_messages_not_mutated(self) -> None:
        """Initial user question is load-bearing — must survive compression."""
        llm = _FakeLLM(content="(summary)")
        cc = ContextCompressor(llm_registry=llm, first_n=2, last_n=2)
        msgs = [
            {
                "role": "user",
                "content": "INITIAL TASK: build a Gmail agent for Alice",
            },
            {"role": "assistant", "content": "Sure, starting work"},
        ] + _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert result.compressed is True
        assert (
            result.messages[0]["content"]
            == "INITIAL TASK: build a Gmail agent for Alice"
        )
        assert result.messages[1]["content"] == "Sure, starting work"


# ---------------------------------------------------------------------------
# Summariser prompt — names / times / decisions explicitly requested
# ---------------------------------------------------------------------------
class TestSummariserPrompt:
    @pytest.mark.asyncio
    async def test_summariser_system_prompt_requests_key_info(self) -> None:
        llm = _FakeLLM(content="s")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        await cc.compress(msgs)

        sys_prompt = llm.calls[0]["messages"][0]["content"].lower()
        # The prompt must ASK for names, dates/times, decisions — even if
        # the actual LLM may ignore them. This is the contract we ship.
        assert "named" in sys_prompt or "name" in sys_prompt
        assert "date" in sys_prompt or "time" in sys_prompt
        assert "decision" in sys_prompt or "commitment" in sys_prompt

    @pytest.mark.asyncio
    async def test_summariser_uses_configured_model(self) -> None:
        llm = _FakeLLM(content="s")
        cc = ContextCompressor(
            llm_registry=llm,
            first_n=1,
            last_n=1,
            model="claude-haiku-4-5",
        )
        msgs = _messages(10, include_system=False)
        await cc.compress(msgs)
        assert llm.calls[0]["model"] == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_summariser_caps_max_tokens(self) -> None:
        llm = _FakeLLM(content="s")
        cc = ContextCompressor(
            llm_registry=llm,
            first_n=1,
            last_n=1,
            summary_max_tokens=256,
        )
        msgs = _messages(10, include_system=False)
        await cc.compress(msgs)
        assert llm.calls[0]["max_tokens"] == 256

    @pytest.mark.asyncio
    async def test_summariser_uses_temperature_zero(self) -> None:
        """Determinism matters — the summary is cache-relevant."""
        llm = _FakeLLM(content="s")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        await cc.compress(msgs)
        assert llm.calls[0]["temperature"] == 0.0


# ---------------------------------------------------------------------------
# Multi-part content & tool-call handling in transcript renderer
# ---------------------------------------------------------------------------
class TestTranscriptRendering:
    @pytest.mark.asyncio
    async def test_multipart_text_content_flattened(self) -> None:
        """Anthropic-style content list of parts is rendered as plain text."""
        llm = _FakeLLM(content="s")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = [
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "chunk A"},
                    {"type": "text", "text": "chunk B"},
                ],
            },
            {"role": "user", "content": "middle"},
            {"role": "assistant", "content": "mid reply"},
            {"role": "user", "content": "end"},
        ]
        result = await cc.compress(msgs)
        assert result.compressed is True
        # chunks must be visible in the rendered transcript.
        rendered = llm.calls[0]["messages"][1]["content"]
        assert "chunk A" in rendered
        assert "chunk B" in rendered

    @pytest.mark.asyncio
    async def test_tool_calls_recorded_as_pseudo_line(self) -> None:
        """Assistant tool-call turns must not silently vanish from the summary input."""
        llm = _FakeLLM(content="s")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = [
            {"role": "user", "content": "search please"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "1", "function": {"name": "web_search"}},
                ],
            },
            {"role": "tool", "content": "search results..."},
            {"role": "assistant", "content": "ok done"},
            {"role": "user", "content": "thanks"},
        ]
        result = await cc.compress(msgs)
        assert result.compressed is True
        rendered = llm.calls[0]["messages"][1]["content"]
        # The tool invocation should survive as a line marker.
        assert "web_search" in rendered


# ---------------------------------------------------------------------------
# Summary formatting — §13.4 "dynamic zone marker"
# ---------------------------------------------------------------------------
class TestSummaryFormatting:
    @pytest.mark.asyncio
    async def test_summary_has_visible_marker(self) -> None:
        llm = _FakeLLM(content="third-person recap")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert result.compressed is True
        summary_msg = next(
            m
            for m in result.messages
            if m["role"] == "assistant" and "[压缩摘要" in (m["content"] or "")
        )
        assert summary_msg["content"].startswith("[压缩摘要")

    @pytest.mark.asyncio
    async def test_summary_preview_in_result(self) -> None:
        llm = _FakeLLM(content="A concise summary of the middle.")
        cc = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = _messages(10, include_system=False)
        result = await cc.compress(msgs)
        assert "concise summary" in result.summary_preview
