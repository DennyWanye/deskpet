# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from deskpet.tools import ppt_tools
from deskpet.tools.ppt_tools import SlideOutline


def _report(md: str = "市场规模 120 亿美元，年增速 18%。[^1]") -> SimpleNamespace:
    return SimpleNamespace(report_md=md)


@pytest.mark.asyncio
async def test_ppt_pro_content_drafts_outline_from_report_with_bullets_and_image_prompt():
    async def llm_call(prompt: str) -> str:
        assert "市场规模 120 亿美元" in prompt
        return json.dumps([
            {
                "layout": "image_full",
                "title": "行业机会",
                "bullets": ["规模达到 120 亿美元", "年增速保持 18%", "引用报告数据做判断"],
                "image_prompt": "cinematic dashboard, negative space, no text, no watermark",
            }
        ], ensure_ascii=False)

    slides = await ppt_tools._draft_outline_from_research(
        "AI 教育市场",
        _report(),
        pages=1,
        theme="minimal",
        image_mode=True,
        llm_call=llm_call,
    )

    assert len(slides) == 1
    assert slides[0].image_prompt
    assert len(slides[0].bullets) >= 3


@pytest.mark.asyncio
async def test_ppt_pro_content_report_none_still_drafts_outline():
    async def llm_call(prompt: str) -> str:
        assert "调研报告为空" in prompt or "无调研报告" in prompt
        return json.dumps([
            {
                "layout": "bullet",
                "title": "可行起点",
                "bullets": ["明确问题", "列出假设", "标注待核验"],
                "image_prompt": "cinematic desk scene, negative space, no text, no watermark",
            }
        ], ensure_ascii=False)

    slides = await ppt_tools._draft_outline_from_research(
        "本地优先 AI 应用",
        None,
        pages=1,
        theme="minimal",
        image_mode=False,
        llm_call=llm_call,
    )

    assert slides[0].title == "可行起点"
    assert slides[0].bullets


@pytest.mark.asyncio
async def test_ppt_pro_content_feedback_and_prev_slides_make_incremental_prompt():
    captured: dict[str, str] = {}

    async def llm_call(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps([
            {
                "layout": "bullet",
                "title": "保留页",
                "bullets": ["旧要点", "新增风险", "不改无关页"],
                "image_prompt": "cinematic office wall, negative space, no text, no watermark",
            }
        ], ensure_ascii=False)

    prev = [SlideOutline(title="上一版标题", bullets=["上一版要点 A", "上一版要点 B"])]
    await ppt_tools._draft_outline_from_research(
        "供应链韧性",
        _report("引用报告：交付周期缩短 22%。"),
        pages=1,
        theme="minimal",
        image_mode=False,
        llm_call=llm_call,
        feedback="第二页增加风险",
        prev_slides=prev,
    )

    prompt = captured["prompt"]
    assert "第二页增加风险" in prompt
    assert "上一版标题" in prompt
    assert "上一版要点 A" in prompt
    assert "只改" in prompt or "增量" in prompt


@pytest.mark.asyncio
async def test_ppt_pro_content_garbage_llm_output_falls_back():
    async def llm_call(prompt: str) -> str:
        return "not json at all"

    slides = await ppt_tools._draft_outline_from_research(
        "隐私计算",
        _report(),
        pages=3,
        theme="minimal",
        image_mode=True,
        llm_call=llm_call,
    )

    assert len(slides) == 3
    assert slides[0].layout == "title"
    assert all(s.title for s in slides)


def test_ppt_pro_content_outline_to_markdown_multiple_pages():
    md = ppt_tools._outline_to_markdown([
        SlideOutline(title="封面", subtitle="副标题"),
        SlideOutline(title="洞察", bullets=["第一条", "第二条"]),
    ])

    assert "第 1 页：封面" in md
    assert "副标题" in md
    assert "第 2 页：洞察" in md
    assert "  - 第一条" in md
    assert "  - 第二条" in md


@pytest.mark.asyncio
async def test_ppt_pro_content_research_timeout_returns_none(monkeypatch):
    from deskpet.tools import research_tools

    async def fake_llm():
        async def call(prompt: str) -> str:
            return ""
        return call

    async def slow_deepresearch(*args, **kwargs):
        await asyncio.sleep(0.2)
        return _report()

    monkeypatch.setattr(research_tools, "_resolve_default_llm_call", fake_llm)
    monkeypatch.setattr(research_tools, "deepresearch", slow_deepresearch)

    result = await ppt_tools._research_topic_for_ppt("新能源", depth="deep", timeout_s=0.01)

    assert result is None


@pytest.mark.asyncio
async def test_ppt_pro_content_research_config_error_raises(monkeypatch):
    from deskpet.tools import research_tools

    async def bad_llm():
        raise RuntimeError("no llm provider configured")

    monkeypatch.setattr(research_tools, "_resolve_default_llm_call", bad_llm)

    with pytest.raises(RuntimeError, match="provider configured"):
        await ppt_tools._research_topic_for_ppt("新能源", depth="deep", timeout_s=1.0)
