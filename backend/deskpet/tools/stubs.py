"""P4-S5: stub tools for features owned by later slices.

WI-T4.1 v3 D11（守卫模式）：``registry.has(name)`` 检查 + ``replace_allowed=True``
opt-in。pkgutil discovery 按字典序加载，``memory_tools.py`` ('m') < ``stubs.py``
('s')，所以 memory_tools.py 的 memory_forget 等真实现先注册；本文件后跑时若 name
已被真实现占用 → 跳过（不覆盖）。同理 T3.2/T3.3 时 skill_invoke 真实现
(``skill_tools.py`` 等)、mcp_call/delegate 直接 unregister，本文件不会盖到。

Stubs grouped by owning slice:

* memory_{write,read,search}      — P4-S4 → 升级路径 T3.1 memory_tools.py
                                    schema migration（旧名透明翻译到 v2 真实现）
* delegate                         — T3.3 v3 直接删（无真 caller）
* skill_invoke                     — P4-S10 → T3.2 真实现接 SkillLoader
* mcp_call                         — T3.3 v3 直接删（无真 caller）
"""
from __future__ import annotations

import json
from typing import Any

from .registry import registry


def _stub_handler(slice_name: str):
    """Return a handler closure that reports the owning slice."""

    def handler(args: dict[str, Any], task_id: str) -> str:
        return json.dumps(
            {
                "error": f"not implemented (pending {slice_name})",
                "retriable": False,
            },
            ensure_ascii=False,
        )

    return handler


def _maybe_register(
    name: str, toolset: str, schema: dict[str, Any], slice_name: str,
) -> None:
    """WI-T4.1 v3 守卫注册：name 已存在 → 跳过（真实现优先）.

    本函数显式 ``replace_allowed=True`` 仅为标记"我是 stub，谁要覆盖我都行"。
    实际守卫由 ``registry.has(name)`` 提供 — name 存在则直接 return，
    不调 register，永远不会触发覆盖。
    """
    if registry.has(name):
        return  # 真实现已注册（pkgutil 字典序：memory_tools < stubs）
    registry.register(
        name, toolset, schema, _stub_handler(slice_name),
        replace_allowed=True,  # 显式 opt-in: 真实现 import 时合法覆盖本 stub
    )


# ---------------------------------------------------------------------
# memory_* — P4-S4 → T3.1 schema migration 真实现
# ---------------------------------------------------------------------
_MEMORY_WRITE_SCHEMA: dict[str, Any] = {
    "name": "memory_write",
    "description": (
        "Persist a fact / observation to DeskPet long-term memory. "
        "(Pending T3.1 schema migration to FactsStore.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Content to remember."},
            "tier": {
                "type": "string",
                "enum": ["l1", "l2", "l3", "auto"],
                "default": "auto",
            },
            "salience": {
                "type": "number",
                "description": "0.0-1.0 importance. Default 0.5.",
                "default": 0.5,
            },
        },
        "required": ["text"],
    },
}
_MEMORY_READ_SCHEMA: dict[str, Any] = {
    "name": "memory_read",
    "description": (
        "Read a specific memory record by id. "
        "(Pending T3.1 → FactsStore.get_by_id.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "integer"},
        },
        "required": ["memory_id"],
    },
}
_MEMORY_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "memory_search",
    "description": (
        "Hybrid recall across L1+L2+L3 (vec + FTS5 + recency + salience) "
        "with RRF fusion. (Pending T3.1 → EnhancedRetriever.recall.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}

_maybe_register("memory_write", "memory", _MEMORY_WRITE_SCHEMA, "T3.1")
_maybe_register("memory_read", "memory", _MEMORY_READ_SCHEMA, "T3.1")
_maybe_register("memory_search", "memory", _MEMORY_SEARCH_SCHEMA, "T3.1")


# ---------------------------------------------------------------------
# control namespace: skill_invoke
# (mcp_call / delegate 不再注册 — T3.3 v3 决策 D10：直接删，无真 caller)
# ---------------------------------------------------------------------
_SKILL_INVOKE_SCHEMA: dict[str, Any] = {
    "name": "skill_invoke",
    "description": (
        "Invoke a DeskPet skill by name with arguments. Skills are "
        "composable multi-step procedures. (Pending T3.2 SkillLoader.)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["skill_name"],
    },
}

_maybe_register("skill_invoke", "control", _SKILL_INVOKE_SCHEMA, "T3.2")
