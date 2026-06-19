# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase 1.1.3 — ContextConfig per-model 比例阈值（D2）单测。

覆盖 spec `per-model-context` 的 "ContextManager thresholds derive from
resolved model info" Requirement 两个场景：
  1. Compaction trigger scales with model window
  2. v2_enabled=false falls back to legacy absolute thresholds

设计（design.md D2）：
  - v2（默认）：ContextConfig 注入 ModelContextInfo，阈值是 @property，
    按 resolved window 比例算（compact_at_tokens = window*compact_at_pct，
    tool_result_threshold = max(8000, window//25)）
  - v1（v2_enabled=false）：退回旧的绝对值 ContextConfig（Strangler-Fig）
"""
from __future__ import annotations

from agent.context_manager import ContextConfig
from llm.model_info import resolve


def test_v2_compaction_trigger_scales_with_model_window():
    """Scenario: Compaction trigger scales with model window.

    deepseek-v4-pro (1M, compact 0.75) → compact_at_tokens = 750_000
    切到 claude-sonnet-4-5 (200K, compact 0.83) → 166_000，无需改配置文件。
    """
    deepseek = resolve("deepseek-v4-pro", project_root=None)
    cfg = ContextConfig(model_info=deepseek)
    assert cfg.compact_at_tokens == 750_000

    sonnet = resolve("claude-sonnet-4-5", project_root=None)
    cfg2 = ContextConfig(model_info=sonnet)
    assert cfg2.compact_at_tokens == 166_000


def test_v2_tool_result_threshold_scales_by_window():
    """2026-06-13 收紧(上下文外置): max(6_000, min(12_000, window//60))。

    1M → 12_000 cap；200K → 6_000 floor；32K _default → 6_000 floor。
    原 window//25(1M→40K)留存太宽,是真机调研会话上下文爆炸主因。
    """
    deepseek = resolve("deepseek-v4-pro", project_root=None)
    assert ContextConfig(model_info=deepseek).tool_result_threshold == 12_000

    sonnet = resolve("claude-sonnet-4-5", project_root=None)
    assert ContextConfig(model_info=sonnet).tool_result_threshold == 6_000

    fallback = resolve("unknown-7b", project_root=None)  # _default 32K
    assert ContextConfig(model_info=fallback).tool_result_threshold == 6_000


def test_v2_budget_warn_block_still_present_and_ratio_based():
    """budget_warn_pct/budget_block_pct 比例保留（0.80/0.95 与业界一致）。"""
    deepseek = resolve("deepseek-v4-pro", project_root=None)
    cfg = ContextConfig(model_info=deepseek)
    assert cfg.budget_warn_pct == 0.80
    assert cfg.budget_block_pct == 0.95


def test_v2_effective_window_uses_model_effective_pct():
    """有效窗口 = context_window * effective_pct（budget 分母用，D2）。"""
    deepseek = resolve("deepseek-v4-pro", project_root=None)
    cfg = ContextConfig(model_info=deepseek)
    # 1_000_000 * 0.95
    assert cfg.effective_window_tokens == 950_000


def test_v2_model_info_swap_recomputes_with_no_file_edit():
    """切模型即重算：同一进程内换 model_info → compact_at_tokens 自动变。"""
    cfg = ContextConfig(model_info=resolve("deepseek-v4-pro"))
    assert cfg.compact_at_tokens == 750_000
    # 新 session 用新 model → 新 ContextConfig，无 config.toml 编辑
    cfg_b = ContextConfig(model_info=resolve("claude-opus-4-5"))
    assert cfg_b.compact_at_tokens == int(200_000 * 0.83)  # 166_000


def test_v1_legacy_falls_back_to_absolute_thresholds():
    """Scenario: v2_enabled=false falls back to legacy absolute thresholds.

    不注入 model_info 且 v2_enabled=False → 旧绝对值路径（Strangler-Fig
    回退闸）。沿用 2026-05-15 stop-gap 的绝对常量。
    """
    cfg = ContextConfig(v2_enabled=False)
    # 旧路径阈值是固定 int，不依赖 model window
    assert isinstance(cfg.tool_result_threshold, int)
    assert isinstance(cfg.compact_at_tokens, int)
    # 旧路径下切 model 不影响阈值（就是写死的常量）
    assert cfg.tool_result_threshold == cfg.tool_result_threshold
    # legacy budget pct 也在
    assert cfg.budget_warn_pct == 0.80
    assert cfg.budget_block_pct == 0.95


def test_v1_legacy_default_when_no_model_info_and_v2_off():
    """v2_enabled=False 时 model_info 可选，缺省走 legacy 常量。"""
    cfg = ContextConfig(v2_enabled=False)
    # legacy 绝对值（threshold 不动;head/tail 是两路共用经验值,
    # 2026-06-13 收紧到 2500/800,v1 同享）
    assert cfg.tool_result_threshold == 16_000
    assert cfg.tool_result_head == 2_500
    assert cfg.tool_result_tail == 800
    assert cfg.compact_message_threshold == 80
    assert cfg.compact_char_threshold == 300_000
    assert cfg.compact_keep_recent == 12
    assert cfg.skip_truncation_for_tools == {"fetch_tool_result"}


def test_v2_default_is_enabled_when_model_info_injected():
    """注入 model_info 时 v2_enabled 默认 True（per-model 是默认路径）。"""
    cfg = ContextConfig(model_info=resolve("deepseek-v4-pro"))
    assert cfg.v2_enabled is True
    # head/tail 仍可用（v2 也要切 tool_result），保留稳定默认(2500/800)
    assert cfg.tool_result_head == 2_500
    assert cfg.tool_result_tail == 800
    assert cfg.skip_truncation_for_tools == {"fetch_tool_result"}
