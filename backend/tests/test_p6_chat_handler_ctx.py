"""P6 Phase 4 — chat handler ContextManager integration tests.

Direct ws-message-branch testing of main.py's chat handler is brittle
(the chat path constructs many service-context dependencies). Instead
we extracted the message-prep logic into a small pure-function helper
``backend/agent/chat_prep.py::prepare_chat_messages_for_chain`` so the
behaviour change is easy to unit-test and the main.py edit is a
one-line swap.

Cases:
  * 4.8a ctx is set → ctx.prepare_chat_messages called with the
    expected llm_for_summarize argument.
  * 4.8b ctx is None → returns messages as-is, no compaction invoked.
  * 4.8c when a non-empty provider_chain is supplied, llm_for_summarize
    is the first provider in the chain.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.chat_prep import prepare_chat_messages_for_chain
from agent.context_manager import ContextManager


class _RecordingCtx:
    """Stand-in for ContextManager that records every call."""

    def __init__(self, return_value: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._return = return_value

    async def prepare_chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        llm_for_summarize: Any = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "llm_for_summarize": llm_for_summarize,
            }
        )
        return self._return if self._return is not None else list(messages)


class _StubProvider:
    """Provider stub with .model + .id attributes."""

    def __init__(self, provider_id: str, model: str = "stub-model") -> None:
        self.id = provider_id
        self.model = model

    async def chat_with_tools(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"content": "summary", "tool_calls": []}


# ─────────────── 4.8 chat_prep helper tests ───────────────


class TestChatPrep:
    @pytest.mark.asyncio
    async def test_chat_prep_calls_ctx_when_present(self):
        """ctx is set → ctx.prepare_chat_messages called exactly once."""
        ctx = _RecordingCtx()
        provider = _StubProvider("p1")
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        out = await prepare_chat_messages_for_chain(
            msgs, provider_chain=[provider], ctx_mgr=ctx,
        )
        assert len(ctx.calls) == 1
        # Returned value matches what ctx returned (passthrough copy).
        assert out == msgs

    @pytest.mark.asyncio
    async def test_chat_prep_no_ctx_returns_unchanged(self):
        """ctx is None → returns messages unchanged, no exception."""
        msgs = [{"role": "user", "content": "ping"}]
        out = await prepare_chat_messages_for_chain(
            msgs, provider_chain=[_StubProvider("p1")], ctx_mgr=None,
        )
        assert out is msgs  # identity passthrough — no copy needed

    @pytest.mark.asyncio
    async def test_chat_prep_summarize_uses_first_provider_in_chain(self):
        """When a non-empty chain is supplied, llm_for_summarize is
        provider_chain[0]."""
        ctx = _RecordingCtx()
        prov_a = _StubProvider("p_first", model="model-a")
        prov_b = _StubProvider("p_second", model="model-b")
        await prepare_chat_messages_for_chain(
            [{"role": "user", "content": "hi"}],
            provider_chain=[prov_a, prov_b],
            ctx_mgr=ctx,
        )
        assert ctx.calls[0]["llm_for_summarize"] is prov_a
        assert ctx.calls[0]["model"] == "model-a"

    @pytest.mark.asyncio
    async def test_chat_prep_empty_chain_falls_back_to_fallback_summarizer(self):
        """Empty provider_chain → caller-provided fallback summarizer is
        used; model defaults to 'unknown' when no model is resolvable."""
        ctx = _RecordingCtx()
        fallback = _StubProvider("fallback")
        await prepare_chat_messages_for_chain(
            [{"role": "user", "content": "hi"}],
            provider_chain=None,
            ctx_mgr=ctx,
            fallback_summarizer=fallback,
        )
        # ctx called with fallback as llm_for_summarize
        assert ctx.calls[0]["llm_for_summarize"] is fallback

    @pytest.mark.asyncio
    async def test_chat_prep_real_context_manager_passthrough(self):
        """Smoke test with a real ContextManager — short history below
        compaction threshold returns as-is."""
        ctx = ContextManager()
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        out = await prepare_chat_messages_for_chain(
            msgs, provider_chain=[_StubProvider("p1")], ctx_mgr=ctx,
        )
        # ContextManager.prepare_chat_messages returns list(messages)
        # when no compaction is needed; identity not required.
        assert out == msgs
