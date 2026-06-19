# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-2.1 — StructuredReflection schema + prompt builder + JSON parser.

设计纪律（与 goal_checker 同源）：
  - **JSON 解析容错**：3 级 fallback（直 parse → fenced block → 首个 ``{...}``）
  - **Safe-fail**：解析失败 → 返 ``None``，不阻断 dispatch
  - **默认值**：str 字段缺失 → ""；confidence 缺失 → 0.5（不误触发 2.4 evaluator）
  - **防回归**：_REFLECTION_INSTRUCTION 禁止出现"改口说已完成"类表述
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class StructuredReflection:
    """LLM 在被 verify-gate/selfcheck 拦截后必须产出的 5 段结构化反思。

    字段语义（DeskPet 裁剪版，避免 token 膨胀）：
      error_analysis     — 上一轮为何没达成 / 校验不过（根因，非复述报错）
      execution_critique — 对自己已走路径的批判（是否走了惯性短路径）
      task_replanning    — 据批判产出的新方案（必须与上轮不同的具体动作）
      next_action        — 下一步要调的具体工具+参数意图（驱动 WI-2.2 重试）
      confidence         — 0-1，自评新方案成功概率（<0.3 → 升级到 2.4 evaluator）
    """
    error_analysis: str = ""
    execution_critique: str = ""
    task_replanning: str = ""
    next_action: str = ""
    confidence: float = 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Prompt instruction text injected at rebound points
# ──────────────────────────────────────────────────────────────────────────────

_REFLECTION_INSTRUCTION = (
    "\n\n[结构化反思 — 必须先输出]\n"
    "你上一轮的 end_turn 被拦截，因为声称的动作没有对应的执行收据。\n"
    "在下一轮回复的**最开头**，你必须先输出一个 JSON 代码块，格式如下：\n\n"
    "```json\n"
    "{\n"
    '  "error_analysis": "<上一轮失败的根本原因，不要复述报错信息>",\n'
    '  "execution_critique": "<对自己已走路径的批判：是否走了惯性短路径？>",\n'
    '  "task_replanning": "<与上轮完全不同的具体新方案，必须包含要调用的工具和参数>",\n'
    '  "next_action": "<下一步具体工具名+参数意图>",\n'
    '  "confidence": 0.0\n'
    "}\n"
    "```\n\n"
    "要求：\n"
    "1. task_replanning 必须与上轮路径不同，给出具体工具调用方案。\n"
    "2. 输出完 JSON 代码块后，立即执行 task_replanning 中描述的工具调用。\n"
    "3. 不要重复解释为什么上一轮失败，直接给出新方案并执行。\n"
    "4. confidence 填 0.0-1.0 之间的数字，反映你对新方案的把握程度。\n"
)


# ──────────────────────────────────────────────────────────────────────────────
# JSON extraction (same 3-level fallback as goal_checker._extract_json)
# ──────────────────────────────────────────────────────────────────────────────

_FENCED_JSON_RX = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
_BARE_JSON_RX = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:  # type: ignore[type-arg]
    """3 级 fallback 提 JSON 对象（与 goal_checker._extract_json 同源）。

    1. 直 ``json.loads(raw.strip())``
    2. fenced json code block
    3. 首个 ``{...}`` substring

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


# ──────────────────────────────────────────────────────────────────────────────
# Public parser
# ──────────────────────────────────────────────────────────────────────────────

def _emit_reflection_parse_failed(text_len: int) -> None:
    """Best-effort metric emit for reflection parse failure（R-T3 §15.4）。

    失败不抛 — metric 不可用时不阻断降级逻辑。
    """
    try:
        from observability.metrics_sink import record as _metric  # noqa: PLC0415
        _metric("reflection_parse_failed", {"count": 1})
    except Exception:  # noqa: BLE001
        pass


def parse_reflection(assistant_text: str) -> StructuredReflection | None:
    """Parse LLM assistant text into a StructuredReflection.

    Uses the same 3-level JSON fallback as GoalChecker to handle the full
    range of real-world LLM output variability (bare JSON / fenced / embedded).

    Returns
    -------
    StructuredReflection
        When a JSON dict was successfully extracted.  Missing fields get
        safe defaults (str → "", confidence → 0.5).
    None
        When no JSON dict could be extracted at all.  Caller should fall back
        to plain-text nudge behaviour (safe-fail — never blocks dispatch).
    """
    if not assistant_text or not assistant_text.strip():
        return None

    obj = _extract_json(assistant_text)
    if obj is None:
        logger.debug(
            "parse_reflection: could not extract JSON from text (len=%d)",
            len(assistant_text),
        )
        # R-T3 §15.4: 反思畸形 → 降级机械 nudge + 记降级事实
        _emit_reflection_parse_failed(len(assistant_text))
        return None

    # Extract str fields with "" default
    error_analysis = str(obj.get("error_analysis") or "")
    execution_critique = str(obj.get("execution_critique") or "")
    task_replanning = str(obj.get("task_replanning") or "")
    next_action = str(obj.get("next_action") or "")

    # confidence: must be 0-1 float; missing or invalid → 0.5
    raw_conf = obj.get("confidence")
    if raw_conf is None:
        confidence = 0.5
    else:
        try:
            confidence = float(raw_conf)
            # Clamp to [0, 1]
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

    return StructuredReflection(
        error_analysis=error_analysis,
        execution_critique=execution_critique,
        task_replanning=task_replanning,
        next_action=next_action,
        confidence=confidence,
    )


__all__ = [
    "StructuredReflection",
    "_REFLECTION_INSTRUCTION",
    "parse_reflection",
]
