# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase 1.1 — per-model 上下文窗口 + 三层 override 解析单测。

覆盖 spec `per-model-context` 的 5 个场景：
  1. 无 override → 走 BUILTIN 内置表
  2. 缺失 model → 走 `_default` 保守默认
  3. 全局 override 深合并（只覆盖出现的字段）打败 builtin
  4. 项目 override（code mode）打败 global
  5. 非 code mode（project_root=None）忽略项目层

`resolve()` 是纯函数：全局层路径由 `paths.user_data_dir()` 决定，测试
通过 `DESKPET_USER_DATA_DIR` 环境变量把它指向 `tmp_path`，项目层路径
直接传 fixture 目录，因此整个解析过程无外部副作用、可重复。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm.model_info import (
    BUILTIN,
    ModelContextInfo,
    load_global_overrides,
    load_project_overrides,
    resolve,
)


@pytest.fixture()
def isolated_user_data(monkeypatch, tmp_path):
    """把 paths.user_data_dir() 钉到一个干净的 tmp 目录，无全局 override 文件。"""
    user_data = tmp_path / "user_data"
    user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data))
    return user_data


# ───────────────────────── BUILTIN 内置表 ─────────────────────────


def test_builtin_table_has_required_models():
    """design.md D1 列出的内置 model 都在表里，且参数与 spec 数值一致。"""
    assert BUILTIN["deepseek-v4-pro"].context_window == 1_000_000
    assert BUILTIN["deepseek-v4-pro"].compact_at_pct == 0.75
    assert BUILTIN["deepseek-v4-pro"].effective_pct == 0.95
    assert BUILTIN["deepseek-v4-pro"].recall_sweet_tokens == 384_000

    assert BUILTIN["claude-sonnet-4-5"].context_window == 200_000
    assert BUILTIN["claude-sonnet-4-5"].compact_at_pct == 0.83
    assert BUILTIN["claude-opus-4-5"].context_window == 200_000
    assert BUILTIN["claude-opus-4-5"].compact_at_pct == 0.83

    assert BUILTIN["gpt-5-pro"].context_window == 1_000_000
    assert BUILTIN["gpt-5-pro"].compact_at_pct == 0.80
    assert BUILTIN["gemini-2.5-pro"].context_window == 1_000_000
    assert BUILTIN["gemini-2.5-pro"].compact_at_pct == 0.80

    assert BUILTIN["_default"].context_window == 32_000
    assert BUILTIN["_default"].compact_at_pct == 0.80
    assert BUILTIN["_default"].effective_pct == 0.90


def test_resolve_builtin_default_used_when_no_override(isolated_user_data):
    """Scenario: Built-in default used when no override exists."""
    info = resolve("deepseek-v4-pro", project_root=None)
    assert info.context_window == 1_000_000
    assert info.compact_at_pct == 0.75
    assert info.source == "builtin"
    assert info.model == "deepseek-v4-pro"


def test_resolve_unknown_model_falls_back_to_default(isolated_user_data):
    """Scenario: Unknown model falls back to conservative default."""
    info = resolve("some-local-7b", project_root=None)
    assert info.context_window == 32_000
    assert info.compact_at_pct == 0.80
    # 仍带原 model 名（便于日志/UI 显示用户实际用的是什么）
    assert info.model == "some-local-7b"
    assert info.source == "builtin"


# ───────────────────── 全局层深合并 ─────────────────────


