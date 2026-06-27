# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T3.2 v3 — ``skill_invoke`` 真实现.

替换 ``stubs.py`` 同名 stub（字典序：'sk' < 'st' → 本文件先注册，stubs.py 守卫
模式跳过同名 stub，详 plans/2026-05-24-tool-layer-optimization-v3/00-PRD.md
§3.4 + TDD §A9）。

设计参考 ``memory_tools.py``：
* 顶层 ``registry.register`` —— pkgutil discovery 触发
* ``bind(skill_loader)`` —— main.py lifespan 在 SkillLoader 构造后注入
* import 时 ``_skill_loader=None`` → handler 返 "not bound" 错而不抛
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from deskpet.tools.registry import registry

log = logging.getLogger(__name__)

# Module-level handle — bind() in main.py lifespan 注入。
_skill_loader: Any = None


def bind(*, skill_loader: Any) -> None:
    """Inject SkillLoader. Idempotent."""
    global _skill_loader
    _skill_loader = skill_loader
    log.info(
        "skill_tools.bind: skill_loader=%s",
        type(skill_loader).__name__ if skill_loader is not None else None,
    )


def is_bound() -> bool:
    return _skill_loader is not None


_SCHEMA: dict[str, Any] = {
    "name": "skill_invoke",
    "description": (
        "Invoke a DeskPet skill by name. Skills are composable procedures "
        "(scripts under built-in or user skills directory)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Skill identifier (e.g. 'transcribe_audio').",
            },
            "arguments": {
                "type": "array",
                "description": "Optional list of string arguments to pass.",
                "items": {"type": "string"},
                "default": [],
            },
        },
        "required": ["skill_name"],
    },
}


async def _handle(args: dict, task_id: str) -> str:  # noqa: ARG001
    """Registry handler protocol: (args, task_id) -> JSON str."""
    if _skill_loader is None:
        return json.dumps({
            "ok": False,
            "error": "skill_invoke not bound (SkillLoader not initialized)",
        }, ensure_ascii=False)
    name = args.get("skill_name")
    if not name or not isinstance(name, str):
        return json.dumps({
            "ok": False,
            "error": "missing required field 'skill_name' (non-empty string)",
        }, ensure_ascii=False)
    raw_args = args.get("arguments") or []
    if isinstance(raw_args, list):
        str_args: list[str] = [str(a) for a in raw_args]
    else:
        str_args = [str(raw_args)]
    try:
        # invoke_script 返 stdout 字符串（已是 JSON 或 raw）
        out = await _skill_loader.invoke_script(name, args=str_args)
    except KeyError:
        return json.dumps({
            "ok": False,
            "error": f"skill not found: {name!r}",
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "ok": False,
            "error": f"skill_invoke failed: {type(exc).__name__}: {exc}",
        }, ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "skill": name,
        "output": out,
    }, ensure_ascii=False)


# 模块顶层 register — pkgutil discovery 触发
# 2026-05-30 P0 bug fix #8: permission_category="execute_command" 不在
# PermissionCategory Literal 白名单（8 个合法值：read_file/read_file_sensitive
# /write_file/desktop_write/shell/network/mcp_call/skill_install），导致
# skill_invoke 触发时 PermissionGate.check() 抛 ValueError → skill 全部
# fail，LLM stream 已经说"已生成"但 0 文件生成（fake completion）。
# 改用 'shell' — skill_invoke 性质等同 spawn 子进程/调任意工具，shell
# category 走 prompt 流，permission_auto_mode 启用时 auto-allow。
# 内部 tool 各自的 permission_category 仍各自 check（write_file 等），
# 这个改动不削弱沙箱。
registry.register(
    name="skill_invoke",
    toolset="control",
    schema=_SCHEMA,
    handler=_handle,
    permission_category="shell",
    source="builtin",
)
