"""Slash command dispatcher — WI-A2 v1.

用户在桌宠 chat 框输 `/<cmd> [args]` → InputBar 发 `slash_command` WS →
main.py handler 调 dispatch_slash_command。

约定:
  - cmd 不区分大小写（lowercased）
  - args 是一个空格分隔的 raw string；handler 自行 split
  - 返回 dict {type, ...} — type ∈ {"skill_result", "goal_set", "goal_cleared",
    "help", "error"}

Builtin commands:
  - /help — 列出所有可用 skill + builtin command
  - /goal <text>   — 设置 session-level 目标
  - /goal clear    — 清除目标
  - /<skill_name>  — 调用 SkillLoader（直接走 invoke_script，不经 LLM）
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


async def dispatch_slash_command(
    name: str,
    args: str,
    session_id: str,
    *,
    skill_loader: Any = None,
    session_goal_store: Any = None,
) -> dict[str, Any]:
    """Route a slash command. Returns response dict (always non-empty).

    Args:
        name: command name without leading "/" (e.g. "help", "ppt-generate")
        args: raw arg string (handler split as needed)
        session_id: session for goal context
        skill_loader: SkillLoader instance (or None when v1 feature flag off)
        session_goal_store: SessionGoalStore (or None)
    """
    name = (name or "").strip().lower()
    args = (args or "").strip()

    if not name:
        return {"type": "error", "message": "empty command"}

    # Builtin: /help
    if name == "help":
        return _handle_help(skill_loader)

    # Builtin: /goal
    if name == "goal":
        return _handle_goal(args, session_id, session_goal_store)

    # Skill: /<skill_name>
    if skill_loader is not None:
        return await _handle_skill(name, args, skill_loader)

    return {
        "type": "error",
        "message": f"unknown command: /{name}",
        "hint": "use /help to list available commands",
    }


def _handle_help(skill_loader: Any) -> dict[str, Any]:
    builtins = [
        {"name": "help", "description": "列出所有可用命令 + skill"},
        {"name": "goal <text>", "description": "设置 session 级长期目标"},
        {"name": "goal clear", "description": "清除当前 goal"},
    ]
    skills: list[dict[str, str]] = []
    if skill_loader is not None:
        try:
            for s in skill_loader.list_skills():
                skills.append({
                    "name": s.get("name") or "",
                    "description": (s.get("description") or "")[:120],
                })
        except Exception as exc:  # noqa: BLE001
            log.warning("list_skills failed: %s", exc)
    return {"type": "help", "builtins": builtins, "skills": skills}


def _handle_goal(
    args: str, session_id: str, store: Any,
) -> dict[str, Any]:
    if store is None:
        return {
            "type": "error",
            "message": "goal feature disabled (set [features] goal_mode = true)",
        }
    args_lower = args.lower().strip()
    if not args or args_lower == "clear" or args_lower == "":
        if args_lower == "clear":
            ok = store.clear(session_id)
            return {"type": "goal_cleared", "session_id": session_id, "ok": ok}
        # show current
        current = store.get(session_id)
        if current is None:
            return {"type": "goal_status", "active": False}
        return {
            "type": "goal_status",
            "active": True,
            "text": current.text,
            "iterations_used": current.iterations_used,
            "max_iterations": current.max_iterations,
            "done": current.done,
        }
    # set
    goal = store.set(session_id, args)
    return {
        "type": "goal_set",
        "session_id": session_id,
        "text": goal.text,
        "max_iterations": goal.max_iterations,
    }


async def _handle_skill(
    name: str, args: str, skill_loader: Any,
) -> dict[str, Any]:
    # Translate dash → look up; SkillLoader stores by skill dir name.
    skill_args = args.split() if args else []
    try:
        # invoke_script raises KeyError if skill not found
        out = await skill_loader.invoke_script(name, args=skill_args)
    except KeyError:
        return {
            "type": "error",
            "message": f"unknown skill: /{name}",
            "hint": "use /help to list available skills",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "type": "error",
            "message": f"skill invocation failed: {type(exc).__name__}: {exc}",
        }
    return {
        "type": "skill_result",
        "skill": name,
        "args": args,
        "output": out,
    }
