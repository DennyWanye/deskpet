# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F5 修复（2026-06-01）：检索 query 分词，供 facts.search / workspace.recall
的 LIKE 层用。

## 为什么需要
旧实现 `LIKE '%{整个 query}%'` 要求字段包含**整个 query 串**。agent 传自然
语言 query（"刚读的文档"、"test-research-helper README.md file access read
touch"），字段（如 path）不可能包含整串 → 几乎永远 0 命中（缺陷 F5）。

分词后逐词 OR LIKE，命中"目标里出现的某些词"即可召回，并按**命中词数**排序
（命中多的更相关）。

## 边界（诚实）
分词 OR LIKE **只修"词在但整串不匹配"**这类（如 README 在 path 里，但被
"file access read touch"稀释成整串匹配不到）。它**修不了**：
- 纯语义（"宠物" ↔ "橘猫"，词面零重叠）
- 跨语言（"乌龙茶" ↔ "oolong tea"）
这些必须走向量召回（embedder / vector_search），不是 LIKE 的职责。
调用方在 embedder 可用时应优先向量路，LIKE 仅兜底。
"""
from __future__ import annotations

import re

# 切分符：空白 + 常见中英文标点 + 路径分隔符
_SPLIT_RE = re.compile(r"[\s\.,，。、;；:：/\\\-_()\[\]{}'\"!?！？]+")
# 中文字符判定
_CJK_RE = re.compile(r"[一-鿿]")
# 英文/数字停用词（过短或太通用，LIKE 噪声大）
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "is", "are", "and", "or",
    "file", "read", "write", "access", "touch", "md", "txt", "py",
})


def tokenize_query(query: str, *, max_tokens: int = 12) -> list[str]:
    """把 query 切成用于 LIKE 的子串 token 列表。

    规则：
    - 按 `_SPLIT_RE` 切片
    - 英文/数字片段：原样保留（去停用词、长度 ≥ 2）
    - 中文片段（≥2 字）：原词 + 所有相邻 2-gram（覆盖"乌龙茶"→乌龙/龙茶/乌龙茶）
    - 去重保序，截断到 max_tokens
    - 全部 lower（LIKE 在调用方用 lower 字段比，或 SQLite LIKE 默认 ASCII
      大小写不敏感；中文无大小写不受影响）

    返回空列表表示无可用 token（调用方应据此返回空，而非全表）。
    """
    if not query or not query.strip():
        return []
    toks: list[str] = []
    for part in _SPLIT_RE.split(query.strip()):
        if not part:
            continue
        if _CJK_RE.search(part):
            # 含中文：加 2-gram + 原词
            if len(part) >= 2:
                for i in range(len(part) - 1):
                    toks.append(part[i:i + 2])
            if len(part) >= 2:
                toks.append(part)
            # 单字中文不加（LIKE 噪声极大）
        else:
            low = part.lower()
            if len(low) >= 2 and low not in _STOPWORDS:
                toks.append(low)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= max_tokens:
            break
    return out
