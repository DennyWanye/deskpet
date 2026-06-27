# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TDD for deepseek-inline-cot-dsml-sanitize.

Fixtures captured verbatim from the real incident that corrupted
test-research-helper/backend/app/services/llm_service.py.
"""
import pytest

from providers._response_sanitizer import (
    extract_dsml_tool_calls,
    sanitize_response,
    strip_inline_reasoning,
)

# Real incident shape: orphan <｜end▁of▁thinking｜> + CoT + raw DSML block.
INCIDENT = (
    "# Inline thinking-tag filtering (stateful)\n"
    "<｜end▁of▁thinking｜>API 测试通过！LLM 正常工作。现在继续优化。让我并行推进多个模块。\n"
    "\n"
    '<｜｜DSML｜｜tool_calls>\n'
    '<｜｜DSML｜｜invoke name="todo_write">\n'
    '<｜｜DSML｜｜parameter name="items" string="false">'
    '[{"activeForm": "验证", "content": "验证后端", "status": "completed"}]'
)


def test_strip_closed_deepseek_thinking():
    src = "prefix<｜begin▁of▁thinking｜>internal reasoning<｜end▁of▁thinking｜>answer"
    assert strip_inline_reasoning(src) == "prefixanswer"


def test_strip_orphan_unterminated_thinking_incident():
    # strip-alone contract (design D2): orphan THINKING delimiter + CoT
    # removed, real prefix kept, DSML block intentionally preserved for
    # the extractor stage (end-to-end no-markup guarantee is asserted in
    # test_sanitize_response_recovers_dsml_when_no_structured).
    out = strip_inline_reasoning(INCIDENT)
    assert "▁of▁thinking｜>" not in out  # no thinking delimiter token survives
    assert "API 测试通过" not in out  # CoT removed
    assert out.startswith("# Inline thinking-tag filtering")  # real prefix kept
    assert "<｜｜DSML｜｜tool_calls>" in out  # DSML handed to extractor next


def test_strip_think_tags_defensive():
    assert strip_inline_reasoning("<think>plan</think>final") == "final"


def test_clean_content_byte_identical():
    clean = 'def f():\n    return {"ok": True}\n'
    assert strip_inline_reasoning(clean) is clean or strip_inline_reasoning(clean) == clean
    assert strip_inline_reasoning(clean) == clean


def test_extract_dsml_todo_write():
    clean, calls = extract_dsml_tool_calls(INCIDENT)
    assert "<｜｜DSML｜｜" not in clean
    assert len(calls) == 1
    assert calls[0]["name"] == "todo_write"
    assert calls[0]["arguments"]["items"] == [
        {"activeForm": "验证", "content": "验证后端", "status": "completed"}
    ]


def test_dsml_malformed_payload_no_raise_no_leak(caplog):
    bad = (
        '<｜｜DSML｜｜tool_calls>'
        '<｜｜DSML｜｜invoke name="todo_write">'
        '<｜｜DSML｜｜parameter name="items" string="false">{not json,,'
    )
    clean, calls = extract_dsml_tool_calls(bad)
    assert "<｜｜DSML｜｜" not in clean  # markup still stripped
    assert calls and calls[0]["name"] == "todo_write"
    assert calls[0]["arguments"]["items"] == "{not json,,"  # raw fallback


def test_sanitize_response_structured_wins_no_double():
    structured = [{"id": "1", "name": "read_file", "arguments": {"path": "x"}}]
    content, tcs, extracted = sanitize_response(INCIDENT, structured, enabled=True)
    assert tcs is structured  # structured untouched, not duplicated
    assert extracted is False
    assert "<｜｜DSML｜｜" not in content  # markup still stripped from content


def test_sanitize_response_recovers_dsml_when_no_structured():
    content, tcs, extracted = sanitize_response(INCIDENT, [], enabled=True)
    assert extracted is True
    assert tcs[0]["name"] == "todo_write"
    assert "<｜" not in content


def test_flag_disabled_identity():
    content, tcs, extracted = sanitize_response(INCIDENT, [], enabled=False)
    assert content == INCIDENT  # byte-identical passthrough
    assert tcs == []
    assert extracted is False


def test_clean_response_unchanged_when_no_markup():
    src = "Just a normal answer with no markup."
    content, tcs, extracted = sanitize_response(src, [], enabled=True)
    assert content == src
    assert tcs == []
    assert extracted is False
