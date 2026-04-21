"""Tests for [asr] section loading — P2-2-F1 hotwords support.

[asr] already existed (provider/model/device/compute_type). This slice
adds a `hotwords` list so short-audio phrases like "讲个笑话" get logit
bias and stop being misheard as "一个消化".
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from config import AppConfig, ASRConfig, load_config


def test_asr_config_hotwords_default_is_empty_list() -> None:
    """No hotwords by default — preserves current behavior for existing users."""
    cfg = ASRConfig()
    assert cfg.hotwords == []


def test_app_config_has_asr_field_with_hotwords() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.asr, ASRConfig)
    assert cfg.asr.hotwords == []


def test_load_config_without_asr_section_uses_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        schema_version = 1

        [backend]
        host = "127.0.0.1"
    """).strip())
    cfg = load_config(cfg_path)
    assert cfg.asr.hotwords == []
    assert cfg.asr.model == "large-v3-turbo"


def test_load_config_asr_reads_hotwords_list(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [asr]
        provider = "faster-whisper"
        hotwords = ["讲个笑话", "你好", "再见"]
    """).strip(), encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.asr.hotwords == ["讲个笑话", "你好", "再见"]


def test_asr_config_model_dir_default() -> None:
    """P3-S1: ASRConfig.model_dir defaults to the bundled whisper subfolder."""
    cfg = ASRConfig()
    assert cfg.model_dir == "faster-whisper-large-v3-turbo"


def test_load_config_asr_reads_model_dir(tmp_path: Path) -> None:
    """P3-S1: custom [asr].model_dir overrides default."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [asr]
        model_dir = "my-custom-whisper"
    """).strip(), encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.asr.model_dir == "my-custom-whisper"


def test_load_config_asr_ignores_unknown_keys(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [asr]
        model = "large-v3-turbo"
        hotwords = ["你好"]
        experimental_rescore = true
    """).strip(), encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.asr.hotwords == ["你好"]
    assert cfg.asr.model == "large-v3-turbo"
