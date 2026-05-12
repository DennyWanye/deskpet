"""P6 Phase 4 — chat handler preparation helper.

The main.py chat handler previously inlined a ~90-line block that
imported history_compactor, called should_compact, then called
compact_messages with a hand-built summarize closure. P6 moves the
orchestration to ContextManager.prepare_chat_messages — this module is
the thin shim that lets main.py swap behaviour cleanly with one call
while keeping the unit-testable boundary tight.

The shim is intentionally tiny:
  * If ``ctx_mgr`` is None, the helper is an identity passthrough
    (the legacy inline block runs unchanged in main.py).
  * If ``ctx_mgr`` is supplied, the helper delegates to
    ``ctx_mgr.prepare_chat_messages(...)`` with the right provider
    selected for the summarize step (first provider in the chain,
    falling back to a caller-supplied fallback provider).

Keeping this in its own module + as a pure async function means the
P6 behaviour change is testable without spinning up a FastAPI client.
"""
from __future__ import annotations

from typing import Any, Optional


async def prepare_chat_messages_for_chain(
    messages: list[dict[str, Any]],
    *,
    provider_chain: Optional[list[Any]],
    ctx_mgr: Any,
    fallback_summarizer: Any = None,
) -> list[dict[str, Any]]:
    """Run optional preflight compaction via ContextManager.

    Parameters
    ----------
    messages
        Current chat history (raw list of role/content dicts).
    provider_chain
        Resolved provider chain; the first entry's ``.model`` is used
        as the context-window reference AND as ``llm_for_summarize``.
        ``None`` or empty falls back to ``fallback_summarizer``.
    ctx_mgr
        ContextManager instance, or ``None`` to short-circuit (legacy).
    fallback_summarizer
        Used as ``llm_for_summarize`` when ``provider_chain`` is empty/None.

    Returns
    -------
    list[dict]
        Either the original ``messages`` (identity passthrough when
        ``ctx_mgr`` is None or compaction was a no-op) or a new list
        with old turns folded into a single system summary message.
    """
    if ctx_mgr is None:
        return messages

    if provider_chain:
        summarizer = provider_chain[0]
        model = getattr(provider_chain[0], "model", None) or "unknown"
    else:
        summarizer = fallback_summarizer
        model = (
            getattr(fallback_summarizer, "model", None)
            if fallback_summarizer is not None
            else None
        ) or "unknown"

    return await ctx_mgr.prepare_chat_messages(
        messages, model=model, llm_for_summarize=summarizer,
    )
