# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 2.1: tool error classifier.

The classifier turns a raw tool error (dict / str / Exception) into one of
three classes — ``PermanentToolError``, ``TransientToolError``,
``HallucinationError`` — so the agent loop can decide whether to break
out, retry via ReAct, or hand off to the supervisor.

Conservative default: unknown errors → ``TransientToolError`` (better to
waste a retry than to incorrectly kill a turn).
"""
from __future__ import annotations

import pytest

from agent import errors


# ─────────────── classify(dict) ───────────────


def test_classify_missing_param_is_permanent() -> None:
    """`missing required parameter: <field>` is a schema-level mistake;
    retrying with the same args will fail the same way → PermanentToolError."""
    assert (
        errors.classify({"error": "missing required parameter: path"})
        is errors.PermanentToolError
    )


def test_classify_missing_required_parameters_is_permanent() -> None:
    """Variant phrasing used by some validators."""
    assert (
        errors.classify({"error": "missing_required_parameters: ['path']"})
        is errors.PermanentToolError
    )


def test_classify_timeout_is_transient() -> None:
    """Network / IO timeout → may succeed on retry → TransientToolError."""
    assert errors.classify({"error": "timeout"}) is errors.TransientToolError


def test_classify_circuit_open_is_permanent() -> None:
    """Circuit breaker is OPEN — retrying the same tool now is guaranteed to
    fail → PermanentToolError (agent should pivot, not retry)."""
    assert errors.classify({"error": "circuit_open"}) is errors.PermanentToolError


def test_classify_would_overwrite_is_permanent() -> None:
    """write_file overwrite guard — same args will keep failing."""
    assert (
        errors.classify({"error": "would_overwrite: file exists, pass overwrite=true"})
        is errors.PermanentToolError
    )


def test_classify_file_not_found_is_permanent() -> None:
    """A file that doesn't exist won't appear by retrying."""
    assert (
        errors.classify({"error": "file_not_found: /tmp/nope.txt"})
        is errors.PermanentToolError
    )


def test_classify_permission_denied_is_permanent() -> None:
    """Permission gate denied — retry won't change the gate's mind."""
    assert (
        errors.classify({"error": "permission_denied (source=user)"})
        is errors.PermanentToolError
    )


def test_classify_schema_invalid_is_permanent() -> None:
    assert (
        errors.classify({"error": "schema_invalid: 'count' must be int"})
        is errors.PermanentToolError
    )


def test_classify_503_is_transient() -> None:
    """Upstream temporary failure → retry could work."""
    assert errors.classify({"error": "HTTP 503 from provider"}) is errors.TransientToolError


def test_classify_502_is_transient() -> None:
    assert errors.classify({"error": "502 Bad Gateway"}) is errors.TransientToolError


def test_classify_rate_limit_is_transient() -> None:
    assert errors.classify({"error": "rate_limit exceeded, retry later"}) is errors.TransientToolError


def test_classify_connection_reset_is_transient() -> None:
    assert errors.classify({"error": "connection_reset by peer"}) is errors.TransientToolError


def test_classify_server_disconnected_is_transient() -> None:
    assert (
        errors.classify({"error": "Server disconnected without sending a response"})
        is errors.TransientToolError
    )


def test_classify_unknown_defaults_transient() -> None:
    """Conservative default: never-seen-before string → assume transient.
    Better to waste a retry than to give up too early on something the
    LLM might solve with adjusted args."""
    assert (
        errors.classify({"error": "weird-thing-we-never-saw-before-xyz123"})
        is errors.TransientToolError
    )


def test_classify_no_error_field_defaults_transient() -> None:
    """Empty / missing error string still classifies (fall-through)."""
    assert errors.classify({"ok": False}) is errors.TransientToolError


def test_classify_hallucinated_tool() -> None:
    """`tool_not_found` is a strong hallucination signal — the LLM
    invoked a tool that doesn't exist. Different recovery path: route
    to supervisor, not naive retry."""
    assert errors.classify({"error": "tool_not_found"}) is errors.HallucinationError


def test_classify_unknown_tool_string_is_hallucination() -> None:
    """Variant phrasing from registry.execute_tool."""
    assert errors.classify({"error": "unknown tool: do_magic"}) is errors.HallucinationError


# ─────────────── classify(str) ───────────────


def test_classify_string_input_works() -> None:
    """classify must accept a bare error string too — some call sites
    only have the message."""
    assert errors.classify("missing required parameter: path") is errors.PermanentToolError
    assert errors.classify("timeout") is errors.TransientToolError
    assert errors.classify("tool_not_found") is errors.HallucinationError


def test_classify_empty_string_defaults_transient() -> None:
    assert errors.classify("") is errors.TransientToolError


# ─────────────── classify(Exception) ───────────────


def test_classify_exception_input_works() -> None:
    """classify must accept an Exception (str(exc) is inspected)."""
    assert errors.classify(TimeoutError("timeout while reading")) is errors.TransientToolError
    assert (
        errors.classify(ValueError("missing required parameter: path"))
        is errors.PermanentToolError
    )


# ─────────────── nested envelope (P5-S2 reality) ───────────────


def test_classify_envelope_with_nested_result_json() -> None:
    """In the agent_loop reality, execute_tool returns
    ``{"ok": False, "error": "...", "result": None}``. The top-level
    `error` is what we classify on. This is the most common path."""
    envelope = {"ok": False, "result": None, "error": "missing required parameter: path"}
    assert errors.classify(envelope) is errors.PermanentToolError


# ─────────────── keyword constants ───────────────


def test_keyword_constants_exposed() -> None:
    """Spec 2.7: keyword tables are module constants for easy extension."""
    assert isinstance(errors.PERMANENT_KEYWORDS, (set, frozenset, tuple, list))
    assert isinstance(errors.TRANSIENT_KEYWORDS, (set, frozenset, tuple, list))
    assert isinstance(errors.HALLUCINATION_KEYWORDS, (set, frozenset, tuple, list))
    # Sanity: each table has the canonical example
    assert any("missing required parameter" in k for k in errors.PERMANENT_KEYWORDS)
    assert any("timeout" == k for k in errors.TRANSIENT_KEYWORDS)
    assert any("tool_not_found" == k for k in errors.HALLUCINATION_KEYWORDS)


# ─────────────── error class hierarchy ───────────────


def test_error_classes_are_exceptions() -> None:
    """Three classes must be Exception subclasses so callers can raise/catch."""
    assert issubclass(errors.PermanentToolError, Exception)
    assert issubclass(errors.TransientToolError, Exception)
    assert issubclass(errors.HallucinationError, Exception)


def test_error_classes_distinct() -> None:
    """No accidental subclassing between the three classes — they signal
    different recovery paths."""
    assert not issubclass(errors.PermanentToolError, errors.TransientToolError)
    assert not issubclass(errors.TransientToolError, errors.PermanentToolError)
    assert not issubclass(errors.HallucinationError, errors.PermanentToolError)
    assert not issubclass(errors.HallucinationError, errors.TransientToolError)
