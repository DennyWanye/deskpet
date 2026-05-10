"""P5-S2 Phase 2: tool error classification.

Classifies a tool error (dict envelope, raw string, or Exception) into one
of three categories so the agent loop can pick the right recovery path:

- :class:`PermanentToolError` — same args will keep failing. The agent
  loop should break out immediately and surface a structured error to
  the supervisor (saves up to ``max_iterations`` worth of wasted LLM
  turns when the LLM keeps invoking the same broken tool_call).

- :class:`TransientToolError` — could succeed on retry (network blip,
  upstream 5xx, rate limit). The loop feeds the error back to the LLM
  as a regular tool_result and lets ReAct continue — the LLM can retry
  with adjusted args or move on.

- :class:`HallucinationError` — the LLM invoked a tool that doesn't
  exist or used a totally bogus shape. Different recovery: the
  orchestrator (Phase 4) routes this to the supervisor for diagnosis
  rather than blind retry.

Conservative default: if we've never seen the error string before,
return ``TransientToolError``. Wasting a retry is cheap; killing a turn
that could have been recovered is expensive.

Keyword tables are exposed as module constants
(:data:`PERMANENT_KEYWORDS` / :data:`TRANSIENT_KEYWORDS` /
:data:`HALLUCINATION_KEYWORDS`) so new error patterns can be added in
one place without touching :func:`classify` logic.
"""
from __future__ import annotations

from typing import Type, Union


# ─────────────── exception types ───────────────


class PermanentToolError(Exception):
    """Tool error that retrying with the same args won't fix.

    Examples: schema-level mistakes (missing required parameter), permission
    denial, file-not-found, write_file overwrite guard, circuit-breaker open.
    """


class TransientToolError(Exception):
    """Tool error that *might* succeed on retry.

    Examples: network timeout, upstream 5xx, rate limit, connection reset.
    Also the conservative default for unknown error strings.
    """


class HallucinationError(Exception):
    """LLM invoked a tool that does not exist (or otherwise outside the
    allowed contract). Recovery path is supervisor diagnosis, not naive
    retry.
    """


# ─────────────── keyword tables (extend here, not in classify) ───────────────

PERMANENT_KEYWORDS: frozenset[str] = frozenset({
    # Schema / args mistakes — same args = same failure.
    "missing required parameter",
    "missing_required_parameters",
    "schema_invalid",
    "invalid_argument",
    # Domain-level guards.
    "would_overwrite",
    "file_not_found",
    "permission_denied",
    "permission denied",
    "circuit_open",
    "not_unique",
    "not_a_dir",
    "not a directory",
    "binary_file",
    "encoding_error",
})

TRANSIENT_KEYWORDS: frozenset[str] = frozenset({
    # Network / IO.
    "timeout",
    "tool_timeout",
    "ReadTimeout",
    "Server disconnected",
    "server disconnected",
    "connection_reset",
    "connection reset",
    "connectionreset",
    # Upstream HTTP 5xx / rate limit.
    "503",
    "502",
    "504",
    "rate_limit",
    "rate limit",
})

HALLUCINATION_KEYWORDS: frozenset[str] = frozenset({
    "tool_not_found",
    "unknown_tool",
    "unknown tool",
})


# ─────────────── classifier ───────────────


def _extract_error_string(raw: Union[dict, str, Exception, None]) -> str:
    """Pull a single string suitable for keyword matching out of any
    accepted classify() input."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Exception):
        return f"{type(raw).__name__}: {raw}"
    if isinstance(raw, dict):
        # Top-level error wins (this is what execute_tool envelope uses).
        for key in ("error", "hint", "message"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
    # Anything else — best-effort string cast (won't match anything but
    # safer than raising).
    try:
        return str(raw)
    except Exception:  # noqa: BLE001 — never let classify itself fail
        return ""


def classify(
    raw: Union[dict, str, Exception, None],
) -> Type[Exception]:
    """Classify a tool error into one of the three error classes.

    :param raw: the tool result dict (envelope or nested), bare error
        string, or raised exception. Anything else falls through to
        ``TransientToolError`` (conservative default).
    :return: one of :class:`PermanentToolError`,
        :class:`TransientToolError`, :class:`HallucinationError` (the
        class itself, not an instance — callers compare with ``is``).

    Order of precedence:
      1. Hallucination keywords (most specific).
      2. Permanent keywords.
      3. Transient keywords.
      4. Default → Transient.
    """
    err_str = _extract_error_string(raw)
    if not err_str:
        return TransientToolError

    err_lower = err_str.lower()

    # 1. Hallucination check first — `tool_not_found` is unambiguous.
    for kw in HALLUCINATION_KEYWORDS:
        if kw.lower() in err_lower:
            return HallucinationError

    # 2. Permanent next — schema/permission/etc are deterministic.
    for kw in PERMANENT_KEYWORDS:
        if kw.lower() in err_lower:
            return PermanentToolError

    # 3. Transient — network/upstream temporary.
    for kw in TRANSIENT_KEYWORDS:
        if kw.lower() in err_lower:
            return TransientToolError

    # 4. Conservative default: unknown → transient (retry-friendly).
    return TransientToolError


__all__ = [
    "PermanentToolError",
    "TransientToolError",
    "HallucinationError",
    "PERMANENT_KEYWORDS",
    "TRANSIENT_KEYWORDS",
    "HALLUCINATION_KEYWORDS",
    "classify",
]
