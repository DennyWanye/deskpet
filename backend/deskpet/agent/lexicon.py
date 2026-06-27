# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""共享词法地板模块（BUG-B Phase 1 WI-1）。

高精度、确定性、零 LLM 的整句锚定寒暄判定 + 轻量 code 信号探测。
**原则 5**：快路径用高精度 allowlist（整句锚定，禁止子串），不是 block-list；
假阴性（漏放行 → 多走一次 LLM）安全，假阳性（误放行真问题 → 误短路）危险。
**原则 4**：便宜确定性层只做地板不做天花板 —— 不命中时不能无脑 fail-open 成 chat，
由调用方倒向能力侧（见 classifier WI-6）。

两个意图判定器（intent_triage allowlist 快路径 + classifier 词法地板）**复用同一模块**，
避免双词表漂移（plan §1 原则 3 / R2-B4）。

规格见 plan §2.2（整句锚定 + 否决优先）：
  - 纯 emoji / 纯标点（无字母数字汉字）→ True
  - 否决项任一命中 → False（否决优先于锚定）：
      问号 [?？] | 祈使/求助词 | 故障/code 词
    （R2 决策：否决用问号 `[?？]` 而非裸疑问助词 `吗么`，否则误杀整句寒暄"在吗/在不"。）
  - 整句锚定 re.fullmatch( (招呼词 + 尾缀)+ ) → True；否则 False
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["is_obvious_chitchat", "has_code_signal"]


# ──────────────────────────────────────────────────────────────────────────
# 招呼词根（整句锚定用；长词优先以保证 "你好呀" 不被 "你好" 抢先吃掉尾部）。
# R2 决策点：保留 "在吗"/"在不"（含疑问助词 吗），靠"否决不再用裸 吗么"放行。
# ──────────────────────────────────────────────────────────────────────────
_GREET_ROOT = (
    r"你好呀|你好|您好|嗨|哈喽|哈啰|hello|hi|hey|"
    r"早安|午安|晚安|晚上好|早上好|中午好|下午好|早|"
    r"在吗|在不|在的|在呀|在哦|在|"
    r"谢谢|多谢|感谢|thx|thanks|thank you|ty|"
    r"拜拜|再见|bye|goodbye|"
    r"哈哈哈|哈哈|嘿嘿|呵呵|嗯嗯|嗯|哦哦|哦|嘻嘻|haha|lol|"
    r"好的|好哒|ok|okay"
)
# 容许尾缀（语气词 + 标点 + 空白），可在每个招呼词后重复。
_GREET_SUFFIX = r"[呀啊哟哦呢嘛吧哈~！!。.，,、\s]*"

# 整句锚定：招呼词 + 尾缀 可整体重复（"在吗在吗" / "你好你好" / "hi 你好"）。
_GREET_ANCHOR = re.compile(
    rf"^(?:(?:{_GREET_ROOT}){_GREET_SUFFIX})+$",
    re.IGNORECASE,
)

# ──────────────────────────────────────────────────────────────────────────
# 否决正则（命中任意 → 一定不是纯寒暄，否决优先于锚定）。
# ──────────────────────────────────────────────────────────────────────────
_VETO_QUESTION_MARK = re.compile(r"[?？]")

# 祈使 / 求助 / 疑问词（plan §2.2 列表）。
_VETO_IMPERATIVE = re.compile(
    r"(帮我|帮忙|帮你|帮|请|给我|告诉我|教我|"
    r"看下|看一下|看看|查一下|查查|查|搜一下|搜搜|搜|"
    r"写个|写一下|写|改一下|改个|改|修一下|修个|修|生成|做个|做一下|做|算|"
    r"解释|说明|分析|总结|"
    r"为什么|为啥|怎么|咋|如何|啥|什么是|是什么|有没有|能不能|可不可以|"
    r"explain|how|why|what|please|help)",
    re.IGNORECASE,
)

# 故障 / code 词（plan §2.2 列表 + 常见编程语言/符号）。
_VETO_CODE_FAULT = re.compile(
    r"(报错|debug|bug|error|异常|崩|卡死|失败|不行|死循环|超时|"
    r"代码|脚本|函数|变量|编译|栈|stack\s*trace|traceback|"
    r"python|java|javascript|typescript|golang|rust|c\+\+|"
    r"报告|越界|index|null|none\b)",
    re.IGNORECASE,
)

# 轻量 code 信号（classifier WI-6 倒向能力侧用：命中 → code，否则 → task）。
# 与 _VETO_CODE_FAULT 范围一致但语义不同：这里是"该当 code 处理"，不是"否决寒暄"。
_CODE_SIGNAL = re.compile(
    r"(报错|debug|bug|error|异常|崩|卡死|死循环|栈|stack\s*trace|traceback|"
    r"代码|脚本|函数|变量|类型|编译|报\s*\w*error|越界|"
    r"python|java|javascript|typescript|golang|rust|c\+\+|sql|"
    r"def |class |import |return |[(){}\[\];]|->|=>|::|<\w+>)",
    re.IGNORECASE,
)


def _is_only_emoji_or_punct(text: str) -> bool:
    """True 当且仅当文本全由 emoji / 标点 / 符号 / 空白组成（无字母数字汉字实意）。"""
    seen = False
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("Z"):  # 空白
            continue
        if cat.startswith("P"):  # 标点
            seen = True
            continue
        if cat in ("So", "Sm", "Sk", "Sc"):  # 符号 / emoji
            seen = True
            continue
        cp = ord(ch)
        if (0x1F300 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF) or (0xFE00 <= cp <= 0xFE0F):
            seen = True
            continue
        return False  # 命中字母 / 数字 / 汉字等实意字符
    return seen


def is_obvious_chitchat(msg: str) -> bool:
    """高精度寒暄 allowlist：True = 确定纯寒暄可短路；False = 不确定，交给上层（LLM / 能力侧）。

    判定顺序（plan §2.2，否决优先）：
      1. 空串 → False
      2. 否决项任一命中 → False
      3. 纯 emoji / 纯标点 → True
      4. 整句锚定（招呼词+尾缀 可重复）→ True；否则 False
    """
    if msg is None:
        return False
    s = msg.strip()
    if not s:
        return False

    # 否决优先（真问句 / 求助 / 故障一律不放行）。
    if _VETO_QUESTION_MARK.search(s):
        return False
    if _VETO_IMPERATIVE.search(s):
        return False
    if _VETO_CODE_FAULT.search(s):
        return False

    # 纯 emoji / 纯标点放行。
    if _is_only_emoji_or_punct(s):
        return True

    # 整句锚定放行。
    return bool(_GREET_ANCHOR.match(s))


def has_code_signal(msg: str) -> bool:
    """轻量 code 信号探测（classifier WI-6 词法地板倒向能力侧用）。"""
    if not msg:
        return False
    return bool(_CODE_SIGNAL.search(msg))
