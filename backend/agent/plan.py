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

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class PlanStep:
    title: str
    detail: str


@dataclass
class Plan:
    steps: list[PlanStep]
    rationale: str


# OpenAI / chinzy structured-output spec. Ollama gets `format: "json"`
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

PLAN_SYSTEM = (
    "你是一名资深工程师，在动手写代码前先做规划。给定用户请求 + 项目根目录，"
    "产出 1-8 步的简明执行计划。每一步是一个小而可验证的工作单元；不要写代码，"
    "只做计划。严格按提供的 JSON schema 回应。"
)

# Don't bother planning short utterances.
_PLAN_MIN_CHARS = 40


async def maybe_extract_plan(
    provider,
    user_message: str,
    project_root: str | None,
    *,
    in_code_mode: bool,
) -> Plan | None:
    """Run the plan call when conditions warrant; else return None.

    Returns None when:
      * Not in code mode
      * Message too short
      * Provider call errors (logged WARN, falls through to plain ReAct)
      * LLM returns malformed JSON
    """
    if not in_code_mode:
        return None
    if len(user_message.strip()) < _PLAN_MIN_CHARS:
        return None

    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"项目根目录: {project_root or '(未设)'}\n\n"
                f"用户请求:\n{user_message}"
            ),
        },
    ]
    try:
        raw = await provider.chat_with_tools(
            messages,
            tools=None,
            max_tokens=800,
            temperature=0.3,
            response_format=PLAN_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001
        # chinzy / sealos proxies often reject response_format with
        # thinking-mode models (HTTP 400). The fallback is graceful —
        # no plan = plain ReAct loop — so this is an expected
        # degradation, not an actual failure. INFO so it doesn't
        # spam the warning channel.
        log.info("p4s25_plan_call_skipped: %s", exc)
        return None

    content = raw.get("content") or ""
    if not content:
        return None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # LLM returned non-JSON despite response_format. Fall through.
        log.info("p4s25_plan_invalid_json content_preview=%r", content[:120])
        return None

    raw_steps = data.get("steps") or []
    steps = [
        PlanStep(
            title=str(s.get("title", "")).strip() or "(no title)",
            detail=str(s.get("detail", "")).strip(),
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
