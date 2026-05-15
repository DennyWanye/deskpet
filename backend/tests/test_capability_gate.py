"""Phase 2 — capability gate 单测 (OpenSpec 2026-05-16-companion-context-isolation §D2)。

回归背景：用户在 `default` 陪伴 session 说"你能帮我生成一个海报图片嘛？"，
deskpet 没有图像生成工具，LLM 没有合法动作可做 → 漂移到 8 天前的 VPN 旧项目，
建了 17 个文件。capability_gate 在进 agent loop 前拦下"明显无对应工具"的请求，
直接 graceful refuse + 替代建议（不是沙箱、不是弹窗，是诚实答复）。

acceptance scenarios 来自 specs/capability-gate/spec.md。
"""
from __future__ import annotations

import asyncio

import pytest

from agent.capability_gate import (
    GateVerdict,
    Verdict,
    classify_request,
)


# ---------------------------------------------------------------------------
# Scenario: Image-generation request with no image tool is refused
# ---------------------------------------------------------------------------
def test_image_request_no_image_tool_refused() -> None:
    """GIVEN ToolRegistry 无图像工具 AND 用户说"帮我生成一个海报图片"
    WHEN 进 capability gate
    THEN REFUSE，reason 诚实（含"图像"），带 alternative。
    """
    available = ["read_file", "write_file", "edit_file", "run_shell", "list_directory"]
    verdict = asyncio.run(
        classify_request("你能帮我生成一个海报图片嘛？", available_tools=available)
    )
    assert verdict.verdict is Verdict.REFUSE
    assert verdict.reason
    assert "图" in verdict.reason  # 诚实说"我没有图像生成能力"
    assert verdict.alternative  # 必须给替代建议
    assert verdict.alternative.strip()


@pytest.mark.parametrize(
    "text",
    [
        "帮我画一张猫的插画",
        "给我做个视频片头",
        "生成一段配音",
        "帮我作一首钢琴曲",
        "做一个 3D 模型",
        "generate an image of a sunset",
        "帮我P一下这张图",
    ],
)
def test_various_unfulfillable_generation_requests_refused(text: str) -> None:
    """图像/视频/语音/作曲/3D 等无对应工具的生成请求都 REFUSE。"""
    available = ["read_file", "write_file", "run_shell"]
    verdict = asyncio.run(classify_request(text, available_tools=available))
    assert verdict.verdict is Verdict.REFUSE, f"{text!r} should REFUSE"
    assert verdict.alternative


# ---------------------------------------------------------------------------
# Scenario: Normal code request passes the gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "重构 server/db.js 的连接池",
        "修复登录页的 bug",
        "你好呀今天过得怎么样",
        "还记得我们上次聊的旅行计划吗",
        "帮我写个快速排序",
        "解释一下什么是闭包",
    ],
)
def test_normal_requests_pass(text: str) -> None:
    """正常 code / chat / recall 请求 → PASS，正常进 loop。"""
    available = ["read_file", "write_file", "edit_file", "run_shell"]
    verdict = asyncio.run(classify_request(text, available_tools=available))
    assert verdict.verdict is Verdict.PASS, f"{text!r} should PASS"


# ---------------------------------------------------------------------------
# Scenario: Gate auto-adapts when a capability is added
# ---------------------------------------------------------------------------
def test_gate_auto_passes_when_image_tool_registered() -> None:
    """注册了图像生成工具后，图像请求自动 PASS（读 live 工具，不写死黑名单）。"""
    available = [
        "read_file",
        "write_file",
        "generate_image",  # 新增的图像工具
    ]
    verdict = asyncio.run(
        classify_request("帮我生成一个海报图片", available_tools=available)
    )
    assert verdict.verdict is Verdict.PASS


def test_gate_auto_passes_when_video_tool_registered() -> None:
    """视频工具注册后视频请求自动 PASS。"""
    available = ["read_file", "mcp_media_generate_video"]
    verdict = asyncio.run(
        classify_request("帮我做个视频片头", available_tools=available)
    )
    assert verdict.verdict is Verdict.PASS


# ---------------------------------------------------------------------------
# Scenario: Gate disabled restores legacy behavior (Strangler-Fig)
# ---------------------------------------------------------------------------
def test_gate_disabled_always_passes() -> None:
    """capability_gate_enabled=false → 任何请求都 PASS。"""
    available = ["read_file", "write_file"]
    verdict = asyncio.run(
        classify_request(
            "你能帮我生成一个海报图片嘛？",
            available_tools=available,
            enabled=False,
        )
    )
    assert verdict.verdict is Verdict.PASS


# ---------------------------------------------------------------------------
# GateVerdict 数据结构 sanity
# ---------------------------------------------------------------------------
def test_gateverdict_pass_helper() -> None:
    v = GateVerdict.passed()
    assert v.verdict is Verdict.PASS
    assert v.reason == ""
    assert v.alternative == ""


def test_gateverdict_refuse_helper() -> None:
    v = GateVerdict.refuse("我没有图像生成能力", "你可以用外部工具")
    assert v.verdict is Verdict.REFUSE
    assert "图像" in v.reason
    assert v.alternative == "你可以用外部工具"


def test_gateverdict_render_text_for_refuse() -> None:
    """REFUSE 文案应是直接答复（含 reason + alternative），不是弹窗确认。"""
    v = GateVerdict.refuse("我没有图像生成能力", "如果要我帮你写生成图片的代码，请进 code 模式")
    rendered = v.render_text()
    assert "我没有图像生成能力" in rendered
    assert "code 模式" in rendered


# ---------------------------------------------------------------------------
# 歧义请求走 LLM 兜底（注入一个 stub classifier）
# ---------------------------------------------------------------------------
def test_ambiguous_request_uses_llm_fallback_refuse() -> None:
    """规则不确定的请求走 haiku-class 兜底；兜底判 REFUSE 则 REFUSE。"""

    class _StubLLM:
        async def chat_with_fallback(self, messages, **kwargs):  # noqa: ANN001
            class _R:
                content = "REFUSE"

            return _R()

    available = ["read_file", "write_file"]
    # 一个不含明显关键词、规则无法判定的请求
    verdict = asyncio.run(
        classify_request(
            "帮我搞个那种动起来的东西",
            available_tools=available,
            llm_registry=_StubLLM(),
        )
    )
    assert verdict.verdict is Verdict.REFUSE


def test_ambiguous_request_llm_fallback_pass_defaults_safe() -> None:
    """兜底判 PASS（或 LLM 不可用）→ PASS（不冤枉正常请求）。"""
    available = ["read_file", "write_file"]
    verdict = asyncio.run(
        classify_request(
            "帮我搞个那种动起来的东西",
            available_tools=available,
            llm_registry=None,  # 无 LLM → 默认放行
        )
    )
    assert verdict.verdict is Verdict.PASS
