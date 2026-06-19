# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase 1.1 followup — token_budget must honor the per-model window.

Bug: token_budget.get_context_window had its own stale _KNOWN_WINDOWS
dict (deepseek-v4-pro=64_000) while llm.model_info.BUILTIN says
1_000_000. ContextManager.check_budget delegated to token_budget
WITHOUT passing the already-resolved per-model window, so budget BLOCK
fired at ~6.4% of deepseek's real capacity.

Fix: check_budget gains an optional context_window= param (authoritative
when provided); ContextManager passes config.model_info.context_window;
stale _KNOWN_WINDOWS deepseek entries synced to reality for legacy
direct callers.
"""
from __future__ import annotations

import pytest

from agent.token_budget import (
    DEFAULT_CONTEXT_WINDOW,
    BudgetCheck,
    check_budget,
    get_context_window,
)


def _msgs(n_chars: int) -> list[dict]:
    return [{"role": "user", "content": "x" * n_chars}]


def test_check_budget_explicit_window_is_authoritative():
    # 1M window: 100k chars ≈ 25k tokens → way under 80% of 1M → OK
    r = check_budget(_msgs(100_000), model="deepseek-v4-pro", context_window=1_000_000)
    assert r.context_window == 1_000_000
    assert r.verdict is BudgetCheck.OK


def test_check_budget_explicit_window_overrides_stale_dict():
    # Same payload, but if we (wrongly) used the old 64_000 dict it would
    # BLOCK. With explicit 1M it must be OK — proves the dict is bypassed.
    big = _msgs(300_000)  # ~75k tokens
    blocked = check_budget(big, model="deepseek-v4-pro", context_window=64_000)
    assert blocked.verdict is BudgetCheck.BLOCK  # 75k/64k > 95%
    ok = check_budget(big, model="deepseek-v4-pro", context_window=1_000_000)
    assert ok.verdict is BudgetCheck.OK  # 75k/1M < 80%


def test_check_budget_without_window_falls_back_legacy():
    # Back-compat: callers that don't pass context_window still work.
    r = check_budget(_msgs(10), model="claude-sonnet-4.5")
    assert r.context_window == 200_000  # from legacy table
    assert r.verdict is BudgetCheck.OK


def test_get_context_window_deepseek_no_longer_stale():
    # The headline bug: deepseek-v4-pro must NOT be 64_000 anymore.
    w = get_context_window("deepseek-v4-pro")
    assert w >= 1_000_000, f"deepseek-v4-pro window still stale: {w}"


def test_get_context_window_unknown_model_conservative_default():
    assert get_context_window("totally-made-up-model-7b") == DEFAULT_CONTEXT_WINDOW


def test_get_context_window_legacy_only_model_still_resolves():
    # gpt-4o is in the legacy table but not necessarily in model_info
    # BUILTIN — must still resolve, not collapse to DEFAULT.
    assert get_context_window("gpt-4o") >= 128_000


def test_context_manager_check_budget_uses_resolved_model_info():
    """Integration: ContextManager.check_budget must use the per-model
    window from config.model_info, not token_budget's stale dict."""
    from agent.context_manager import ContextConfig, ContextManager
    from llm.model_info import resolve

    info = resolve("deepseek-v4-pro", None)  # builtin → 1_000_000
    cm = ContextManager(config=ContextConfig(model_info=info, v2_enabled=True))
    # ~75k token payload: BLOCK under 64k, OK under 1M.
    r = cm.check_budget(_msgs(300_000), model="deepseek-v4-pro")
    assert r.context_window == info.context_window == 1_000_000
    assert r.verdict is BudgetCheck.OK


def test_context_manager_v1_rollback_still_uses_legacy_window():
    """v2_enabled=False (Strangler-Fig): legacy path must still work
    (no per-model authority — uses token_budget's table)."""
    from agent.context_manager import ContextConfig, ContextManager

    cm = ContextManager(config=ContextConfig(v2_enabled=False))
    r = cm.check_budget(_msgs(10), model="claude-sonnet-4.5")
    assert r.verdict is BudgetCheck.OK
    assert r.context_window == 200_000


# ---------------------------------------------------------------------------
# FP-2 TC-2.1 真机暴露 bug:char/4 启发式对 CJK 低估 ~4 倍 →
# 中文重度会话 real 28k tokens 被估 ~7k,compaction(24k 阈值)永不触发。
# 修复:CJK 字符按 ≈1 token/字计(等效 4 ASCII chars)。
# ---------------------------------------------------------------------------

def test_estimate_tokens_cjk_not_underestimated():
    """1000 个汉字 ≈ 1000 tokens(±30%),旧 char/4 只给 ~250 → 必须修。"""
    from agent.token_budget import estimate_tokens
    msgs = [{"role": "user", "content": "会" * 1000}]
    est = estimate_tokens(msgs)
    assert est >= 700, f"CJK underestimated: {est} for 1000 hanzi (expect ~1000)"


def test_estimate_tokens_ascii_unchanged():
    """纯 ASCII 仍 ~char/4(4000 chars ≈ 1000 tokens ±20%)。"""
    from agent.token_budget import estimate_tokens
    msgs = [{"role": "user", "content": "a" * 4000}]
    est = estimate_tokens(msgs)
    assert 800 <= est <= 1300, f"ASCII estimate drifted: {est}"


def test_estimate_tokens_mixed_cjk_ascii():
    """混合:500 汉字 + 2000 ASCII ≈ 500 + 500 = ~1000 tokens(±30%)。"""
    from agent.token_budget import estimate_tokens
    msgs = [{"role": "user", "content": "中" * 500 + "x" * 2000}]
    est = estimate_tokens(msgs)
    assert 700 <= est <= 1400, f"mixed estimate off: {est}"
