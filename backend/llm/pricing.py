"""Per-model USD pricing table, indexed by (provider, model).

Units: USD per 1M tokens. input = prompt, output = completion,
cache_read = Anthropic prompt-cache read / OpenAI cached_tokens,
cache_write = Anthropic prompt-cache write.

ALL VALUES BELOW ARE SNAPSHOT ESTIMATES AS OF 2026-04. Provider pricing
pages change. P4-S12 ship runbook MUST re-verify every entry before
cutting v0.6.0-rc1.

Source disclosures (so reviewers can sanity-check):
    - Anthropic: https://anthropic.com/pricing
        claude-sonnet-4-5: $3 / 1M input, $15 / 1M output,
                           $0.30 cache read, $3.75 cache write (5m ephemeral)
        claude-haiku-4-5:  $1 / 1M input, $5 / 1M output,
                           $0.10 cache read, $1.25 cache write
    - OpenAI: https://openai.com/pricing
        gpt-4o:      $2.50 / 1M input, $10 / 1M output,
                     $1.25 cached input (gpt-4o prompt caching Oct 2024)
        gpt-4o-mini: $0.15 / 1M input, $0.60 / 1M output,
                     $0.075 cached input
    - Google: https://ai.google.dev/pricing (values in flux)
        gemini-1.5-pro:   $1.25 / 1M input, $5 / 1M output (est, paid tier)
        gemini-1.5-flash: $0.075 / 1M input, $0.30 / 1M output

Unknown models fall back to UNKNOWN_MODEL_PRICE (pessimistic, so nobody
silently burns budget on an undeclared model).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """USD cost per 1M tokens. All four axes billed separately."""

    input_per_m: float
    output_per_m: float
    cache_read_per_m: float = 0.0
    cache_write_per_m: float = 0.0


# Pessimistic fallback for un-priced models. Intentional: if we forget
# to add a new model here, budget math over-charges rather than under-charges.
UNKNOWN_MODEL_PRICE = ModelPrice(
    input_per_m=10.0,
    output_per_m=30.0,
    cache_read_per_m=1.0,
    cache_write_per_m=12.0,
)


PRICING: dict[tuple[str, str], ModelPrice] = {
    # ─────────────────── Anthropic ───────────────────
    ("anthropic", "claude-sonnet-4-5"): ModelPrice(
        input_per_m=3.0,
        output_per_m=15.0,
        cache_read_per_m=0.30,
        cache_write_per_m=3.75,
    ),
    ("anthropic", "claude-haiku-4-5"): ModelPrice(
        input_per_m=1.0,
        output_per_m=5.0,
        cache_read_per_m=0.10,
        cache_write_per_m=1.25,
    ),
    # ─────────────────── OpenAI ───────────────────
    ("openai", "gpt-4o"): ModelPrice(
        input_per_m=2.50,
        output_per_m=10.0,
        cache_read_per_m=1.25,
        cache_write_per_m=0.0,  # OpenAI cache writes are free (Oct 2024)
    ),
    ("openai", "gpt-4o-mini"): ModelPrice(
        input_per_m=0.15,
        output_per_m=0.60,
        cache_read_per_m=0.075,
        cache_write_per_m=0.0,
    ),
    # ─────────────────── Gemini ───────────────────
    # NEEDS S12 VERIFY: Google shifts tier boundaries frequently. Free tier
    # still exists for 1.5-flash but rate limits are unstable; values below
    # are the paid-tier rates.
    ("gemini", "gemini-1.5-pro"): ModelPrice(
        input_per_m=1.25,
        output_per_m=5.0,
        cache_read_per_m=0.0,  # Gemini caching requires >32K tokens, skipped for MVP
        cache_write_per_m=0.0,
    ),
    ("gemini", "gemini-1.5-flash"): ModelPrice(
        input_per_m=0.075,
        output_per_m=0.30,
        cache_read_per_m=0.0,
        cache_write_per_m=0.0,
    ),
}


def get_price(provider: str, model: str) -> ModelPrice:
    """Lookup pricing; fall back to pessimistic default for unknown models."""
    return PRICING.get((provider.lower(), model), UNKNOWN_MODEL_PRICE)


def estimate_cost_usd(provider: str, model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Compute USD cost for a single call.

    Provider bills the four token axes independently. cache_read_tokens
    are NOT counted as part of input_tokens (adapters already split them
    into the usage struct).
    """
    price = get_price(provider, model)
    return (
        (input_tokens / 1_000_000.0) * price.input_per_m
        + (output_tokens / 1_000_000.0) * price.output_per_m
        + (cache_read_tokens / 1_000_000.0) * price.cache_read_per_m
        + (cache_write_tokens / 1_000_000.0) * price.cache_write_per_m
    )
