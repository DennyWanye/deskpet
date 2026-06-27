# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-3 单测 — plan.py companion plan 升级（决策2：code 模式分支不动）。

覆盖（05 L1）：
  - 旧签名 BC：不传新参 → companion 路径仍 return None（与旧 `if not in_code_mode` 一致）
  - code mode 行为不回归：in_code_mode=True 走 PLAN_SCHEMA（不含 parallelizable）
  - companion_enabled + 复杂 problem_type → 出计划，走 PLAN_SCHEMA_COMPANION
  - companion 简单 problem_type（factual_qa）→ 不出计划
  - attack_order 注入 prompt（计划首步对准 principal）
  - parallelizable 解析
  - 3 级 JSON 容错（fenced 围栏）
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from agent.plan import (
    PLAN_SCHEMA, PLAN_SCHEMA_COMPANION, Plan, PlanStep, maybe_extract_plan,
)


_LONG_MSG = "我的登录功能一直报错，帮我系统排查一下到底哪里出了问题" * 2


def _provider(payload: str) -> AsyncMock:
    prov = AsyncMock()
    prov.chat_with_tools = AsyncMock(return_value={"content": payload})
    return prov


def _plan_payload(with_parallel: bool = False) -> str:
    step = {"title": "查 token", "detail": "检查 token 是否过期"}
    if with_parallel:
        step["parallelizable"] = True
    return json.dumps({"rationale": "先查认证", "steps": [step]})


def test_bc_old_signature_companion_returns_none() -> None:
    """旧调用方不传新参 → companion 路径仍 return None（BC）。"""
    prov = _provider(_plan_payload())
    plan = asyncio.run(maybe_extract_plan(prov, _LONG_MSG, None, in_code_mode=False))
    assert plan is None
    prov.chat_with_tools.assert_not_called()  # 早退，不调 LLM


def test_code_mode_unchanged_uses_base_schema() -> None:
    """决策2：code 模式走原 PLAN_SCHEMA（不含 parallelizable）。"""
    prov = _provider(_plan_payload())
    plan = asyncio.run(maybe_extract_plan(prov, _LONG_MSG, "/proj", in_code_mode=True))
    assert isinstance(plan, Plan)
    # 用了 base schema
    _, kwargs = prov.chat_with_tools.call_args
    assert kwargs["response_format"] is PLAN_SCHEMA
    assert kwargs["response_format"]["json_schema"]["name"] == "code_mode_plan"


def test_code_mode_short_message_still_skips() -> None:
    """code 模式短消息仍跳过（_PLAN_MIN_CHARS 行为不回归）。"""
    prov = _provider(_plan_payload())
    plan = asyncio.run(maybe_extract_plan(prov, "ls", "/proj", in_code_mode=True))
    assert plan is None


def test_companion_enabled_complex_generates_plan() -> None:
    prov = _provider(_plan_payload(with_parallel=True))
    plan = asyncio.run(maybe_extract_plan(
        prov, _LONG_MSG, None,
        in_code_mode=False, companion_enabled=True, problem_type="debug",
    ))
    assert isinstance(plan, Plan)
    # 走 companion schema
    _, kwargs = prov.chat_with_tools.call_args
    assert kwargs["response_format"] is PLAN_SCHEMA_COMPANION
    assert kwargs["response_format"]["json_schema"]["name"] == "companion_plan"
    # parallelizable 解析
    assert plan.steps[0].parallelizable is True


def test_companion_simple_problem_type_skips() -> None:
    """companion 但 problem_type=factual_qa（非复杂）→ 不出计划。"""
    prov = _provider(_plan_payload())
    plan = asyncio.run(maybe_extract_plan(
        prov, _LONG_MSG, None,
        in_code_mode=False, companion_enabled=True, problem_type="factual_qa",
    ))
    assert plan is None
    prov.chat_with_tools.assert_not_called()


def test_attack_order_injected_into_prompt() -> None:
    prov = _provider(_plan_payload())
    asyncio.run(maybe_extract_plan(
        prov, _LONG_MSG, None,
        in_code_mode=False, companion_enabled=True, problem_type="multi_task",
        attack_order=[2, 1],
        contradiction_descs={1: "token 过期", 2: "数据库连接超时"},
    ))
    args, _ = prov.chat_with_tools.call_args
    user_content = args[0][1]["content"]
    assert "主要矛盾攻击顺序" in user_content
    # attack_order=[2,1] → 首项是 id=2 的 desc
    assert "1. 数据库连接超时" in user_content
    assert "2. token 过期" in user_content


def test_parallelizable_defaults_false() -> None:
    prov = _provider(_plan_payload(with_parallel=False))
    plan = asyncio.run(maybe_extract_plan(
        prov, _LONG_MSG, None,
        in_code_mode=False, companion_enabled=True, problem_type="creation",
    ))
    assert plan.steps[0].parallelizable is False


def test_fenced_json_fallback() -> None:
    """M-4：companion 吐 ```json 围栏也能解析（3 级容错）。"""
    fenced = "思考中...\n```json\n" + _plan_payload() + "\n```"
    prov = _provider(fenced)
    plan = asyncio.run(maybe_extract_plan(
        prov, _LONG_MSG, None,
        in_code_mode=False, companion_enabled=True, problem_type="debug",
    ))
    assert isinstance(plan, Plan)
    assert plan.steps[0].title == "查 token"


def test_base_schema_has_no_parallelizable() -> None:
    """决策2 硬断言：共享 PLAN_SCHEMA 未被污染（code 模式字节级不动）。"""
    base_step = PLAN_SCHEMA["json_schema"]["schema"]["properties"]["steps"]["items"]
    assert "parallelizable" not in base_step["properties"]
    assert base_step["required"] == ["title", "detail"]
    # companion schema 才有
    comp_step = PLAN_SCHEMA_COMPANION["json_schema"]["schema"]["properties"]["steps"]["items"]
    assert "parallelizable" in comp_step["properties"]
