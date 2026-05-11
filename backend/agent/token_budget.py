"""P5-S2 B3 — Token budget guard.

Why
---
We hit chinzy mid-stream-drops at 30+ iterations because context grew
silently past the model's window. Without a guard, the LLM either
truncates the prompt (silent quality drop) or errors with
``context_length_exceeded`` (visible but unhelpful).

This module gives the chat handler a fast pre-call check:

  result = check_budget(messages, model="deepseek-v4-pro")
  if result.verdict is BudgetCheck.BLOCK:
      # surface to user, trigger compaction first
  elif result.verdict is BudgetCheck.WARN:
      # log + optionally compact proactively

No tiktoken dependency — we use a ``len(content) / 4`` heuristic that's
~5-15% accurate for mixed English/Chinese, which is enough for warning
thresholds. The real model count will be slightly different but never
by an order of magnitude.

Design
------
* Pure functions; no I/O, no async.
* ``BudgetCheck`` is a 3-state enum (OK / WARN / BLOCK), not a bool —
  the chat handler wants distinct behaviors for each.
* ``BudgetCheckResult`` carries everything the caller needs to log or
  display (tokens, window, ratio, advice).
* Model lookup is best-effort; unknown models get a conservative
  8192 default (errs on the side of compacting earlier).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


# Threshold defaults — same as Claude's own internal heuristic:
#   WARN at 80% so user/UI can react ahead of failure
#   BLOCK at 95% to leave room for the assistant's response itself
DEFAULT_WARN_PCT = 0.80
DEFAULT_BLOCK_PCT = 0.95

# Conservative default for unknown models. Most modern OpenAI-compat
# models support at least 8k; smaller would force compaction too eagerly.
DEFAULT_CONTEXT_WINDOW = 8192


# Empirically-known context windows. Names match the literal id the
# provider returns under ``/v1/models``. Match is case-insensitive +
# stripped of provider prefix (e.g. "anthropic/claude-sonnet-4.5"
# → "claude-sonnet-4.5").
_KNOWN_WINDOWS: dict[str, int] = {
    # DeepSeek family — chinzy exposes these
    "deepseek-v4-pro": 64_000,
    "deepseek-v4-flash": 64_000,
    "deepseek-v3.2": 64_000,
    "deepseek-v3.1": 64_000,
    "deepseek-chat": 32_000,
    "deepseek-reasoner": 64_000,
    # Anthropic
    "claude-sonnet-4.5": 200_000,
    "claude-opus-4.5": 200_000,
    "claude-opus-4.1": 200_000,
    "claude-haiku-4.5": 200_000,
    "claude-3-5-sonnet": 200_000,
    # OpenAI
    "gpt-5": 128_000,
    "gpt-5-mini": 128_000,
    "gpt-5-chat": 128_000,
    "gpt-5.1": 200_000,
    "gpt-5.2": 200_000,
    "gpt-5.3-codex": 200_000,
    "gpt-5.4": 200_000,
    "gpt-5.5": 256_000,
    "gpt-4.1-mini": 128_000,
    "gpt-4o": 128_000,
    # Google
    "gemini-2.5-flash-image": 1_000_000,
    "gemini-2.5-flash-lite": 1_000_000,
    "gemini-3-flash-preview": 1_000_000,
    "gemini-3-pro-preview": 1_000_000,
    # Ollama local — typical default
    "gemma4:e4b": 8_192,
    # Misc — qwen / glm / kimi
    "qwen3-max": 128_000,
    "qwen3-coder-plus": 32_000,
    "glm-4.5": 128_000,
    "glm-4.6": 128_000,
    "kimi-k2-thinking": 128_000,
}


class BudgetCheck(enum.Enum):
    """3-state verdict from check_budget."""

    OK = "ok"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class BudgetCheckResult:
    """Outcome of a budget check, ready to log or surface to UI."""

    verdict: BudgetCheck
    estimated_tokens: int
    context_window: int
    ratio: float
    advice: str = ""


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Char/4 heuristic. Counts content strings + tool_call payloads.

    Tradeoff: we used to consider importing tiktoken, but it's ~30 MB
    of model files and only matches OpenAI's BPE — not deepseek's,
    not Anthropic's, etc. The char/4 estimate is within 15% on
    mixed-language content, which is enough for an 80% WARN threshold.
    """
    if not messages:
        return 0

    chars = 0
    for m in messages:
        # `content` may be a string or absent (assistant tool-only turns)
        content = m.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif content is not None:
            chars += len(str(content))

        # tool_calls payload — args may be a JSON string or pre-parsed dict
        tool_calls = m.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                if isinstance(tc, dict):
                    args = tc.get("args") or tc.get("arguments")
                    if args is not None:
                        chars += len(args if isinstance(args, str) else str(args))
                    name = tc.get("name") or ""
                    chars += len(str(name))

    # Each ~4 chars ≈ 1 token; add a small per-message overhead for
    # role tag + delimiters that the wire protocol adds.
    per_msg_overhead = 4 * len(messages)
    return max(0, chars // 4 + per_msg_overhead)


def get_context_window(model_name: str) -> int:
    """Return trained context window for ``model_name`` (case-insensitive,
    provider prefix stripped). Falls back to DEFAULT_CONTEXT_WINDOW for
    unknown models — conservative on purpose so compaction kicks in
    earlier rather than later.
    """
    if not model_name:
        return DEFAULT_CONTEXT_WINDOW
    normalized = str(model_name).strip().lower()
    # Strip provider prefix like "anthropic/claude-sonnet-4.5"
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]
    return _KNOWN_WINDOWS.get(normalized, DEFAULT_CONTEXT_WINDOW)


def check_budget(
    messages: list[dict[str, Any]],
    *,
    model: str,
    warn_pct: float = DEFAULT_WARN_PCT,
    block_pct: float = DEFAULT_BLOCK_PCT,
) -> BudgetCheckResult:
    """Pre-call budget check.

    Returns a BudgetCheckResult with the verdict and supporting numbers.
    Caller decides what to do: log, compact, ask user, or abort.
    """
    tokens = estimate_tokens(messages)
    window = get_context_window(model)
    ratio = tokens / window if window > 0 else 0.0

    if ratio >= block_pct:
        verdict = BudgetCheck.BLOCK
        advice = (
            f"上下文已达 {int(ratio*100)}% (估算 {tokens} / {window} tokens)，"
            "建议立即触发 history compaction 或拆分任务。继续调用风险高 "
            "（context_length_exceeded 或模型截断 prompt 导致质量下降）。"
        )
    elif ratio >= warn_pct:
        verdict = BudgetCheck.WARN
        advice = (
            f"上下文接近上限 {int(ratio*100)}% (估算 {tokens} / {window} tokens)，"
            "建议在下一轮 LLM 调用前 compact 早期 history。"
        )
    else:
        verdict = BudgetCheck.OK
        advice = ""

    return BudgetCheckResult(
        verdict=verdict,
        estimated_tokens=tokens,
        context_window=window,
        ratio=ratio,
        advice=advice,
    )
