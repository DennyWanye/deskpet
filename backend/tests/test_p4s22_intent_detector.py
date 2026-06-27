# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 — keyword-based "wants to start a project" detector."""
from __future__ import annotations

import pytest

from deskpet.code_mode import maybe_suggest_code_mode


@pytest.mark.parametrize(
    "text",
    [
        "帮我做一个项目",
        "搞一个项目吧",
        "我想新项目",
        "建个项目，python 写",
        "做一个 CLI 小工具",
        "做一个 app",
        "做一个应用呗",
        "Scaffold a Rust project",
        "Build me a TODO tracker",
        "create a project for invoicing",
        "make a CLI for this",
        "set up a project",
    ],
)
def test_triggers(text):
    assert maybe_suggest_code_mode(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "你好",
        "今天天气怎么样",
        "我喜欢可乐",
        "What's 2+2?",
        "",
    ],
)
def test_non_triggers(text):
    assert maybe_suggest_code_mode(text) is False


def test_punctuation_doesnt_block_match():
    assert maybe_suggest_code_mode("Scaffold!") is True
    assert maybe_suggest_code_mode("Scaffold?") is True


def test_case_insensitive():
    assert maybe_suggest_code_mode("SCAFFOLD a Python project") is True
    assert maybe_suggest_code_mode("Build Me A Browser") is True


def test_handles_none_safely():
    # Defensive — caller might pass None by accident.
    assert maybe_suggest_code_mode(None) is False  # type: ignore[arg-type]
