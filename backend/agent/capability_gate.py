# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Capability gate — agent loop 前置能力门 (OpenSpec §D2)。

**为什么存在**：deskpet 无图像/视频/语音/3D 生成工具。用户在 `default`
陪伴 session 说"帮我生成一个海报图片"时，LLM 拿到一个它没有合法动作可
完成的请求 → 漂移到记忆里最"可执行"的旧项目（2026-05-16 实测 bug：建了
17 个 VPN CLI 文件）。

**这不是沙箱**：不拦命令、不弹窗、不写死黑名单。它只回答一个问题——
"这个请求是否明显需要 deskpet 没有的能力？"是 → 进 loop 前就诚实告诉
用户做不到 + 给替代建议（符合 feedback_no_sandbox_constraints：不加确认
弹窗，只防漂移）。

**rule-first**：关键词 + 意图模式命中"生成图片/海报/视频/语音/作曲/3D"
等且 ToolRegistry 无对应工具 → REFUSE。规则不确定才走一次 haiku-class
LLM 兜底（复用 ContextAssembler classifier 的 chat_with_fallback 基础设施）。

**live tool 读取**：能力是否"具备"完全由 `available_tools` 决定（调用方
传 `ToolRegistry.list_tools()`）。后续注册图像工具后，本门自动放行——
绝不写死黑名单。

