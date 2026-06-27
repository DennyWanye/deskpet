# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase 3 — companion session write-scope 单测 (OpenSpec §D3)。

回归背景：`default` 陪伴 session 直接 `mkdir /path/to/deskpet\\backend\\
vpn-cli` 往代码仓库写 17 个文件，没有任何 scope 约束。

**这不是沙箱**：不拦读、不拦命令、不弹窗。它只是"陪伴模式不该随便往
代码仓库写文件"的 session 类型语义——companion/`default` session 的写盘
类工具 path 限定在 resolve(workspace_root) 内；code session 不受影响
（它本来就绑 project_root）。属 feedback_no_sandbox 里允许的 irreversible-guard 例外。

实现点：`ToolRegistry.execute_tool` 把 `_session_context` 合并进 params，
chat handler 给 companion session 注入 `_write_scope_root`，写盘工具读到
该键时做 path 前缀校验。

`write_scope_enforced=false` → 不注入 → 退回旧自由写盘（Strangler-Fig）。

acceptance scenarios 来自 specs/capability-gate/spec.md 第 2 个 Requirement。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.write_scope import (
    is_companion_session,
    resolve_workspace_root,
    scope_violation_message,
    write_scope_check,
)


# ---------------------------------------------------------------------------
# session-kind 判定（复用 code_mode/state.py 语义）
# ---------------------------------------------------------------------------
class _StubCodeMode:
    """最小 CodeModeManager 替身：只暴露 is_enabled。"""

    def __init__(self, enabled_sids: set[str]) -> None:
        self._enabled = enabled_sids

    def is_enabled(self, sid: str) -> bool:
        return sid in self._enabled


def test_default_session_is_companion() -> None:
    cm = _StubCodeMode(enabled_sids=set())
    assert is_companion_session("default", cm) is True


def test_code_session_is_not_companion() -> None:
    cm = _StubCodeMode(enabled_sids={"default"})
    # base session "default" 已进 code mode → 不是 companion
    assert is_companion_session("default", cm) is False


def test_code_prefixed_sid_is_not_companion() -> None:
    cm = _StubCodeMode(enabled_sids=set())
    # code-<sha> 形式的 sid 直接判 code
    assert is_companion_session("code-tyfbt62t", cm) is False


def test_companion_when_code_mode_manager_missing() -> None:
    # 没有 code_mode manager（早期 init / 测试）→ 非 code- 前缀都按 companion
    assert is_companion_session("default", None) is True
    assert is_companion_session("code-abc12345", None) is False


# ---------------------------------------------------------------------------
# Scenario: Companion session blocked from writing into a code repo
# ---------------------------------------------------------------------------
def test_companion_write_outside_workspace_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    repo_path = "/path/to/deskpet\\backend\\vpn-cli"
    err = write_scope_check(repo_path, scope_root=ws)
    assert err is not None  # 拒绝
    assert "companion" in err.lower() or "陪伴" in err
    assert "code 模式" in err


def test_companion_mkdir_command_outside_workspace_rejected(tmp_path: Path) -> None:
    """run_shell 里的 mkdir 越界也要拦（取命令里的目标路径）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    # 绝对路径 mkdir 到仓库
    err = write_scope_check(
        "/path/to/deskpet\\backend\\vpn-cli", scope_root=ws
    )
    assert err is not None


# ---------------------------------------------------------------------------
# Scenario: Companion session may write inside workspace
# ---------------------------------------------------------------------------
def test_companion_write_inside_workspace_ok(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    inside = str(ws / "notes.md")
    assert write_scope_check(inside, scope_root=ws) is None


def test_companion_write_nested_inside_workspace_ok(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    (ws / "sub").mkdir(parents=True)
    inside = str(ws / "sub" / "deep" / "file.txt")
    assert write_scope_check(inside, scope_root=ws) is None


def test_companion_relative_path_resolves_under_workspace(tmp_path: Path) -> None:
    """相对路径按 scope_root 解析，留在 workspace 内 → 放行。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    assert write_scope_check("notes/today.md", scope_root=ws) is None


