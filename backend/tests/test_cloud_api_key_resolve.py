"""P2-1-S3: cloud API key lives in Windows Credential Manager (via Tauri),
reaches the backend through the ``DESKPET_CLOUD_API_KEY`` env var, and is
**never** read from config.toml.

These tests pin:
  1. ``_resolve_cloud_api_key`` reads from env, returns None when absent.
  2. ``load_config`` ignores a plaintext ``[llm.cloud].api_key`` in TOML,
     emits a WARN log pointing the user to SettingsPanel.
"""
from __future__ import annotations

import logging
import textwrap


# ---- resolve_cloud_api_key ---------------------------------------------------
# Imported directly from ``config`` so we don't drag in main.py's heavy
# provider initialisation (faster_whisper, CUDA bindings, ...).


def test_resolve_returns_env_value_when_set(monkeypatch):
    from config import resolve_cloud_api_key
    monkeypatch.setenv("DESKPET_CLOUD_API_KEY", "sk-from-keyring-via-env")
    assert resolve_cloud_api_key() == "sk-from-keyring-via-env"


def test_resolve_returns_none_when_env_unset(monkeypatch):
    from config import resolve_cloud_api_key
    monkeypatch.delenv("DESKPET_CLOUD_API_KEY", raising=False)
    assert resolve_cloud_api_key() is None


def test_resolve_treats_empty_string_as_none(monkeypatch):
    # A leftover `export DESKPET_CLOUD_API_KEY=""` shouldn't be treated as
    # "configured but blank" — downstream provider would reject it with a
    # less helpful error.
    from config import resolve_cloud_api_key
    monkeypatch.setenv("DESKPET_CLOUD_API_KEY", "")
    assert resolve_cloud_api_key() is None


# ---- load_config plaintext warning -------------------------------------------


def test_load_config_warns_on_plaintext_cloud_api_key(tmp_path, caplog):
    """User carried over an old config.toml that still lists a real key under
    ``[llm.cloud].api_key``. We still load the *other* cloud fields (model,
    base_url) so their customisation isn't lost, but emit a WARN that names
    the Credential Manager / SettingsPanel path forward."""
    from config import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent("""
        [llm]
        strategy = "local_first"

        [llm.local]
        model = "gemma4:e4b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"

        [llm.cloud]
        model = "qwen3.6-plus"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key = "sk-real-leftover-from-P2-1-S1"
    """).strip(), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cfg = load_config(cfg_path)

    # Other cloud fields still load — we only suppress the key itself.
    assert cfg.llm.cloud is not None
    assert cfg.llm.cloud.model == "qwen3.6-plus"
    assert cfg.llm.cloud.base_url.startswith("https://dashscope")

    relevant = [r for r in caplog.records if "cloud" in r.message.lower()]
    assert relevant, f"no cloud-related warning logged; records={caplog.records!r}"
    joined = " ".join(r.message.lower() for r in relevant)
    assert "credential" in joined or "keyring" in joined or "plaintext" in joined, (
        f"warning text didn't mention credential/keyring/plaintext: {joined}"
    )


def test_load_config_silent_when_cloud_api_key_is_placeholder(tmp_path, caplog):
    """Default config.toml ships a ``sk-...`` placeholder. That shouldn't
    trigger the warning — the user hasn't actually leaked anything."""
    from config import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent("""
        [llm.cloud]
        model = "qwen3.6-plus"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key = "sk-..."
    """).strip(), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        load_config(cfg_path)

    for r in caplog.records:
        assert "plaintext" not in r.message.lower(), (
            f"placeholder key triggered spurious warning: {r.message}"
        )
