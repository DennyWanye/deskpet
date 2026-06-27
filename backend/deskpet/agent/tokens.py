# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""统一 token 计数 —— 全后端唯一入口,消除散落的 ``len // 4`` 不一致(优化 #1+#3)。

设计取舍(为何不默认上 tiktoken):
  * tiktoken 要 ~30MB BPE 模型文件,且**首次使用从 openaipublic 下载**(中国大陆常被墙),
    冻结正式包还要额外打包 —— 对"单机桌宠 + 中国用户 + 瘦包"是负担。
  * 主力是中转站 gpt-5.x(OpenAI 兼容),但也可能接 deepseek/anthropic,tiktoken 的 BPE
    只精确匹配 OpenAI,跨 provider 本就不准。
  * 现有 CJK-aware 启发式(``token_budget._weighted_chars``)已**真机校准 + 安全偏上**
    (CJK×4≈1token/字,ASCII×8/7≈3.5char/token,宁可早压不可爆窗,FP-2 TC-2.1 实测),
    配合 relay 真实 ``usage.input_tokens`` 三刀制,实战足够。

故:**启发式做可靠基线(零依赖/离线/确定性/防爆窗)**,tiktoken 作**可选精度增强**——
仅当显式 ``DESKPET_TIKTOKEN=1`` 且 tiktoken 装好 + BPE 可加载时启用,任何失败静默回落启发式。
"""
from __future__ import annotations

import os
import re
from typing import Any

# CJK 字符(汉字/假名/全角符号)在主流 BPE 里 ≈1 token/字,而裸 char/4 会低估 ~4 倍。
_CJK_RE = re.compile(r"[　-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")

_PER_MSG_OVERHEAD = 4   # role tag + wire 分隔符的每条消息开销

# ── 可选 tiktoken(默认关) ────────────────────────────────────────────
_TIKTOKEN_ENC: Any = None
_TIKTOKEN_TRIED = False


def _tiktoken_encoder() -> Any:
    """仅当 DESKPET_TIKTOKEN=1 且可加载时返回 o200k_base encoder,否则 None(回落启发式)。"""
    global _TIKTOKEN_ENC, _TIKTOKEN_TRIED
    if _TIKTOKEN_TRIED:
        return _TIKTOKEN_ENC
    _TIKTOKEN_TRIED = True
    if os.environ.get("DESKPET_TIKTOKEN", "").strip() not in ("1", "true", "True"):
        _TIKTOKEN_ENC = None
        return None
    try:
        import tiktoken  # type: ignore
        _TIKTOKEN_ENC = tiktoken.get_encoding("o200k_base")  # gpt-4o/5 系
    except Exception:  # noqa: BLE001 — 没装/BPE 下不动(中国被墙)→ 回落启发式
        _TIKTOKEN_ENC = None
    return _TIKTOKEN_ENC


def _weighted_chars(s: str) -> int:
    """ASCII 等效字符数: CJK 字 ×4(≈1token/字),ASCII ×8/7(≈3.5char/token,安全偏上)。

    真机校准(FP-2 TC-2.1): markdown/路径/代码密集英文实测 ~3char/token,纯散文才近 4;
    ASCII 上调使整体宁可早压不可爆窗。
    """
    cjk = len(_CJK_RE.findall(s))
    ascii_part = len(s) - cjk
    return ascii_part + ascii_part // 7 + cjk * 4


def count_text_tokens(text: str) -> int:
    """一段文本的 token 数。tiktoken 可用则精确,否则 CJK-aware 启发式。"""
    if not text:
        return 0
    enc = _tiktoken_encoder()
    if enc is not None:
        try:
            return max(1, len(enc.encode(text)))
        except Exception:  # noqa: BLE001
            pass
    return max(1, _weighted_chars(text) // 4)


def _msg_text_chars(m: dict[str, Any]) -> int:
    """单条消息的 ASCII 等效字符数(content + tool_calls 载荷),供启发式路径用。"""
    chars = 0
    content = m.get("content")
    if isinstance(content, str):
        chars += _weighted_chars(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                chars += _weighted_chars(str(part.get("text", "")))
            elif isinstance(part, str):
                chars += _weighted_chars(part)
    elif content is not None:
        chars += _weighted_chars(str(content))
    for tc in m.get("tool_calls") or []:
        if isinstance(tc, dict):
            args = tc.get("args") or tc.get("arguments")
            if args is not None:
                chars += _weighted_chars(args if isinstance(args, str) else str(args))
            chars += len(str(tc.get("name") or tc.get("function", {}).get("name", "")
                            if isinstance(tc.get("function"), dict) else tc.get("name") or ""))
    return chars


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """一组消息的 token 数(content + tool_calls + 每条 overhead),全后端统一口径。"""
    if not messages:
        return 0
    enc = _tiktoken_encoder()
    if enc is not None:
        total = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                total += count_text_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += count_text_tokens(str(part.get("text", "")))
                    elif isinstance(part, str):
                        total += count_text_tokens(part)
            elif content is not None:
                total += count_text_tokens(str(content))
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict):
                    args = tc.get("args") or tc.get("arguments")
                    if args is not None:
                        total += count_text_tokens(args if isinstance(args, str) else str(args))
            total += _PER_MSG_OVERHEAD
        return total
    # 启发式路径
    chars = sum(_msg_text_chars(m) for m in messages)
    return max(0, chars // 4 + _PER_MSG_OVERHEAD * len(messages))


__all__ = ["count_text_tokens", "count_messages_tokens"]
