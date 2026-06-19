# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import config
from config import (
    AppConfig,
    LLMEndpointConfig,
    LLMRoutingConfig,
    effective_llm_model,
    effective_llm_model_standalone,
)


def test_effective_llm_model_prefers_dataclass_local_model() -> None:
    cfg = AppConfig(
        llm=LLMRoutingConfig(local=LLMEndpointConfig(model="gpt-5.5")),
        raw={"llm": {"model": "gemma4:e4b"}},
    )

    assert effective_llm_model(cfg) == "gpt-5.5"


def test_effective_llm_model_falls_back_to_raw_model() -> None:
    cfg = AppConfig(
        llm=LLMRoutingConfig(local=LLMEndpointConfig(model="")),
        raw={"llm": {"model": "qwen3.6-plus"}},
    )

    assert effective_llm_model(cfg) == "qwen3.6-plus"


def test_effective_llm_model_falls_back_to_seed_default() -> None:
    cfg = AppConfig(
        llm=LLMRoutingConfig(local=LLMEndpointConfig(model="")),
        raw={"llm": {"model": ""}},
    )

    assert effective_llm_model(cfg) == "gemma4:e4b"


def test_effective_llm_model_is_safe_for_partial_config() -> None:
    assert effective_llm_model(SimpleNamespace(llm=None, raw={})) == "gemma4:e4b"
    assert effective_llm_model(SimpleNamespace(raw=None)) == "gemma4:e4b"


def test_effective_llm_model_standalone_prefers_runtime_json(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config._paths, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda path: SimpleNamespace(raw={"llm": {"model": "qwen3.6-plus"}}),
    )
    (tmp_path / "llm_runtime.json").write_text(
        '{"model":"gpt-5.5"}',
        encoding="utf-8",
    )

    assert effective_llm_model_standalone() == "gpt-5.5"


def test_effective_llm_model_standalone_falls_back_without_runtime_model(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config._paths, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda path: SimpleNamespace(raw={"llm": {"model": "qwen3.6-plus"}}),
    )
    (tmp_path / "llm_runtime.json").write_text(
        '{"base_url":"http://localhost:11434/v1"}',
        encoding="utf-8",
    )

    assert effective_llm_model_standalone() == "qwen3.6-plus"


def test_effective_llm_model_standalone_falls_back_when_runtime_missing(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config._paths, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda path: SimpleNamespace(raw={"llm": {"model": "qwen3.6-plus"}}),
    )

    assert effective_llm_model_standalone() == "qwen3.6-plus"


def test_effective_llm_model_standalone_falls_back_on_bad_runtime_json(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config._paths, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda path: SimpleNamespace(raw={"llm": {"model": "qwen3.6-plus"}}),
    )
    (tmp_path / "llm_runtime.json").write_text("{bad", encoding="utf-8")

    assert effective_llm_model_standalone() == "qwen3.6-plus"
