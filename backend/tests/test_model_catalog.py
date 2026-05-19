"""code-session-model-params — live model catalog + per-model caps.

The picker must not hardcode models; ``model_param_caps`` is the
per-family differentiation the user asked for (gpt-5.x exposes
reasoning_effort; claude opus/sonnet exposes thinking — they differ).
"""
from __future__ import annotations

import asyncio

from llm.model_catalog import (
    build_catalog,
    fetch_models,
    model_context_window,
    model_param_caps,
)


def test_openai_family_supports_effort() -> None:
    c = model_param_caps("gpt-5.5")
    assert c == {"thinking": True, "fast": True,
                 "context": True, "effort": True}
    assert model_param_caps("gpt-5.3-codex")["effort"] is True


def test_anthropic_family_thinking_not_effort() -> None:
    c = model_param_caps("claude-opus-4.5")
    assert c["thinking"] is True
    assert c["effort"] is False
    assert model_param_caps("claude-sonnet-4.5")["effort"] is False


def test_gemini_and_open_reasoning_no_effort() -> None:
    assert model_param_caps("gemini-3-pro-preview")["effort"] is False
    assert model_param_caps("gemini-3-pro-preview")["thinking"] is True
    assert model_param_caps("deepseek-v4-pro")["thinking"] is True
    assert model_param_caps("glm-4.7")["effort"] is False


def test_small_and_nonchat_models_minimal() -> None:
    mini = model_param_caps("gpt-5-mini")
    assert mini["thinking"] is False and mini["effort"] is False
    emb = model_param_caps("text-embedding-3-small")
    assert emb == {"thinking": False, "fast": False,
                   "context": False, "effort": False}
    img = model_param_caps("gpt-image-2")
    assert img["context"] is False


def test_unknown_model_conservative_default() -> None:
    c = model_param_caps("brand-new-model-x")
    # permissive but no effort (we only enable effort for known
    # reasoning_effort families).
    assert c["thinking"] is True
    assert c["effort"] is False


def test_build_catalog_dedupes_and_keeps_order() -> None:
    cat = build_catalog(["gpt-5.5", "gpt-5.5", "claude-opus-4.5", "", 123])  # type: ignore[list-item]
    ids = [m["id"] for m in cat]
    assert ids == ["gpt-5.5", "claude-opus-4.5"]
    assert cat[0]["caps"]["effort"] is True
    assert cat[1]["caps"]["effort"] is False
    assert "id" in cat[0] and "label" in cat[0] and "caps" in cat[0]


def test_model_context_window_is_per_model_not_uniform() -> None:
    # Not every model is 300K/1M — windows differ by family.
    assert model_context_window("gpt-5.5") == 400_000
    assert model_context_window("claude-opus-4.5") == 200_000
    assert model_context_window("gemini-3-pro-preview") == 1_000_000
    assert model_context_window("deepseek-v4-pro") == 1_000_000
    assert model_context_window("glm-4.7") == 200_000


def test_model_context_window_unknown_is_none_not_fabricated() -> None:
    # Genuinely unknown → None (UI shows "由 provider 决定"), never a
    # made-up number or the 32K _default fallback.
    assert model_context_window("totally-unknown-model-xyz") is None
    assert model_context_window("") is None


def test_build_catalog_carries_context_window() -> None:
    cat = build_catalog(["gpt-5.5", "claude-opus-4.5", "weird-x"])
    by_id = {m["id"]: m for m in cat}
    assert by_id["gpt-5.5"]["context_window"] == 400_000
    assert by_id["claude-opus-4.5"]["context_window"] == 200_000
    assert by_id["weird-x"]["context_window"] is None


def test_fetch_models_unreachable_returns_empty_never_raises() -> None:
    # Total/defensive: any failure → [] so caller falls back to the
    # registry's configured models list.
    out = asyncio.run(
        fetch_models("http://127.0.0.1:1/v1", "sk-x", timeout=0.2)
    )
    assert out == []
