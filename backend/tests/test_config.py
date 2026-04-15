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

from config import load_config


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
