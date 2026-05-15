"""Phase 1.1.4 — ContextManager.for_session 工厂：按 model + project_root
三层 resolve 注入 ModelContextInfo（design.md D1/D2 集成点）。

- code mode：传 project_root → 项目层覆盖生效
- 非 code mode：project_root=None → 只走两层
- v2_enabled=False：忽略 per-model，走 legacy 绝对值（Strangler-Fig）
"""
from __future__ import annotations

import pytest

from agent.context_manager import ContextManager


@pytest.fixture()
def isolated_user_data(monkeypatch, tmp_path):
    user_data = tmp_path / "user_data"
    user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data))
    return user_data


def test_for_session_resolves_builtin_window(isolated_user_data):
    """非 code mode（project_root=None）→ builtin deepseek 1M。"""
    ctx = ContextManager.for_session(model="deepseek-v4-pro", project_root=None)
    assert ctx.config.model_info is not None
    assert ctx.config.model_info.context_window == 1_000_000
    assert ctx.config.compact_at_tokens == 750_000
    assert ctx.config.model_info.source == "builtin"


def test_for_session_unknown_model_falls_back_default(isolated_user_data):
    ctx = ContextManager.for_session(model="some-local-7b", project_root=None)
    assert ctx.config.model_info.context_window == 32_000
    assert ctx.config.model_info.model == "some-local-7b"


def test_for_session_project_override_in_code_mode(isolated_user_data, tmp_path):
    """code mode：项目 .deskpet/context.toml 覆盖 window。"""
    project_root = tmp_path / "proj"
    cfg_dir = project_root / ".deskpet"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "context.toml").write_text(
        '[models."deepseek-v4-pro"]\ncontext_window = 500000\n',
        encoding="utf-8",
    )
    ctx = ContextManager.for_session(
        model="deepseek-v4-pro", project_root=project_root,
    )
    assert ctx.config.model_info.context_window == 500_000
    assert ctx.config.model_info.source == "project"
    # compact_at 随项目 window 重算：500_000 * 0.75
    assert ctx.config.compact_at_tokens == 375_000


def test_for_session_v2_disabled_uses_legacy(isolated_user_data):
    """v2_enabled=False → 忽略 per-model，走 legacy 绝对值。"""
    ctx = ContextManager.for_session(
        model="deepseek-v4-pro", project_root=None, v2_enabled=False,
    )
    assert ctx.config.v2_enabled is False
    assert ctx.config.tool_result_threshold == 16_000  # legacy 常量
    assert ctx.config.compact_message_threshold == 80


def test_for_session_preserves_custom_ref_store(isolated_user_data):
    """传入自定义 ref_store（测试隔离用）应被保留。"""
    from agent.tool_result_truncator import ToolResultRefStore

    store = ToolResultRefStore()
    ctx = ContextManager.for_session(
        model="deepseek-v4-pro", project_root=None, ref_store=store,
    )
    assert ctx.ref_store is store


def test_for_session_emits_resolution_log(isolated_user_data, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        ContextManager.for_session(model="claude-sonnet-4-5", project_root=None)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "model_context_resolved" in joined
    assert "model=claude-sonnet-4-5" in joined
    assert "window=200000" in joined
