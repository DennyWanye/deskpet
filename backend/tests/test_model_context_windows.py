# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""2026-06-12 上下文档位可选 — gpt-5.5 32K 兜底修复 + 档位选择持久化。

真机事故链: gpt-5.5 不在 BUILTIN → _default 32K → 26k prompt 即触发
压缩(连环 400)。本组守护: ①gpt-5.5 有真实表项 ②档位列表 ③用户选择
写全局 override 后 resolve 即生效 ④非法档位拒绝。
"""
from __future__ import annotations

import pytest

from llm.model_info import (
    BUILTIN,
    resolve,
    save_global_window_override,
    supported_windows_for,
)


@pytest.fixture()
def isolated_user_data(monkeypatch, tmp_path):
    user_data = tmp_path / "user_data"
    user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data))
    return user_data


def test_gpt55_not_32k_fallback(isolated_user_data):
    """gpt-5.5 必须有真实 BUILTIN 表项 — 不再落 32K _default 兜底。"""
    assert "gpt-5.5" in BUILTIN
    info = resolve("gpt-5.5")
    assert info.context_window == 400_000
    assert info.source == "builtin"


def test_gpt55_supported_windows_include_1m(isolated_user_data):
    wins = supported_windows_for("gpt-5.5")
    assert 1_000_000 in wins
    assert 400_000 in wins
    assert wins == sorted(wins)


def test_save_override_takes_effect(isolated_user_data):
    """用户选 1M → 写全局 override → resolve 立即生效(source=global)。"""
    assert save_global_window_override("gpt-5.5", 1_000_000) is True
    info = resolve("gpt-5.5")
    assert info.context_window == 1_000_000
    assert info.source == "global"
    # 文件确实落盘且可被 loader 读回
    assert (isolated_user_data / "model_overrides.toml").is_file()


def test_save_override_rejects_unsupported(isolated_user_data):
    """非档位值(如随手输入 999)拒绝写入。"""
    assert save_global_window_override("gpt-5.5", 999) is False
    info = resolve("gpt-5.5")
    assert info.context_window == 400_000  # 未被污染


def test_save_override_preserves_other_models(isolated_user_data):
    """写 gpt-5.5 不抹掉文件里其它模型的 override。"""
    assert save_global_window_override("gpt-5.5", 1_000_000) is True
    assert save_global_window_override("deepseek-v4-pro", 400_000) is True
    assert resolve("gpt-5.5").context_window == 1_000_000
    assert resolve("deepseek-v4-pro").context_window == 400_000


def test_unknown_model_single_window(isolated_user_data):
    """未知型号 → 单档(其解析值),UI 不渲染下拉。"""
    wins = supported_windows_for("some-local-7b")
    assert wins == [32_000]


def test_catalog_carries_supported_windows(isolated_user_data):
    from llm.model_catalog import build_catalog

    cat = build_catalog(["gpt-5.5", "totally-unknown-model"])
    by_id = {m["id"]: m for m in cat}
    assert 1_000_000 in by_id["gpt-5.5"]["supported_windows"]
    assert by_id["totally-unknown-model"]["supported_windows"] == []


def test_catalog_window_follows_override(isolated_user_data):
    """选 1M 后 catalog 显示值跟着变(前后端一致)。"""
    from llm.model_catalog import model_context_window

    assert model_context_window("gpt-5.5") == 400_000
    save_global_window_override("gpt-5.5", 1_000_000)
    assert model_context_window("gpt-5.5") == 1_000_000
