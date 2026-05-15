"""Phase 1.1.6 — 模型上下文配置卡片的后端 IPC handler 单测。

两个新消息类型（与 SettingsPanel「模型上下文」卡片对接）：
  - ``model_context_get``  → ``model_context_get_response``
      返回当前 model 三层 resolve 后的 window/compact/source + builtin 全表
  - ``model_context_set``  → ``model_context_set_ack``
      就地把覆盖写回 global（%APPDATA%/deskpet/model_overrides.toml）或
      project（<root>/.deskpet/context.toml）TOML，深合并不覆盖其他模型

测试直接打 ``p4_ipc.handle``，用 FakeWebSocket + FakeServiceContext，
``DESKPET_USER_DATA_DIR`` 钉到 tmp 隔离全局层文件。
"""
from __future__ import annotations

from typing import Any

import pytest

import p4_ipc


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)


class FakeServiceContext:
    def __init__(self, **services: Any) -> None:
        self._services = dict(services)

    def get(self, name: str) -> Any:
        return self._services.get(name)


@pytest.fixture()
def isolated_user_data(monkeypatch, tmp_path):
    user_data = tmp_path / "user_data"
    user_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data))
    return user_data


# ───────────────────── model_context_get ─────────────────────


@pytest.mark.asyncio
async def test_model_context_get_returns_builtin_resolution(isolated_user_data):
    ws = FakeWebSocket()
    sc = FakeServiceContext()
    await p4_ipc.handle(
        ws, "s1", "model_context_get", {"model": "deepseek-v4-pro"}, sc,
    )
    assert len(ws.sent) == 1
    m = ws.sent[0]
    assert m["type"] == "model_context_get_response"
    p = m["payload"]
    assert p["model"] == "deepseek-v4-pro"
    assert p["resolved"]["context_window"] == 1_000_000
    assert p["resolved"]["compact_at_pct"] == 0.75
    assert p["resolved"]["source"] == "builtin"
    # builtin 全表也回传，前端下拉选模型用
    assert "deepseek-v4-pro" in p["builtin"]
    assert p["builtin"]["claude-sonnet-4-5"]["context_window"] == 200_000


@pytest.mark.asyncio
async def test_model_context_get_reflects_global_override(isolated_user_data):
    (isolated_user_data / "model_overrides.toml").write_text(
        '[models."deepseek-v4-pro"]\ncontext_window = 600000\n',
        encoding="utf-8",
    )
    ws = FakeWebSocket()
    await p4_ipc.handle(
        ws, "s1", "model_context_get", {"model": "deepseek-v4-pro"},
        FakeServiceContext(),
    )
    p = ws.sent[0]["payload"]
    assert p["resolved"]["context_window"] == 600_000
    assert p["resolved"]["source"] == "global"


@pytest.mark.asyncio
async def test_model_context_get_defaults_model_when_missing(isolated_user_data):
    ws = FakeWebSocket()
    await p4_ipc.handle(
        ws, "s1", "model_context_get", {}, FakeServiceContext(),
    )
    # 缺 model → 用 _default 兜底，不崩
    p = ws.sent[0]["payload"]
    assert ws.sent[0]["type"] == "model_context_get_response"
    assert p["resolved"]["context_window"] == 32_000


# ───────────────────── model_context_set ─────────────────────


@pytest.mark.asyncio
async def test_model_context_set_writes_global_toml(isolated_user_data):
    ws = FakeWebSocket()
    await p4_ipc.handle(
        ws,
        "s1",
        "model_context_set",
        {
            "scope": "global",
            "model": "deepseek-v4-pro",
            "fields": {"context_window": 750000},
        },
        FakeServiceContext(),
    )
    assert ws.sent[0]["type"] == "model_context_set_ack"
    assert ws.sent[0]["payload"]["ok"] is True
    # 文件真的写了，且 resolve 后能读回
    import tomllib

    data = tomllib.loads(
        (isolated_user_data / "model_overrides.toml").read_text("utf-8")
    )
    assert data["models"]["deepseek-v4-pro"]["context_window"] == 750000

    from llm.model_info import resolve

    info = resolve("deepseek-v4-pro", project_root=None)
    assert info.context_window == 750000
    assert info.source == "global"