def test_path_traversal_escape_rejected(tmp_path: Path) -> None:
    """`../` 逃逸 workspace → 拒绝（resolve 后前缀校验）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    escape = str(ws / ".." / "secret.txt")
    assert write_scope_check(escape, scope_root=ws) is not None


# ---------------------------------------------------------------------------
# Scenario: write_scope_enforced=false restores legacy behavior
# ---------------------------------------------------------------------------
def test_no_scope_root_means_no_check(tmp_path: Path) -> None:
    """scope_root=None（未注入 = 关闭/code session）→ 永不拦。"""
    assert write_scope_check("/path/to/deskpet\\anything", scope_root=None) is None


# ---------------------------------------------------------------------------
# resolve_workspace_root：默认 %APPDATA%/deskpet/workspace，可配置覆盖
# ---------------------------------------------------------------------------
def test_resolve_workspace_root_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    root = resolve_workspace_root(configured="")
    assert root == (tmp_path / "workspace").resolve()


def test_resolve_workspace_root_configured_override(tmp_path: Path) -> None:
    custom = tmp_path / "my_ws"
    root = resolve_workspace_root(configured=str(custom))
    assert root == custom.resolve()


# ---------------------------------------------------------------------------
# scope_violation_message 文案符合 spec 原文
# ---------------------------------------------------------------------------
def test_scope_violation_message_text() -> None:
    msg = scope_violation_message()
    assert msg == (
        "companion session 写盘限定在 workspace；要写项目代码请进 code 模式并选择项目"
    )


# ---------------------------------------------------------------------------
# 集成：write_file 工具读到 _write_scope_root 后拦越界写
# ---------------------------------------------------------------------------
def test_write_file_honors_injected_scope_root(tmp_path: Path) -> None:
    """write_file 拿到 _write_scope_root（由 chat handler 经 session_context
    注入）后，越界写返回 ok:false + 引导文案，且不创建文件。
    """
    from deskpet.tools.os_tools.write_file import write_file

    ws = tmp_path / "workspace"
    ws.mkdir()
    target = tmp_path / "outside" / "evil.txt"

    result = write_file(
        {
            "path": str(target),
            "content": "x",
            "_write_scope_root": str(ws),
        }
    )
    payload = json.loads(result)
    assert payload.get("ok") is False
    assert "companion" in (payload.get("error", "") + payload.get("hint", "")).lower() or \
        "code 模式" in (payload.get("error", "") + payload.get("hint", ""))
    assert not target.exists()  # 没有任何文件被建


def test_write_file_inside_scope_root_succeeds(tmp_path: Path) -> None:
    from deskpet.tools.os_tools.write_file import write_file

    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "notes.md"
    result = write_file(
        {
            "path": str(target),
            "content": "hello",
            "_write_scope_root": str(ws),
        }
    )
    payload = json.loads(result)
    assert payload.get("ok") is not False
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_file_no_scope_root_legacy_free_write(tmp_path: Path) -> None:
    """没有 _write_scope_root（code session / flag off）→ 旧自由写盘。"""
    from deskpet.tools.os_tools.write_file import write_file

    target = tmp_path / "anywhere" / "file.txt"
    result = write_file({"path": str(target), "content": "ok"})
    payload = json.loads(result)
    assert payload.get("ok") is not False
    assert target.exists()


def test_edit_file_honors_injected_scope_root(tmp_path: Path) -> None:
    from deskpet.tools.os_tools.edit_file import edit_file

    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("hello world", encoding="utf-8")

    result = edit_file(
        {
            "path": str(outside),
            "old_string": "hello",
            "new_string": "bye",
            "_write_scope_root": str(ws),
        }
    )
    payload = json.loads(result)
    assert payload.get("ok") is False
    # 文件内容未被改
    assert outside.read_text(encoding="utf-8") == "hello world"


def test_run_shell_honors_injected_scope_root_for_mkdir(tmp_path: Path) -> None:
    """run_shell 里 mkdir 一个 workspace 外的绝对路径 → 拦下，不执行。"""
    from deskpet.tools.os_tools.run_shell import run_shell

    ws = tmp_path / "workspace"
    ws.mkdir()
    victim = tmp_path / "repo" / "vpn-cli"

    result = run_shell(
        {
            "command": f'mkdir -p "{victim}"',
            "_write_scope_root": str(ws),
        }
    )
    payload = json.loads(result)
    assert payload.get("ok") is False
    assert not victim.exists()


def test_run_shell_no_mkdir_unaffected(tmp_path: Path) -> None:
    """run_shell 非写盘命令（echo）即使有 scope_root 也照常执行。"""
    from deskpet.tools.os_tools.run_shell import run_shell

    ws = tmp_path / "workspace"
    ws.mkdir()
    result = run_shell(
        {"command": "echo scoped_ok", "_write_scope_root": str(ws)}
    )
    payload = json.loads(result)
    assert payload.get("ok") is not False
    assert "scoped_ok" in payload.get("stdout", "")
