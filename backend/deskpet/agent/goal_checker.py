# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B2 — GoalChecker（PRD Stage B § B2）.

Lightweight LLM-backed判定: 给定 ``goal_text`` 和最近 working_messages，
判断 goal 是否达成。返 ``(done: bool, hint: str)``，``hint`` 是 done=False
时的"还差什么"提示，done=True 时为空串。

设计纪律:
  - **JSON 解析容错**: LLM 输出可能被 ```json ... ``` 包裹 / 含 markdown
    / 含前后解释文本 — 用尽 3 级 fallback 提取首个合法 JSON 对象。
  - **Safe-fail**: LLM 调用本身异常 → 返 ``(True, "checker_error")``
    意为"放行 + 留痕"。AgentLoop 看到 done=True 时 mark_done，避免
    checker 故障阻塞终态。这符合 ``verify_gate`` "守门失败不阻 dispatch"
    的同源原则。
  - **prompt 简短**: 不喂全 working_messages（容易爆 token），只取最近
    5 轮 assistant message 摘要。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# 最多喂给 checker 的最近 assistant 轮数
_RECENT_TURNS = 5
# 单条消息截断长度（防 prompt 爆 token）
_MSG_TRUNC = 400


_PROMPT_TEMPLATE = (
    "Goal: {goal_text}\n\n"
    "Recent assistant work (last {n} turns):\n"
    "{summary}\n\n"
    "Is the goal achieved? Output ONLY a JSON object on a single line, "
    "no markdown, no explanation:\n"
    '{{"done": true|false, "hint": "<what\'s missing if not done; empty if done>"}}'
)


def _recent_msgs_summary(working_msgs: list[dict[str, Any]]) -> str:
    """提取最近 _RECENT_TURNS 条 assistant 消息内容，每条截断。

    跳过 system / tool / user 消息（goal-checker 只看 assistant 产出）。
    返回换行分隔的 bullet list。
    """
    out: list[str] = []
    for m in reversed(working_msgs):
        if len(out) >= _RECENT_TURNS:
            break
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if content is None:
            continue
        if isinstance(content, list):
            # OpenAI content blocks → 拼接 text 块
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict) and blk.get("type") in ("text", "output_text")
            ]
            text = " ".join(p for p in parts if p)
        else:
            text = str(content)
        text = text.strip()
        if not text:
            continue
        if len(text) > _MSG_TRUNC:
            text = text[:_MSG_TRUNC] + "…"
        out.append(f"  - {text}")
    if not out:
        return "  (no assistant turns yet)"
    # 倒序 → 让最新一轮在末尾（更符合 LLM "what just happened" 直觉）
    return "\n".join(reversed(out))


_FENCED_JSON_RX = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
_BARE_JSON_RX = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """3 级 fallback 提 JSON 对象:

    1. 直 ``json.loads(raw.strip())`` — 最理想
    2. 找 ``` ```json ... ``` ``` fenced block
    3. 找首个 ``{...}`` substring 尝试 parse

    返 ``None`` 表示无法 parse。
    """
    s = raw.strip()
    # 1. 直 parse
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. fenced block
    m = _FENCED_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. 首个 {...}
    m = _BARE_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    return None


class GoalChecker:
    """LLM-backed goal-达成判定.

    Parameters
    ----------
    llm_call:
        ``async (prompt: str) -> str`` callable. 通常由 main.py lifespan
        注入，绑定 LLM registry 的 ephemeral subagent endpoint（参考
        ``verify_gate.consult_ephemeral_subagent`` 模式）。
    """

    def __init__(self, llm_call: Callable[[str], Awaitable[str]]) -> None:
        self.llm_call = llm_call

    async def check(
        self,
        goal_text: str,
        working_msgs: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        """判定 goal 是否达成。

        Returns
        -------
        (done, hint):
            done=True / hint="" → 达成；AgentLoop ``mark_done``。
            done=False / hint=<text> → 未达成；AgentLoop 回灌 hint
                给下一轮 LLM。
            done=True / hint="checker_error" → LLM 异常 safe-fail；
                AgentLoop ``mark_done`` 避免死循环。
        """
        summary = _recent_msgs_summary(working_msgs)
        prompt = _PROMPT_TEMPLATE.format(
            goal_text=goal_text,
            n=_RECENT_TURNS,
            summary=summary,
        )
        try:
            raw = await self.llm_call(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("goal_checker llm_call failed: %s", exc)
            return (True, "checker_error")

        if not isinstance(raw, str):
            logger.warning("goal_checker llm returned non-str: %r", type(raw))
            return (True, "checker_error")

        obj = _extract_json(raw)
        if obj is None:
            logger.warning(
                "goal_checker could not parse JSON from llm output: %r",
                raw[:200],
            )
            # 无法 parse → 保守放行 (safe-fail)
            return (True, "checker_error")

        done_val = obj.get("done")
        # 容错: "true" / "yes" / 1 也接受
        if isinstance(done_val, bool):
            done = done_val
        elif isinstance(done_val, str):
            done = done_val.strip().lower() in ("true", "yes", "1", "done")
        elif isinstance(done_val, (int, float)):
            done = bool(done_val)
        else:
            logger.warning(
                "goal_checker missing 'done' field; defaulting to safe-fail",
            )
            return (True, "checker_error")

        hint_val = obj.get("hint", "")
        hint = str(hint_val) if hint_val is not None else ""
        if done:
            return (True, "")
        return (False, hint)


__all__ = ["GoalChecker"]
