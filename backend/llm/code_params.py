"""code-session-model-params — pure model-params → request mapper.

Maps a code session's Cursor-style ``model_params`` (thinking / fast /
context / effort) onto the relay OpenAI-compatible request fields.

Contract (Spec "Param→request mapping is total"):
  * total & pure — never raises, no I/O
  * None / non-dict / empty  → ``{}`` (provider defaults)
  * unknown / legacy values  → omitted or clamped (never an error)
  * ``thinking == False``     → NO reasoning keys at all
"""
from __future__ import annotations

from typing import Any

# OpenAI exposes low|medium|high; the extra Cursor rungs clamp to high.
_EFFORT: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "extra_high": "high",
    "max": "high",
}

_CONTEXT_TOKENS: dict[str, int] = {
    "300k": 300_000,
    "1m": 1_000_000,
}


def code_params_to_request(model_params: Any) -> dict[str, Any]:
    """Return request-body fragment to merge into the the relay call.

    Well-known OpenAI key (``reasoning_effort``) is emitted top-level;
    less-standardised hints (``context_window``, ``fast``) go under
    ``extra_body`` so an upstream that ignores them never breaks.
    """
    if not isinstance(model_params, dict) or not model_params:
        return {}

    out: dict[str, Any] = {}
    extra: dict[str, Any] = {}

    thinking = model_params.get("thinking")
    if thinking is not False:  # True / absent → reasoning permitted
        effort_raw = model_params.get("effort")
        eff = (
            _EFFORT.get(str(effort_raw).lower())
            if effort_raw is not None
            else None
        )
        if eff is None and thinking is True:
            eff = "medium"  # thinking explicitly ON, no effort → sane default
        if eff is not None:
            out["reasoning_effort"] = eff
    # thinking is False → emit no reasoning keys (effort intentionally dropped)

    ctx_raw = model_params.get("context")
    cw = (
        _CONTEXT_TOKENS.get(str(ctx_raw).lower())
        if ctx_raw is not None
        else None
    )
    if cw is not None:
        extra["context_window"] = cw

    if model_params.get("fast") is True:
        extra["fast"] = True

    if extra:
        out["extra_body"] = extra
    return out
