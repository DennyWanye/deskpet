"""Tests for [voice] section loading (P2-2-M3 Task 13).

Backend-only config for TTS-phase barge-in behavior. The [vad] section
still owns the "normal" threshold/min_speech values — [voice] only holds
TTS-phase overrides so pipeline can dynamically swap them.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from config import load_config, VoiceConfig, AppConfig


def test_voice_config_defaults_match_plan() -> None:
    """Dataclass defaults must match P2-2 architecture doc v5.0 §6.3."""
    cfg = VoiceConfig()
    assert cfg.vad_threshold_during_tts == 0.65
    assert cfg.min_speech_ms_during_tts == 400
    assert cfg.tts_cooldown_ms == 300


def test_app_config_has_voice_field() -> None:
    """AppConfig must expose .voice so main.py can wire it into VoicePipeline."""
    cfg = AppConfig()
    assert isinstance(cfg.voice, VoiceConfig)


def test_load_config_without_voice_section_uses_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [vad]
            threshold = 0.5
            min_speech_ms = 250
            min_silence_ms = 500
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    # No [voice] section → defaults kick in.
    assert cfg.voice.vad_threshold_during_tts == 0.65
    assert cfg.voice.min_speech_ms_during_tts == 400
    assert cfg.voice.tts_cooldown_ms == 300


def test_load_config_voice_section_overrides_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [voice]
            vad_threshold_during_tts = 0.75
            min_speech_ms_during_tts = 500
            tts_cooldown_ms = 250
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    assert cfg.voice.vad_threshold_during_tts == 0.75
    assert cfg.voice.min_speech_ms_during_tts == 500
    assert cfg.voice.tts_cooldown_ms == 250


def test_load_config_voice_ignores_unknown_keys(tmp_path: Path) -> None:
    """Future-field tolerance — user still on old config.toml keeps booting."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            [voice]
            vad_threshold_during_tts = 0.7
            future_experimental_knob = "value"
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    assert cfg.voice.vad_threshold_during_tts == 0.7
    # Defaults still apply for the unspecified fields.
    assert cfg.voice.min_speech_ms_during_tts == 400
