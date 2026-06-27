# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-0 tests — [features.problem_pipeline] config + ServiceContext key 注册。

覆盖（plans/2026-06-24-problem-handling-pipeline-maoxuan §6 WI-0）：
  - enabled 默认 true、各子 flag 默认 on（决策1：测试环境出厂即开）
  - analysis_model / self_check_model 默认 ""（决策3：留空=主 LLM）
  - [features.problem_pipeline] 嵌套子表能被 pop 解析（同 [memory.v2] 模式）
  - 老 config 缺该段时由 dataclass 默认值兜底（无 backfill 也能跑，决策1）
  - ServiceContext.register/get 4 个新 pipeline key 不抛 ValueError（B1）
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from config import ProblemPipelineConfig, load_config
from context import ServiceContext


def test_problem_pipeline_defaults_factory_on() -> None:
    """决策1：dataclass 默认值出厂即开，子 flag 默认 on。"""
    pp = ProblemPipelineConfig()
    assert pp.enabled is True
    assert pp.intent_triage is True
    assert pp.evidence_gate is True
    assert pp.plan_companion_enabled is True
    assert pp.self_check is True
    assert pp.self_check_heterogeneous is True
    assert pp.convergence_report_on_stop is True
    assert pp.observability_events is True
    # WI-4-B：预分析默认走 deepseek-v4-pro（relay 实测 stream+json_schema 稳，规避 gpt-5.5 间歇 502）；
    #          self_check 仍留空 = 主 LLM。
    assert pp.analysis_model == "deepseek-v4-pro"
    assert pp.self_check_model == ""
    assert pp.intent_clarify_threshold == 0.7
    assert pp.evidence_max_nudges == 2
    assert pp.evidence_investigative_tools == []
    # WI-4-B：deepseek-v4-pro thinking 慢 → 超时放宽 30s → 45s（给足时间，原 30s 仍偶超时）
    assert pp.analysis_timeout_s == 45.0


def test_problem_pipeline_parsed_from_nested_subtable(tmp_path: Path) -> None:
    """[features.problem_pipeline] 嵌套子表被 pop 解析（非平铺丢弃）。"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            [features]
            slash_commands = true

            [features.problem_pipeline]
            enabled = true
            evidence_gate = false
            evidence_max_nudges = 5
            analysis_model = "some-model"
            self_check = false
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    # 平铺 flag 仍然解析
    assert cfg.features.slash_commands is True
    # 嵌套子表覆盖默认
    pp = cfg.features.problem_pipeline
    assert pp.enabled is True
    assert pp.evidence_gate is False
    assert pp.evidence_max_nudges == 5
    assert pp.analysis_model == "some-model"
    assert pp.self_check is False
    # 未覆盖字段仍走默认
    assert pp.intent_triage is True


def test_problem_pipeline_absent_section_falls_back_to_defaults(tmp_path: Path) -> None:
    """老 config 缺该段 → dataclass 默认值兜底（决策1：无 backfill 也能跑）。"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            [features]
            goal_mode = true
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    pp = cfg.features.problem_pipeline
    assert pp is not None
    assert pp.enabled is True  # 默认兜底
    assert pp.intent_triage is True


def test_problem_pipeline_unknown_subkey_dropped(tmp_path: Path) -> None:
    """子表里的未知 key 被 _load_section 丢弃，不崩。"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            [features.problem_pipeline]
            enabled = false
            future_unknown_knob = "x"
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    pp = cfg.features.problem_pipeline
    assert pp.enabled is False
    assert not hasattr(pp, "future_unknown_knob")


def test_service_context_pipeline_keys_register_and_get() -> None:
    """B1：4 个 pipeline service key register/get(None 占位) 不抛 ValueError。"""
    sc = ServiceContext()
    for key in (
        "problem_pipeline",
        "pipeline_evidence_gate",
        "pipeline_self_check_gate",
        "pipeline_convergence_controller",
    ):
        sc.register(key, None)  # flag off 时的占位写法
        assert sc.get(key) is None
    # 注册真实对象也能取回
    sentinel = object()
    sc.register("problem_pipeline", sentinel)
    assert sc.get("problem_pipeline") is sentinel
