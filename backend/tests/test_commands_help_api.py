# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2-B5 v2 — /api/commands/help + /api/commands/<name>/schema 测试.

测试范围:
  - feature flag OFF → feature_enabled=False
  - flag ON → builtins (help, goal) 必出现
  - skill list 解析 args / arguments frontmatter (str / dict 两种格式)
  - /api/commands/<name>/schema 404 未知命令
  - /api/commands/<name>/schema 大小写不敏感
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_flag_on(monkeypatch):
    """启 slash_commands flag 后构造 FastAPI client + mock skill_loader."""
    # 必须在 import main 前 monkeypatch 环境
    import main as main_module
    monkeypatch.setattr(
        main_module.config.features, "slash_commands", True, raising=True,
    )
    # mock skill_loader 注入 service_context
    mock_loader = MagicMock()
    mock_loader.list_skills.return_value = [
        {
            "name": "ppt-generate",
            "description": "Generate PowerPoint deck.",
            "args": ["topic", "outline"],
        },
        {
            "name": "deep-research",
            "description": "Multi-step research with citations.",
            "arguments": [
                {"name": "query", "type": "string", "description": "search query", "required": True},
                {"name": "depth", "type": "int", "description": "iter depth", "required": False},
            ],
        },
        {
            "name": "no-args-skill",
            "description": "Toy skill.",
        },
    ]
    monkeypatch.setattr(
        main_module.service_context, "get",
        lambda key: mock_loader if key == "skill_loader" else None,
    )
    return TestClient(main_module.app)


@pytest.fixture
def client_flag_off(monkeypatch):
    import main as main_module
    monkeypatch.setattr(
        main_module.config.features, "slash_commands", False, raising=True,
    )
    return TestClient(main_module.app)


# ─── /api/commands/help ────────────────────────────


def test_help_flag_off_returns_empty(client_flag_off):
    r = client_flag_off.get("/api/commands/help")
    assert r.status_code == 200
    body = r.json()
    assert body["feature_enabled"] is False
    assert body["commands"] == []


def test_help_flag_on_includes_builtins(client_flag_on):
    r = client_flag_on.get("/api/commands/help")
    assert r.status_code == 200
    body = r.json()
    assert body["feature_enabled"] is True
    names = [c["name"] for c in body["commands"]]
    assert "help" in names
    assert "goal" in names


def test_help_includes_skills(client_flag_on):
    r = client_flag_on.get("/api/commands/help")
    body = r.json()
    names = [c["name"] for c in body["commands"]]
    assert "ppt-generate" in names
    assert "deep-research" in names


def test_help_parses_str_args_schema(client_flag_on):
    r = client_flag_on.get("/api/commands/help")
    body = r.json()
    ppt = next(c for c in body["commands"] if c["name"] == "ppt-generate")
    args = ppt["args_schema"]
    assert len(args) == 2
    assert args[0]["name"] == "topic"
    assert args[0]["type"] == "string"
    assert args[0]["required"] is False


def test_help_parses_dict_args_schema(client_flag_on):
    r = client_flag_on.get("/api/commands/help")
    body = r.json()
    dr = next(c for c in body["commands"] if c["name"] == "deep-research")
    args = dr["args_schema"]
    assert args[0]["name"] == "query"
    assert args[0]["required"] is True
    assert args[1]["name"] == "depth"
    assert args[1]["type"] == "int"


def test_help_skill_no_args_returns_empty_list(client_flag_on):
    r = client_flag_on.get("/api/commands/help")
    body = r.json()
    s = next(c for c in body["commands"] if c["name"] == "no-args-skill")
    assert s["args_schema"] == []


# ─── /api/commands/<name>/schema ───────────────────


def test_schema_flag_off_403(client_flag_off):
    r = client_flag_off.get("/api/commands/help/schema")
    assert r.status_code == 403


def test_schema_builtin_help(client_flag_on):
    r = client_flag_on.get("/api/commands/help/schema")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "help"
    assert body["args_schema"] == []


def test_schema_builtin_goal(client_flag_on):
    r = client_flag_on.get("/api/commands/goal/schema")
    body = r.json()
    assert body["name"] == "goal"
    assert len(body["args_schema"]) == 1
    assert body["args_schema"][0]["name"] == "text"


def test_schema_skill_lookup(client_flag_on):
    r = client_flag_on.get("/api/commands/ppt-generate/schema")
    body = r.json()
    assert body["name"] == "ppt-generate"
    assert body["args_schema"][0]["name"] == "topic"


def test_schema_unknown_404(client_flag_on):
    r = client_flag_on.get("/api/commands/notexist/schema")
    assert r.status_code == 404


def test_schema_case_insensitive(client_flag_on):
    r = client_flag_on.get("/api/commands/HELP/schema")
    assert r.status_code == 200
    assert r.json()["name"] == "help"
