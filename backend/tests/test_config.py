# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for config.py loader resilience.

Rationale (P2-1-S1 review follow-up):
    load_config() currently does `XxxConfig(**raw["xxx"])`. That blows up with
    TypeError when the TOML contains a key no longer present in the dataclass
    — e.g. after a field gets renamed or removed in a future slice, any user
    still running an old config.toml will be locked out on startup.

    Harden the loader to silently drop unknown keys (a warning, not a crash).
    Dataclass defaults already cover *missing* keys — this test is strictly
    about *extra* keys in the user's config file.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from config import TTSConfig, load_config


def test_load_config_ignores_unknown_toml_keys(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [llm]
            strategy = "local_first"
            daily_budget_cny = 10.0
            # Simulates a knob that existed in a prior release but got
            # removed. An untouched user config.toml would still carry it.
            future_experimental_knob = "value-from-old-release"

            [llm.local]
            model = "gemma4:e4b"
            base_url = "http://localhost:11434/v1"
            api_key = "ollama"
            temperature = 0.7
            max_tokens = 2048
            """
        ).strip()
    )

    cfg = load_config(cfg_path)
    # The known fields still load correctly.
    assert cfg.llm.strategy == "local_first"
    assert cfg.llm.local.model == "gemma4:e4b"
    assert cfg.llm.local.api_key == "ollama"
    # The unknown field was filtered out (no attribute, no crash).
    assert not hasattr(cfg.llm, "future_experimental_knob")


def test_load_config_parses_llm_routing_with_local_and_cloud(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [llm]
        strategy = "local_first"
        daily_budget_cny = 10.0

        [llm.local]
        model = "gemma4:e4b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
        temperature = 0.7

        [llm.cloud]
        model = "qwen3.6-plus"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key = "sk-test-not-real"
        temperature = 0.7
    """).strip())

    cfg = load_config(cfg_path)
    assert cfg.llm.strategy == "local_first"
    assert cfg.llm.daily_budget_cny == 10.0
    assert cfg.llm.local.model == "gemma4:e4b"
    assert cfg.llm.cloud is not None
    assert cfg.llm.cloud.model == "qwen3.6-plus"


def test_load_config_llm_cloud_optional(tmp_path):
    """No [llm.cloud] section → cfg.llm.cloud is None, router runs local-only."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [llm]
        strategy = "local_first"

        [llm.local]
        model = "gemma4:e4b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
    """).strip())

    cfg = load_config(cfg_path)
    assert cfg.llm.cloud is None
    assert cfg.llm.local.model == "gemma4:e4b"


def test_load_config_promotes_unified_llm_schema(tmp_path, caplog):
    """P4-S20-LLM-Unified: 单 [llm] 段含 endpoint 字段（model/base_url/...）
    应被 promote 到 routing.local，让下游代码无感升级。

    背景：原 P2-1-S2 schema 把 endpoint 字段拆到 [llm.local] / [llm.cloud]，
    现在合并回 [llm] 单段。这个测试验证迁移路径：用户写新 schema → cfg.llm
    .local 拿到正确 model/base_url。

    旧行为 (pre-P2-1-S2 警告)被本次重构 deprecated。
    """
    import logging

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [llm]
        model = "qwen2.5:7b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
        temperature = 0.5
    """).strip())

    with caplog.at_level(logging.INFO):
        cfg = load_config(cfg_path)

    # 用户填的 model 被正确读到（不再丢失为默认值）
    assert cfg.llm.local.model == "qwen2.5:7b"
    assert cfg.llm.local.base_url == "http://localhost:11434/v1"
    assert cfg.llm.local.api_key == "ollama"
    assert cfg.llm.local.temperature == 0.5
    # cloud 段默认不存在
    assert cfg.llm.cloud is None
    # info 日志会汇报这次 promote
    assert any("llm_unified_schema_loaded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# P3-S1: TTSConfig.model_dir default + legacy './assets/...' migration
# ---------------------------------------------------------------------------


def test_tts_config_model_dir_default() -> None:
    """P3-S1: TTSConfig.model_dir now defaults to a bare subfolder name,
    not the legacy relative path './assets/cosyvoice2'."""
    cfg = TTSConfig()
    assert cfg.model_dir == "cosyvoice2"


def test_load_config_tts_reads_model_dir(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [tts]
        model_dir = "cosyvoice2-instruct"
    """).strip(), encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.tts.model_dir == "cosyvoice2-instruct"


def test_load_config_tts_legacy_model_dir_normalized(tmp_path, caplog):
    """P3-S1: old config with `./assets/cosyvoice2` is auto-stripped to
    `cosyvoice2` and a WARNING is logged. Hardcoded-relative paths break
    under PyInstaller, so we nudge users off them without crashing."""
    import logging

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [tts]
        model_dir = "./assets/cosyvoice2"
    """).strip(), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cfg = load_config(cfg_path)

    assert cfg.tts.model_dir == "cosyvoice2"
    assert any("legacy" in r.message.lower() for r in caplog.records)


def test_load_config_tts_legacy_bare_assets_prefix(tmp_path, caplog):
    """Also handle `assets/cosyvoice2` (no leading ./)."""
    import logging

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [tts]
        model_dir = "assets/cosyvoice2"
    """).strip(), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cfg = load_config(cfg_path)

    assert cfg.tts.model_dir == "cosyvoice2"


def test_load_config_tts_non_legacy_value_unchanged(tmp_path, caplog):
    """A plain subfolder name must NOT be mangled and must not warn."""
    import logging

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [tts]
        model_dir = "cosyvoice2"
    """).strip(), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cfg = load_config(cfg_path)

    assert cfg.tts.model_dir == "cosyvoice2"
    assert not any("legacy" in r.message.lower() for r in caplog.records)


def test_load_config_parses_skills_codify_flag(tmp_path: Path) -> None:
    """2026-06-06 真机手测抓 bug 回归：load_config 必须解析 [skills.codify]
    子表（原加载器只 pop auto_disclosure，丢了 codify → flag enabled=true 被忽略
    → 技能自创确认卡生产永不弹）。同时校验 auto_disclosure 仍正常。"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [skills.auto_disclosure]
            enabled = true

            [skills.codify]
            enabled = true
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    assert cfg.skills.auto_disclosure.enabled is True
    assert cfg.skills.codify.enabled is True, (
        "[skills.codify] enabled=true 未被解析 → 技能自创生产 dead（真机抓的 bug）"
    )


def test_load_config_skills_codify_defaults_off(tmp_path: Path) -> None:
    """无 [skills.codify] 段时默认 enabled=False（BC，flag-OFF 字节契约）。"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("schema_version = 1\n")
    cfg = load_config(cfg_path)
    assert cfg.skills.codify.enabled is False
