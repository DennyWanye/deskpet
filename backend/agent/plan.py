# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S25 A2 — Plan/Replan helper.

Before launching the ReAct loop on a complex code-mode request, do a
single Structured-Output (JSON schema) LLM call to extract a small plan
of steps. The plan is:

1. Sent to the frontend as ``chat_v2_plan`` so the user can see it.
2. Injected as an extra ``system`` message into the working_messages
   the AgentLoop consumes, so the LLM stays anchored to the plan
   while it dispatches tools.

Auto-confirm by default — we don't gate execution on user click.
That's a future enhancement; for now, just SHOWING the plan up-front
gives the user predictability + a way to cancel (停止 button).

Skips planning when:
  * Not in code mode (companion chat doesn't need it)
  * User message is short (<40 chars; "list files" doesn't need a plan)
  * The plan call itself errors (we degrade silently to plain ReAct)
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class PlanStep:
    title: str
    detail: str
    # 弹钢琴（plans/2026-06-24-... §M1 升级3）：本步是否可与其他步并行（次要子问题）。
    # 默认 False = BC：旧渲染/注入/code 模式调用方均不读它。
    parallelizable: bool = False


@dataclass
class Plan:
    steps: list[PlanStep]
    rationale: str


# OpenAI / the relay structured-output spec. Ollama gets `format: "json"`
# emitted alongside via the provider shim.
PLAN_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "code_mode_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rationale": {
                    "type": "string",
                    "description": "1-2 sentences on why this plan addresses the request.",
                },
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Short imperative phrase, e.g. 'Read package.json'.",
                            },
                            "detail": {
                                "type": "string",
                                "description": "1-2 sentences on what this step actually does.",
                            },
                        },
                        "required": ["title", "detail"],
                    },
                },
            },
            "required": ["rationale", "steps"],
        },
    },
}

# 决策2（plans/2026-06-24-...）：code 模式 plan 走 PLAN_SCHEMA **原样不动**。
# Companion 主线新增 plan 用独立 PLAN_SCHEMA_COMPANION（= PLAN_SCHEMA 深拷贝 + 每步加
# parallelizable 布尔），**绝不修改共享 PLAN_SCHEMA**，从而 code 模式的 LLM 调用字节级不变。
PLAN_SCHEMA_COMPANION: dict = copy.deepcopy(PLAN_SCHEMA)
PLAN_SCHEMA_COMPANION["json_schema"]["name"] = "companion_plan"
_companion_step = PLAN_SCHEMA_COMPANION["json_schema"]["schema"]["properties"]["steps"]["items"]
_companion_step["properties"]["parallelizable"] = {
    "type": "boolean",
    "description": "本步是否可与其他步并行（次要子问题，弹钢琴统筹）。",
}
# strict:True + additionalProperties:False → 新增 property 必须进 required。
_companion_step["required"] = ["title", "detail", "parallelizable"]

PLAN_SYSTEM = (
    "你是一名资深工程师，在动手写代码前先做规划。给定用户请求 + 项目根目录，"
    "产出 1-8 步的简明执行计划。每一步是一个小而可验证的工作单元；不要写代码，"
    "只做计划。严格按提供的 JSON schema 回应。"
)

# 3 级 JSON 提取（裸 / fenced ```json / bare {...}）——与 deskpet.agent.intent_triage 同源容错。
# 决策2/M-4：companion 走默认 gpt-5.5，对 response_format 兼容性更差，更易吐围栏/夹带思考文本，
# 单层 json.loads 会静默丢计划 → 这层 fallback 是刚需。对 code 路径只多 fallback、不改成功路径行为。
_FENCED_JSON_RX = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON_RX = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    s = (raw or "").strip()
    for candidate in (s,):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    m = _FENCED_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    m = _BARE_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    return None

# Don't bother planning short utterances.
_PLAN_MIN_CHARS = 40


_COMPANION_PLAN_TYPES = frozenset({"debug", "research", "multi_task", "creation"})


