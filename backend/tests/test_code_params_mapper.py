# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""code-session-model-params — exhaustive pure-mapper tests (no network).

Spec "Param→request mapping is total".
"""
from __future__ import annotations

import pytest

from llm.code_params import code_params_to_request as m


@pytest.mark.parametrize("bad", [None, {}, [], "x", 0, {"unused": 1}])
def test_empty_or_unknown_only_yields_provider_defaults(bad) -> None:
    assert m(bad) == {} or m(bad) == {}  # never raises; {} for None/empty


def test_none_and_empty_are_empty() -> None:
    assert m(None) == {}
    assert m({}) == {}


@pytest.mark.parametrize(
    "effort,expected",
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("extra_high", "high"),  # clamp
        ("max", "high"),  # clamp
        ("MAX", "high"),  # case-insensitive
    ],
)
def test_effort_mapping_and_clamp(effort, expected) -> None:
    assert m({"effort": effort})["reasoning_effort"] == expected


def test_unknown_effort_omitted_not_error() -> None:
    assert "reasoning_effort" not in m({"effort": "bogus"})
    assert m({"effort": 999}) == {} or "reasoning_effort" not in m({"effort": 999})


def test_thinking_false_drops_all_reasoning() -> None:
    out = m({"thinking": False, "effort": "max"})
    assert "reasoning_effort" not in out


def test_thinking_true_no_effort_defaults_medium() -> None:
    assert m({"thinking": True})["reasoning_effort"] == "medium"


def test_thinking_true_respects_effort() -> None:
    assert m({"thinking": True, "effort": "low"})["reasoning_effort"] == "low"


@pytest.mark.parametrize(
    "ctx,tokens",
    [("300k", 300_000), ("1m", 1_000_000), ("1M", 1_000_000)],
)
def test_context_window_hint(ctx, tokens) -> None:
    assert m({"context": ctx})["extra_body"]["context_window"] == tokens


def test_unknown_context_omitted() -> None:
    assert "extra_body" not in m({"context": "9000g"})


def test_fast_true_only() -> None:
    assert m({"fast": True})["extra_body"]["fast"] is True
    assert "extra_body" not in m({"fast": False})


def test_full_combo() -> None:
    out = m(
        {
            "thinking": True,
            "fast": True,
            "context": "1m",
            "effort": "high",
        }
    )
    assert out["reasoning_effort"] == "high"
    assert out["extra_body"] == {"context_window": 1_000_000, "fast": True}