@pytest.mark.asyncio
async def test_model_context_set_global_deep_merges_other_models(isolated_user_data):
    """写一个 model 的覆盖不能抹掉同文件里其他 model 的覆盖。"""
    (isolated_user_data / "model_overrides.toml").write_text(
        '[models."gpt-5-pro"]\ncompact_at_pct = 0.6\n',
        encoding="utf-8",
    )
    ws = FakeWebSocket()
    await p4_ipc.handle(
        ws,
        "s1",
        "model_context_set",
        {
            "scope": "global",
            "model": "deepseek-v4-pro",
            "fields": {"context_window": 700000},
        },
        FakeServiceContext(),
    )
    import tomllib

    data = tomllib.loads(
        (isolated_user_data / "model_overrides.toml").read_text("utf-8")
    )
    # 旧 model 覆盖保留
    assert data["models"]["gpt-5-pro"]["compact_at_pct"] == 0.6
    # 新 model 覆盖写入
    assert data["models"]["deepseek-v4-pro"]["context_window"] == 700000


@pytest.mark.asyncio
async def test_model_context_set_writes_project_toml(isolated_user_data, tmp_path):
    project_root = tmp_path / "myproj"
    project_root.mkdir(parents=True, exist_ok=True)
    ws = FakeWebSocket()
    await p4_ipc.handle(
        ws,
        "s1",
        "model_context_set",
        {
            "scope": "project",
            "project_root": str(project_root),
            "model": "deepseek-v4-pro",
            # 用与 builtin(1_000_000) 不同的值，使 resolve 的 source 链
            # 正确落到 project（resolve.source = 最后一个真正改了字段的层）。
            "fields": {"context_window": 900000},
        },
        FakeServiceContext(),
    )
    assert ws.sent[0]["payload"]["ok"] is True
    import tomllib

    data = tomllib.loads(
        (project_root / ".deskpet" / "context.toml").read_text("utf-8")
    )
    assert data["models"]["deepseek-v4-pro"]["context_window"] == 900000

    from llm.model_info import resolve

    info = resolve("deepseek-v4-pro", project_root=project_root)
    assert info.context_window == 900000
    assert info.source == "project"


@pytest.mark.asyncio
async def test_model_context_set_project_scope_requires_root(isolated_user_data):
    ws = FakeWebSocket()
    await p4_ipc.handle(
        ws,
        "s1",
        "model_context_set",
        {"scope": "project", "model": "x", "fields": {"context_window": 1}},
        FakeServiceContext(),
    )
    # 缺 project_root → ok=False，不写盘不崩
    m = ws.sent[0]
    assert m["type"] == "model_context_set_ack"
    assert m["payload"]["ok"] is False
    assert "project_root" in (m["payload"].get("reason") or "")


@pytest.mark.asyncio
async def test_model_context_set_rejects_unknown_field(isolated_user_data):
    ws = FakeWebSocket()
    await p4_ipc.handle(
        ws,
        "s1",
        "model_context_set",
        {
            "scope": "global",
            "model": "deepseek-v4-pro",
            "fields": {"evil_field": 1, "context_window": 900000},
        },
        FakeServiceContext(),
    )
    import tomllib

    data = tomllib.loads(
        (isolated_user_data / "model_overrides.toml").read_text("utf-8")
    )
    # 白名单外字段被丢弃，合法字段仍写入
    assert "evil_field" not in data["models"]["deepseek-v4-pro"]
    assert data["models"]["deepseek-v4-pro"]["context_window"] == 900000


@pytest.mark.asyncio
async def test_model_context_types_registered():
    """两个新类型进了 P4_IPC_MESSAGE_TYPES，main.py dispatch 才会路由进来。"""
    assert "model_context_get" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert "model_context_set" in p4_ipc.P4_IPC_MESSAGE_TYPES