async def maybe_extract_plan(
    provider,
    user_message: str,
    project_root: str | None,
    *,
    in_code_mode: bool,
    companion_enabled: bool = False,        # 决策2：Companion 主线新增 plan（flag off=False=BC）
    problem_type: str | None = None,        # companion 模式按 problem_type 决定是否出计划
    attack_order: list[int] | None = None,  # 吃 Step3 主要矛盾排序（来自 IntentCard.contradiction）
    contradiction_descs: dict[int, str] | None = None,  # id→desc 供首步对准 principal
) -> Plan | None:
    """Run the plan call when conditions warrant; else return None.

    Returns None when:
      * Companion 路径未开（companion_enabled=False）或 problem_type 非复杂类
      * Message too short
      * Provider call errors (logged WARN, falls through to plain ReAct)
      * LLM returns malformed JSON

    决策2：code 模式分支（in_code_mode==True）**字节级原样不动**——直接跳过下面这个
    companion-only 早退块，走原有 code 路径（仅受 _PLAN_MIN_CHARS 约束）。新能力只在
    `not in_code_mode` 且 companion_enabled + 复杂 problem_type 时进入新 companion 分支。
    """
    if not in_code_mode:
        if not (companion_enabled and problem_type in _COMPANION_PLAN_TYPES):
            return None   # BC：companion_enabled=False（默认）→ 与旧 `if not in_code_mode: return None` 一致
    if len(user_message.strip()) < _PLAN_MIN_CHARS:
        return None

    # 升级2（弹钢琴/集中优势兵力）：companion 路径把主要矛盾 attack_order 注入 prompt，
    # 让计划首步对准 principal。code 路径 attack_order=None → 无注入（BC）。
    _ao_hint = ""
    if attack_order and contradiction_descs:
        ordered = [contradiction_descs.get(i, "") for i in attack_order if i in contradiction_descs]
        ordered = [d for d in ordered if d]
        if ordered:
            _ao_hint = (
                "\n\n[主要矛盾攻击顺序 — 计划首步必须对准第一项（集中优势兵力）]\n"
                + "\n".join(f"{idx}. {d}" for idx, d in enumerate(ordered, 1))
            )

    # 决策2：companion 路径用独立 schema（含 parallelizable）；code 路径用原 PLAN_SCHEMA（不动）。
    _companion_path = (not in_code_mode) and companion_enabled
    _schema = PLAN_SCHEMA_COMPANION if _companion_path else PLAN_SCHEMA

    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"项目根目录: {project_root or '(未设)'}\n\n"
                f"用户请求:\n{user_message}{_ao_hint}"
            ),
        },
    ]
    try:
        # P5-S1 D fix: bumped 800 → 2048. thinking-mode models
        # (deepseek-v4-pro etc.) commonly use 800-1500 tokens just
        # for <think>...</think> chain-of-thought before producing the
        # JSON plan. The original 800 cap meant `stop_reason='length'`
        # almost every call → `p4s25_plan_invalid_json` warnings →
        # silent fallback to non-planned ReAct. 2048 leaves comfortable
        # room for thinking + the small JSON output schema.
        raw = await provider.chat_with_tools(
            messages,
            tools=None,
            max_tokens=2048,
            temperature=0.3,
            response_format=_schema,
        )
    except Exception as exc:  # noqa: BLE001
        # the relay / sealos proxies often reject response_format with
        # thinking-mode models (HTTP 400). The fallback is graceful —
        # no plan = plain ReAct loop — so this is an expected
        # degradation, not an actual failure. INFO so it doesn't
        # spam the warning channel.
        log.info("p4s25_plan_call_skipped: %s", exc)
        return None

    content = raw.get("content") or ""
    if not content:
        return None
    # M-4：3 级容错（裸/fenced/bare）替代单层 json.loads。code 路径成功 case 行为不变
    # （裸 JSON 一级即命中），只多 fallback 兜 companion 高失败率（gpt-5.5 易吐围栏）。
    data = _extract_json(content)
    if data is None:
        # LLM returned non-JSON despite response_format. Fall through.
        log.info("p4s25_plan_invalid_json content_preview=%r", content[:120])
        return None

    raw_steps = data.get("steps") or []
    steps = [
        PlanStep(
            title=str(s.get("title", "")).strip() or "(no title)",
            detail=str(s.get("detail", "")).strip(),
            parallelizable=bool(s.get("parallelizable", False)),  # 升级3：默认 False = BC
        )
        for s in raw_steps
        if isinstance(s, dict)
    ]
    if not steps:
        return None
    return Plan(steps=steps, rationale=str(data.get("rationale", "")).strip())


def plan_to_system_message(plan: Plan) -> str:
    """Render a plan as a system message the agent loop can consume."""
    lines = ["要按以下计划执行（必要时可在工具结果后调整）："]
    for i, step in enumerate(plan.steps, 1):
        lines.append(f"{i}. {step.title} — {step.detail}")
    if plan.rationale:
        lines.append(f"\n依据：{plan.rationale}")
    return "\n".join(lines)