def _write_toml(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_global_override_beats_builtin_via_deep_merge(isolated_user_data):
    """Scenario: Global override beats built-in via deep merge.

    只覆盖 context_window；compact_at_pct 等未出现字段保留 builtin 值。
    """
    _write_toml(
        isolated_user_data / "model_overrides.toml",
        '[models."deepseek-v4-pro"]\ncontext_window = 800000\n',
    )
    info = resolve("deepseek-v4-pro", project_root=None)
    assert info.context_window == 800_000
    # 深合并：未覆盖字段保留 builtin
    assert info.compact_at_pct == 0.75
    assert info.effective_pct == 0.95
    assert info.source == "global"


def test_global_override_loader_returns_dict(isolated_user_data):
    """load_global_overrides() 纯读 TOML → dict；缺文件返回 {}。"""
    assert load_global_overrides() == {}
    _write_toml(
        isolated_user_data / "model_overrides.toml",
        '[models."gpt-5-pro"]\ncompact_at_pct = 0.7\n',
    )
    data = load_global_overrides()
    assert data["models"]["gpt-5-pro"]["compact_at_pct"] == 0.7


# ───────────────────── 项目层（code mode）─────────────────────


def test_project_override_beats_global_in_code_mode(isolated_user_data, tmp_path):
    """Scenario: Project override beats global in code mode."""
    _write_toml(
        isolated_user_data / "model_overrides.toml",
        '[models."deepseek-v4-pro"]\ncontext_window = 800000\n',
    )
    project_root = tmp_path / "myproj"
    _write_toml(
        project_root / ".deskpet" / "context.toml",
        '[models."deepseek-v4-pro"]\ncontext_window = 1000000\n',
    )
    info = resolve("deepseek-v4-pro", project_root=project_root)
    assert info.context_window == 1_000_000
    assert info.source == "project"
    # 项目只覆盖 window，compact_at_pct 仍走 builtin（深合并跨三层）
    assert info.compact_at_pct == 0.75


def test_project_loader_returns_empty_when_no_root(isolated_user_data, tmp_path):
    assert load_project_overrides(None) == {}
    empty_root = tmp_path / "no_deskpet_dir"
    empty_root.mkdir()
    assert load_project_overrides(empty_root) == {}


def test_non_code_mode_ignores_project_layer(isolated_user_data, tmp_path):
    """Scenario: Non-code-mode ignores the project layer.

    project_root=None → 即使磁盘上有 .deskpet/context.toml 也不读它。
    """
    _write_toml(
        isolated_user_data / "model_overrides.toml",
        '[models."deepseek-v4-pro"]\ncontext_window = 800000\n',
    )
    # 项目文件存在，但 resolve 不传 project_root → 只走前两层
    project_root = tmp_path / "myproj"
    _write_toml(
        project_root / ".deskpet" / "context.toml",
        '[models."deepseek-v4-pro"]\ncontext_window = 1234567\n',
    )
    info = resolve("deepseek-v4-pro", project_root=None)
    assert info.context_window == 800_000  # global, not project
    assert info.source == "global"


def test_three_layer_deep_merge_field_precedence(isolated_user_data, tmp_path):
    """三层叠加 + 每个字段独立覆盖：project > global > builtin。"""
    _write_toml(
        isolated_user_data / "model_overrides.toml",
        '[models."deepseek-v4-pro"]\n'
        "context_window = 900000\n"
        "compact_at_pct = 0.7\n",
    )
    project_root = tmp_path / "p"
    _write_toml(
        project_root / ".deskpet" / "context.toml",
        '[models."deepseek-v4-pro"]\ncompact_at_pct = 0.6\n',
    )
    info = resolve("deepseek-v4-pro", project_root=project_root)
    # context_window: project 没动 → 取 global 900000
    assert info.context_window == 900_000
    # compact_at_pct: project 覆盖了 → 0.6（不是 global 的 0.7，不是 builtin 0.75）
    assert info.compact_at_pct == 0.6
    # effective_pct: 两层都没动 → builtin 0.95
    assert info.effective_pct == 0.95
    assert info.source == "project"


def test_resolve_emits_resolution_log(isolated_user_data, caplog):
    """1.1.5: resolve 落 `model_context_resolved model=.. window=.. source=..`。"""
    import logging

    with caplog.at_level(logging.INFO):
        resolve("deepseek-v4-pro", project_root=None)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "model_context_resolved" in joined
    assert "model=deepseek-v4-pro" in joined
    assert "window=1000000" in joined
    assert "source=builtin" in joined


def test_model_context_info_is_frozen_value_object():
    """ModelContextInfo 是不可变值对象（避免解析后被意外篡改）。"""
    info = ModelContextInfo(
        model="x",
        context_window=100,
        effective_pct=0.9,
        compact_at_pct=0.8,
        recall_sweet_tokens=50,
        source="builtin",
    )
    with pytest.raises((AttributeError, Exception)):
        info.context_window = 999  # type: ignore[misc]
