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


def test_load_config_warns_on_pre_split_llm_schema(tmp_path, caplog):
    """User on the pre-P2-1-S2 flat [llm] schema should get a loud warning,
    not a silent revert to default model.

    Failure mode this guards against: user upgrading from v0.2.0 with a
    custom `[llm] model = "qwen2.5:7b"` would silently start using the
    `gemma4:e4b` default because the old keys get dropped and no
    [llm.local] section means the dataclass defaults take over.
    """
    import logging

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [llm]
        strategy = "local_first"
        model = "qwen2.5:7b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
    """).strip())

    with caplog.at_level(logging.WARNING):
        cfg = load_config(cfg_path)

    # The custom model is silently lost (we can't recover it without invasive
    # auto-migration), but the user is warned in logs.
    assert cfg.llm.local.model == "gemma4:e4b"  # default kicked in
    assert any("pre-P2-1-S2 schema" in r.message for r in caplog.records)
    assert any("model" in r.message for r in caplog.records)


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
