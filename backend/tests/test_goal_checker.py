# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B2 — GoalChecker unit tests (PRD Stage B)."""
from __future__ import annotations

import pytest

from deskpet.agent.goal_checker import GoalChecker


def _make_llm(response: str):
    async def _call(prompt: str) -> str:
        return response
    return _call


def _make_failing_llm(exc: Exception):
    async def _call(prompt: str) -> str:
        raise exc
    return _call


@pytest.mark.asyncio
async def test_clean_json_done_true():
    checker = GoalChecker(_make_llm('{"done": true, "hint": ""}'))
    done, hint = await checker.check("write hello", [])
    assert done is True
    assert hint == ""


@pytest.mark.asyncio
async def test_clean_json_done_false_with_hint():
    checker = GoalChecker(
        _make_llm('{"done": false, "hint": "需要先创建文件"}')
    )
    done, hint = await checker.check("create x.txt", [])
    assert done is False
    assert hint == "需要先创建文件"


@pytest.mark.asyncio
async def test_markdown_wrapped_json_still_parses():
    raw = (
        "Sure, here's my assessment:\n\n"
        "```json\n"
        '{"done": false, "hint": "tests still failing"}\n'
        "```\n\n"
        "Let me know if you need more detail."
    )
    checker = GoalChecker(_make_llm(raw))
    done, hint = await checker.check("make tests pass", [])
    assert done is False
    assert hint == "tests still failing"


@pytest.mark.asyncio
async def test_bare_object_fallback_when_no_fences():
    raw = 'Looks like {"done": true, "hint": ""} based on receipts.'
    checker = GoalChecker(_make_llm(raw))
    done, hint = await checker.check("x", [])
    assert done is True
    assert hint == ""


@pytest.mark.asyncio
async def test_llm_exception_safe_fails_to_skipped():
    # R-T3 §15.4 变更：LLM 异常 → (False, "goal_check=skipped")
    # 原行为 (True, "checker_error") 被变更是因为：
    #   done=True 会触发 mark_done，在 checker 本身故障时静默标目标完成 —
    #   这是危险的默认放行（尤其高后果目标）。
    # 新行为：done=False + hint="goal_check=skipped" 表示"无法正向确认"，
    # AgentLoop 看到 goal_check=skipped 时不调用 mark_done，保持目标 active。
    checker = GoalChecker(_make_failing_llm(RuntimeError("LLM blew up")))
    done, hint = await checker.check("x", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_unparseable_output_safe_fails_to_skipped():
    # R-T3 §15.4 变更：JSON 畸形 → (False, "goal_check=skipped")（同上理由）
    checker = GoalChecker(_make_llm("I cannot parse this at all"))
    done, hint = await checker.check("x", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_missing_done_field_safe_fails_to_skipped():
    # R-T3 §15.4 变更：缺 done 字段 → (False, "goal_check=skipped")（同上理由）
    checker = GoalChecker(_make_llm('{"hint": "no done field"}'))
    done, hint = await checker.check("x", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_done_string_true_accepted():
    checker = GoalChecker(_make_llm('{"done": "true", "hint": ""}'))
    done, hint = await checker.check("x", [])
    assert done is True


@pytest.mark.asyncio
async def test_done_int_zero_treated_as_false():
    checker = GoalChecker(_make_llm('{"done": 0, "hint": "not yet"}'))
    done, hint = await checker.check("x", [])
    assert done is False
    assert hint == "not yet"


@pytest.mark.asyncio
async def test_prompt_includes_goal_text_and_recent_assistant_turns():
    captured: dict = {}

    async def _capture(prompt: str) -> str:
        captured["prompt"] = prompt
        return '{"done": true, "hint": ""}'

    checker = GoalChecker(_capture)
    working_msgs = [
        {"role": "user", "content": "user-1"},
        {"role": "assistant", "content": "assistant-turn-1"},
        {"role": "tool", "content": "tool-output"},
        {"role": "assistant", "content": "assistant-turn-2-LATEST"},
    ]
    await checker.check("MY-GOAL-TEXT", working_msgs)
    p = captured["prompt"]
    assert "MY-GOAL-TEXT" in p
    # both assistant messages should appear; user/tool should not
    assert "assistant-turn-1" in p
    assert "assistant-turn-2-LATEST" in p
    assert "user-1" not in p
    assert "tool-output" not in p


@pytest.mark.asyncio
async def test_long_assistant_message_truncated_in_prompt():
    captured: dict = {}

    async def _capture(prompt: str) -> str:
        captured["prompt"] = prompt
        return '{"done": true, "hint": ""}'

    checker = GoalChecker(_capture)
    very_long = "X" * 5000
    await checker.check("g", [{"role": "assistant", "content": very_long}])
    # 应该被截到 _MSG_TRUNC 范围 — 不会把 5000 字符全塞进去
    assert "X" * 5000 not in captured["prompt"]
    assert "…" in captured["prompt"] or "X" * 400 in captured["prompt"]


@pytest.mark.asyncio
async def test_non_string_llm_response_safe_fails_to_skipped():
    # R-T3 §15.4 变更：非字符串响应 → (False, "goal_check=skipped")
    async def _call(prompt: str):
        return 42  # type: ignore[return-value]

    checker = GoalChecker(_call)
    done, hint = await checker.check("x", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_content_blocks_list_format_handled():
    """OpenAI-style content blocks (list of dicts) 也应被摘要正确处理。"""
    captured: dict = {}

    async def _capture(prompt: str) -> str:
        captured["prompt"] = prompt
        return '{"done": true, "hint": ""}'

    checker = GoalChecker(_capture)
    working_msgs = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "block-1-text"},
            {"type": "text", "text": "block-2-text"},
        ],
    }]
    await checker.check("g", working_msgs)
    assert "block-1-text" in captured["prompt"]
    assert "block-2-text" in captured["prompt"]
