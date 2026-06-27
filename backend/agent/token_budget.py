# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 B3 — Token budget guard.

Why
---
We hit the relay mid-stream-drops at 30+ iterations because context grew
silently past the model's window. Without a guard, the LLM either
truncates the prompt (silent quality drop) or errors with
``context_length_exceeded`` (visible but unhelpful).

This module gives the chat handler a fast pre-call check:

  result = check_budget(messages, model="deepseek-v4-pro")
  if result.verdict is BudgetCheck.BLOCK:
      # surface to user, trigger compaction first
  elif result.verdict is BudgetCheck.WARN:
      # log + optionally compact proactively

Token 计数统一委托 ``deskpet.agent.tokens.count_messages_tokens``（CJK-aware
启发式，可选 tiktoken 精度增强），消除散落的 ``len//4`` 口径不一致。对中文
不再低估，足够驱动 warning 阈值；真实 model count 略有出入但绝不差一个数量级。

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
import re
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
#
# 2026-05-15 (Phase 1.1 followup): this table is now the **legacy
# fallback only**. The authoritative per-model window comes from
# ``llm.model_info`` (3-layer builtin/global/project resolve), threaded
# through ``ContextManager.check_budget → check_budget(context_window=)``.
# This table still backs (a) v2_enabled=False rollback, (b) direct
# callers that don't pass context_window, (c) models not yet in
# model_info.BUILTIN (gpt-4o / qwen / glm / kimi …). The stale
# deepseek-v4* = 64_000 values (a 6.4% under-count vs the real 1M
# window) were the headline bug — synced to reality below.
_KNOWN_WINDOWS: dict[str, int] = {
    # DeepSeek family — the relay exposes these. v4 is 1M context (matches
    # llm.model_info.BUILTIN["deepseek-v4-pro"]).
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v3.2": 128_000,
    "deepseek-v3.1": 128_000,
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 128_000,
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


# CJK 字符(汉字/假名/全角符号)在主流 BPE 里 ≈1 token/字,而 char/4 会低估
# ~4 倍。FP-2 真机实测:中文会话 real prompt_tokens=28k 被估 ~7k,导致
# compaction(24k 阈值)永不触发、直到撑爆 32k 窗口。CJK 字符按等效 4 个
# ASCII 字符计入,使最终 //4 后 ≈1 token/字。
_CJK_RE = re.compile(
    r"[　-ヿ㐀-䶿一-鿿豈-﫿＀-￯]"
)


def _weighted_chars(s: str) -> int:
    """Return ASCII-equivalent char count: CJK chars weigh 4× (≈1 token each).

    真机校准(FP-2 TC-2.1 第二刀): markdown/路径/代码密集的英文实测
    ~3 char/token(relay prompt_tokens=34008 vs 旧估 <24000,低估 30%+),
    纯散文才接近 4。ASCII 部分按 ×8/7 上调(等效 ~3.5 char/token),
    宁可早压不可爆窗。
    """
    cjk = len(_CJK_RE.findall(s))
    ascii_part = len(s) - cjk
    return ascii_part + ascii_part // 7 + cjk * 4


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """CJK-aware token 估算。**统一委托** ``deskpet.agent.tokens.count_messages_tokens``
    (同一口径,可选 tiktoken 精度),消除散落 ``len//4`` 不一致(优化 #1+#3)。

    历史取舍仍成立: 默认不上 tiktoken(~30MB BPE + 中国网络 + 跨 provider 不准),
    启发式 CJK-aware + 安全偏上 + relay 真实反馈三刀制实战足够;tiktoken 仅
    ``DESKPET_TIKTOKEN=1`` 时作精度增强。``_weighted_chars`` 保留供 BC。
    """
    if not messages:
        return 0
    from deskpet.agent.tokens import count_messages_tokens
    return count_messages_tokens(messages)


def get_context_window(model_name: str) -> int:
    """Return trained context window for ``model_name`` (case-insensitive,
    provider prefix stripped).

    Resolution order (Phase 1.1 followup — single source of truth is
    ``llm.model_info`` when it knows the model):

      1. ``llm.model_info.BUILTIN[model]`` — the per-model map. Consulted
         directly (no ``resolve()`` call → no per-budget-check log spam,
         no I/O); the 3-layer global/project override is already baked
         into ``ContextConfig.model_info`` and reaches us via the
         explicit ``context_window=`` arg in :func:`check_budget`, so
         here we only need the builtin floor.
      2. ``_KNOWN_WINDOWS`` — legacy fallback for models not yet in
         model_info.BUILTIN (gpt-4o / qwen / glm / kimi …).
      3. ``DEFAULT_CONTEXT_WINDOW`` — conservative, so compaction kicks
         in earlier rather than later for genuinely unknown models.
    """
    if not model_name:
        return DEFAULT_CONTEXT_WINDOW
    normalized = str(model_name).strip().lower()
    # Strip provider prefix like "anthropic/claude-sonnet-4.5"
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]

    # 1. per-model map (authoritative for models it knows)
    try:
        from llm.model_info import BUILTIN

        info = BUILTIN.get(normalized)
        if info is not None:
            return info.context_window
    except Exception:  # noqa: BLE001 — model_info import must never break budget
        pass

    # 2/3. legacy table → conservative default
    return _KNOWN_WINDOWS.get(normalized, DEFAULT_CONTEXT_WINDOW)


def check_budget(
    messages: list[dict[str, Any]],
    *,
    model: str,
    warn_pct: float = DEFAULT_WARN_PCT,
    block_pct: float = DEFAULT_BLOCK_PCT,
    context_window: int | None = None,
    real_prompt_tokens_floor: int = 0,
) -> BudgetCheckResult:
    """Pre-call budget check.

    Returns a BudgetCheckResult with the verdict and supporting numbers.
    Caller decides what to do: log, compact, ask user, or abort.

    ``context_window``: when provided (e.g. by
    :meth:`ContextManager.check_budget` passing
    ``config.model_info.context_window``), it is **authoritative** — the
    full 3-layer per-model resolution (builtin/global/project) already
    happened at ContextConfig construction, so we must NOT re-derive a
    (possibly stale) window from :func:`get_context_window` here. When
    ``None`` (legacy / v1-rollback / direct callers) we fall back to the
    name-based lookup.

    ``real_prompt_tokens_floor``: the actual ``prompt_tokens`` of the last
    real LLM call (``response.usage.input_tokens``), used as a *floor* on
    the estimate. ``estimate_tokens(messages)`` only counts
    ``working_messages`` (the conversation history) — it does **not** see the
    fixed base every request carries (system persona + tool schemas + skill
    prelude, often several thousand tokens, added by the provider at call
    time). Without this floor the gate undercounts the real prompt and can
    fail to fire even when the model receives an over-window prompt (observed:
    real prompt 113% of window, ratio seen as 16%, no BLOCK). The real
    last-prompt size already includes that base, so ``max(estimate, floor)``
    gives a window-accurate numerator. Default ``0`` → byte-level BC (the
    floor never raises the estimate; identical to the old working_messages-only
    behaviour). Mirrors the same signal the compaction trigger uses.
    """
    tokens = estimate_tokens(messages)
    if real_prompt_tokens_floor > tokens:
        tokens = int(real_prompt_tokens_floor)
    window = (
        context_window
        if context_window is not None and context_window > 0
        else get_context_window(model)
    )
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