**Strangler-Fig**：`enabled=False`（来自 `[companion].capability_gate_enabled`）
→ 永远 PASS，退回旧"啥都进 loop"行为。
"""
from __future__ import annotations

import asyncio
import enum
import re
from dataclasses import dataclass
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class Verdict(enum.Enum):
    """能力门裁决。"""

    PASS = "pass"
    REFUSE = "refuse"


@dataclass
class GateVerdict:
    """`classify_request` 的结果。

    REFUSE 时 ``reason`` 诚实说明做不到，``alternative`` 给出替代路径。
    PASS 时两者为空。
    """

    verdict: Verdict
    reason: str = ""
    alternative: str = ""

    @classmethod
    def passed(cls) -> "GateVerdict":
        return cls(verdict=Verdict.PASS)

    @classmethod
    def refuse(cls, reason: str, alternative: str) -> "GateVerdict":
        return cls(verdict=Verdict.REFUSE, reason=reason, alternative=alternative)

    def render_text(self) -> str:
        """渲染成直接答复给用户的文案（不是弹窗确认 — 符合 no-sandbox）。"""
        parts = [self.reason.strip()]
        if self.alternative.strip():
            parts.append(self.alternative.strip())
        return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Capability 分类规则
# ---------------------------------------------------------------------------
# 每个 capability：触发该能力的意图正则 + 哪些工具名子串代表"具备"该能力。
# `tool_markers` 命中任一 → 视为已具备该能力 → 该 capability 不再 REFUSE。
# 关键：tool_markers 是"具备"信号，不是黑名单——新增带这些子串的工具会
# 自动让对应请求 PASS。
@dataclass(frozen=True)
class _Capability:
    key: str
    label_zh: str
    intent: re.Pattern[str]
    tool_markers: tuple[str, ...]
    alternative: str


# 意图正则尽量收紧：必须是"生成/制作类动词" + "媒体名词"，避免误伤
# "帮我读取这张图片的路径" / "解释一下这段音频代码" 这类正常请求。
_GEN_VERB = r"(生成|制作|做(?:一|个|张|段|首)?|搞(?:一|个)?|弄(?:一|个)?|画(?:一|张|个)?|创作|作(?:一首|曲)|设计(?:一张)?|P(?:一下)?|渲染|合成|配)"
_GEN_VERB_EN = r"(generate|create|make|draw|render|compose|design|paint)"

_CAPABILITIES: tuple[_Capability, ...] = (
    _Capability(
        key="image",
        label_zh="图像生成",
        intent=re.compile(
            rf"({_GEN_VERB}.{{0,6}}(图片|图像|海报|插画|图标|logo|头像|壁纸|封面|照片))"
            rf"|({_GEN_VERB_EN}\s+(an?\s+)?(image|picture|poster|illustration|icon|logo|avatar|wallpaper))"
            r"|(画一(张|个|幅))"
            r"|(P(图|一下(这|那)?(张)?图))",
            re.IGNORECASE,
        ),
        tool_markers=(
            "generate_image",
            "image_gen",
            "text_to_image",
            "create_image",
            "draw_image",
            "dalle",
            "stable_diffusion",
            "sdxl",
            "midjourney",
            "flux",
            "imagegen",
        ),
        alternative=(
            "我没有图像生成能力。你可以用外部工具（比如即梦 / Midjourney / "
            "DALL·E）来生成图片；如果你想让我帮你写一段调用图像生成 API 的"
            "代码，请进 code 模式并选择项目。"
        ),
    ),
    _Capability(
        key="video",
        label_zh="视频生成",
        intent=re.compile(
            rf"({_GEN_VERB}.{{0,6}}(视频|短片|动画|片头|片尾|gif|动图|MV))"
            rf"|({_GEN_VERB_EN}\s+(an?\s+)?(video|animation|movie|clip|gif))"
            r"|(做(一)?个(那种)?动起来的)",
            re.IGNORECASE,
        ),
        tool_markers=(
            "generate_video",
            "video_gen",
            "text_to_video",
            "create_video",
            "sora",
            "runway",
            "kling",
            "videogen",
        ),
        alternative=(
            "我没有视频生成能力。可以试试可灵 / Runway / Sora 这类外部工具；"
            "如需我帮你写调用视频生成 API 的代码，请进 code 模式。"
        ),
    ),
    _Capability(
        key="audio",
        label_zh="语音/音频生成",
        intent=re.compile(
            rf"({_GEN_VERB}.{{0,6}}(配音|语音|音频|声音|旁白|有声))"
            rf"|({_GEN_VERB_EN}\s+(an?\s+)?(voice|audio|speech|narration|sound))"
            r"|(把.{0,8}转(成|为)?(语音|音频|声音))",
            re.IGNORECASE,
        ),
        tool_markers=(
            "generate_audio",
            "text_to_speech",
            "tts_generate",
            "voice_gen",
            "audio_gen",
            "elevenlabs",
            "synthesize_speech",
        ),
        alternative=(
            "我没有语音/音频合成能力。可以用 ElevenLabs / 火山引擎 TTS 等"
            "外部服务；如需代码集成请进 code 模式。"
        ),
    ),
    _Capability(
        key="music",
        label_zh="作曲/音乐生成",
        intent=re.compile(
            rf"({_GEN_VERB}.{{0,4}}(歌|曲|音乐|配乐|钢琴曲|旋律|BGM))"
            rf"|(作(一)?首)"
            rf"|({_GEN_VERB_EN}\s+(a\s+)?(song|music|melody|soundtrack))",
            re.IGNORECASE,
        ),
        tool_markers=(
            "generate_music",
            "compose_music",
            "music_gen",
            "suno",
            "udio",
        ),
        alternative=(
            "我没有作曲/音乐生成能力。可以试试 Suno / Udio；如需我帮你写"
            "调用音乐生成 API 的代码，请进 code 模式。"
        ),
    ),
    _Capability(
        key="model3d",
        label_zh="3D 模型生成",
        intent=re.compile(
            rf"({_GEN_VERB}.{{0,4}}(3D|三维)?.{{0,4}}(模型|建模|手办))"
            rf"|({_GEN_VERB_EN}\s+(a\s+)?(3d\s+model|mesh))"
            r"|(做(一)?个\s*3D)",
            re.IGNORECASE,
        ),
        tool_markers=(
            "generate_3d",
            "model3d",
            "text_to_3d",
            "mesh_gen",
            "tripo",
            "hunyuan3d",
            "rodin",
        ),
        alternative=(
            "我没有 3D 模型生成能力。可以试试 Tripo / Rodin / Hunyuan3D；"
            "如需代码集成请进 code 模式。"
        ),
    ),
)


def _has_tool_for(cap: _Capability, available_tools: set[str]) -> bool:
    """是否有任意工具名包含该 capability 的 marker 子串。

    live 读取——这是"具备能力"的唯一判据，绝不写死黑名单。
    """
    low = {t.lower() for t in available_tools}
    for marker in cap.tool_markers:
        for tname in low:
            if marker in tname:
                return True
    return False


def _rule_match(text: str, available_tools: set[str]) -> Optional[_Capability]:
    """规则层：返回命中的"无对应工具"capability，或 None。"""
    if not text:
        return None
    for cap in _CAPABILITIES:
        if cap.intent.search(text):
            if not _has_tool_for(cap, available_tools):
                return cap
            # 有对应工具 → 这个 capability 不拦，继续看别的（一般直接 PASS）
            return None
    return None


# 兜底 LLM 提示：只回 PASS 或 REFUSE。语义——这个请求是否需要 deskpet
# 没有的"生成类多媒体"能力（图像/视频/语音/作曲/3D）。
_LLM_SYSTEM = (
    "你判断一句用户请求是否明显需要『生成类多媒体能力』"
    "（生成图片/视频/语音/音乐/3D 模型）。\n"
    "deskpet 是一个桌宠助手，可以聊天、写代码、跑命令、读写文件、搜索，"
    "但【没有】任何图像/视频/语音/音乐/3D 生成能力。\n"
    "规则：\n"
    "- 用户想让 deskpet 直接产出一张图/一段视频/一段配音/一首曲子/一个 3D"
    "模型 → 回 REFUSE\n"
    "- 其它（聊天、写代码、解释、查资料、读写文本文件、情感陪伴）→ 回 PASS\n"
    "只回一个词：PASS 或 REFUSE，不要别的。"
)

_GENERIC_ALT = (
    "如果你是想让我帮你写调用这类生成服务的代码，请进 code 模式并选择项目；"
    "或者直接用对应的外部工具。"
)


async def _llm_fallback(
    text: str,
    llm_registry: Any,
    timeout_s: float = 2.0,
) -> Optional[Verdict]:
    """歧义请求走一次 haiku-class 兜底。失败/超时 → None（默认放行）。"""
    if llm_registry is None:
        return None
    messages = [
        {"role": "system", "content": _LLM_SYSTEM},
        {"role": "user", "content": text},
    ]
    try:
        resp = await asyncio.wait_for(
            llm_registry.chat_with_fallback(
                messages,
                model="claude-haiku-4-5",
                max_tokens=8,
                temperature=0.0,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning("capability_gate.llm_timeout", timeout_s=timeout_s)
        return None
    except Exception as exc:  # noqa: BLE001 — 兜底永远不阻断主流程
        logger.warning(
            "capability_gate.llm_failed",
            error=str(exc)[:200],
            error_type=type(exc).__name__,
        )
        return None
    content = str(getattr(resp, "content", "") or "").strip().upper()
    if "REFUSE" in content:
        return Verdict.REFUSE
    if "PASS" in content:
        return Verdict.PASS
    return None


# 规则"可能相关但不确定"的弱信号：含生成动词 + 模糊媒体词，但没命中
# 精确 intent。用来决定是否值得花一次 LLM 兜底。
_AMBIGUOUS = re.compile(
    rf"({_GEN_VERB}|{_GEN_VERB_EN})", re.IGNORECASE
)


async def classify_request(
    user_text: str,
    *,
    available_tools: Any,
    enabled: bool = True,
    llm_registry: Any = None,
) -> GateVerdict:
    """请求进 agent loop 前的能力门。

    Parameters
    ----------
    user_text:
        用户原话。
    available_tools:
        实时工具名集合（调用方传 ``ToolRegistry.list_tools()``）。
        capability 是否"具备"完全由此决定——新增工具自动放行。
    enabled:
        ``[companion].capability_gate_enabled``。False → 永远 PASS
        （Strangler-Fig 回退）。
    llm_registry:
        歧义兜底用的 LLM（带 ``async chat_with_fallback``）。None →
        跳过兜底，默认 PASS（不冤枉正常请求）。
    """
    if not enabled:
        return GateVerdict.passed()

    tools_set: set[str] = set(available_tools or [])

    # 1) 规则层：精确命中"无对应工具的生成类请求" → 直接 REFUSE
    cap = _rule_match(user_text or "", tools_set)
    if cap is not None:
        reason = f"我没有{cap.label_zh}能力。"
        logger.info(
            "capability_gate.refuse",
            capability=cap.key,
            text=(user_text or "")[:120],
        )
        return GateVerdict.refuse(reason, cap.alternative)

    # 2) 歧义：含生成动词但没命中精确 intent → 一次 haiku 兜底
    if llm_registry is not None and _AMBIGUOUS.search(user_text or ""):
        v = await _llm_fallback(user_text or "", llm_registry)
        if v is Verdict.REFUSE:
            logger.info(
                "capability_gate.refuse_llm",
                text=(user_text or "")[:120],
            )
            return GateVerdict.refuse(
                "我可能没有完成这个请求所需的生成能力（图像/视频/语音/音乐/3D）。",
                _GENERIC_ALT,
            )

    # 3) 默认放行
    return GateVerdict.passed()
