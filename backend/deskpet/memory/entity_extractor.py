"""Stage 2 WI-S2.2 — Entity extraction for query-side NER.

Three-tier degradation chain (see PRD §3.3 / TDD §A2.2):

  1. :class:`LLMEntityExtractor` — primary (lightweight LLM call).
  2. :class:`RegexEntityExtractor` — fallback, with stopword filter.
  3. :class:`NoopEntityExtractor` — final fallback (always returns ``[]``).

:class:`CompositeEntityExtractor` composes LLM → Regex degradation:
LLM 抽空（无网络 / 配额耗尽 / 抛异常）→ 自动降级 regex；regex 提空 →
返回 ``[]``，调用方据此跳过 entity 路。

设计原则
--------

* **停用词集（v2 ★）** — regex 太宽（``[一-龥]{2,4}`` 命中"今天""怎么"
  等高频字），单独维护 ``_STOPWORDS`` 抹掉噪声。LLM 也走同一停用词
  过滤（防 LLM 把"我们"当作 entity）。
* **数量上限** — 单 query 最多返 5 个 entity，保护下游 LIKE 查询。
* **长度下限** — 单 entity 长度 < 2 字符跳过（用于 ``FactsStore.find_by_entities``）。
* **失败兜底** — LLM 抛异常 / 返回非法 JSON → 返 ``[]``，永不冒泡。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger(__name__)


# v2 ★ 停用词集 —— 防 regex / LLM 把高频中文字符 / 英文疑问词当 entity。
# 中文部分覆盖 PRD §3.3 D7 v2 + TDD §A2.2 列举的 30+ 词，外加常见疑问/连接词。
# 英文部分覆盖 12+ 大写疑问/指代词（regex `\b[A-Z][a-zA-Z]+\b` 会命中）。
_STOPWORDS: frozenset[str] = frozenset({
    # ── 中文：代词 / 时间 / 疑问 / 助词 / 高频泛指 ──
    "我的", "我们", "你的", "你们", "他的", "她的",
    "今天", "明天", "昨天", "前天", "后天",
    "这个", "那个", "这些", "那些", "这里", "那里",
    "什么", "怎么", "怎样", "为什么", "为啥",
    "了吗", "的人", "的话", "的事", "有什么",
    "可以", "知道", "应该", "可能", "已经", "需要",
    "时候", "地方", "事情", "东西", "问题", "意思",
    "因为", "所以", "如果", "但是", "然后", "现在",
    "一下", "一些", "其他", "其它",
    # ── 英文：疑问 + 指代 + 高频泛指 ──
    "What", "When", "Where", "Why", "How", "Who", "Which",
    "The", "This", "That", "These", "Those",
    "There", "Here", "Some", "Any", "All",
    "Yes", "No", "Hello", "Hi",
})


def _filter_stopwords(items: list[str]) -> list[str]:
    """统一停用词过滤入口（LLM / regex 通用）。"""
    return [x for x in items if x and x.strip() and x not in _STOPWORDS]


class EntityExtractor(Protocol):
    """Duck-typed 协议。任何带 ``async def extract(query) -> list[str]``
    的对象都可注入 :class:`EnhancedRetriever`。"""

    async def extract(self, query: str) -> list[str]:  # pragma: no cover - protocol
        ...


# ────────────────────────────────────────────────────────────────────
# 1. Regex 实现（fallback，不依赖任何外部）
# ────────────────────────────────────────────────────────────────────


class RegexEntityExtractor:
    """正则抽取 + 停用词过滤。

    * 中文：识别连续汉字串后**展开所有 2~4 字子串**（覆盖人名 / 宠物名
      / 地名 / 短语）。展开是必须的 —— 贪心 ``{2,4}`` 会把"旺财怎么样了"
      切成"旺财怎么"+"样了"，导致"旺财"永远不出现，停用词集也无从过滤。
    * 英文：首字母大写后续字母的词（专名 heuristic）。

    去重保序：先到先得。最终上限 5 个 entity。
    """

    # 中文连续段：1 个或多个汉字（具体子串由 _expand_cjk_substrings 切）。
    _CN_RUN = re.compile(r"[一-鿿]+")
    _EN = re.compile(r"\b[A-Z][a-zA-Z]+\b")

    @staticmethod
    def _expand_cjk_substrings(run: str) -> list[str]:
        """对连续汉字段切出所有 2~4 字子串（保留出现顺序）。

        例："旺财怎么样了" → 长 6 ⇒
            2 字：旺财 财怎 怎么 么样 样了
            3 字：旺财怎 财怎么 怎么样 么样了
            4 字：旺财怎么 财怎么样 怎么样了
        停用词集随后把"怎么""怎么样""今天"等过滤掉。
        """
        out: list[str] = []
        n = len(run)
        for size in (2, 3, 4):
            if n < size:
                break
            for i in range(0, n - size + 1):
                out.append(run[i : i + size])
        return out

    async def extract(self, query: str) -> list[str]:
        if not query or not query.strip():
            return []
        cn_subs: list[str] = []
        for m in self._CN_RUN.finditer(query):
            cn_subs.extend(self._expand_cjk_substrings(m.group(0)))
        en_hits = self._EN.findall(query)
        # 保序去重 + 停用词过滤
        seen: dict[str, None] = {}
        for h in cn_subs + en_hits:
            if h in _STOPWORDS:
                continue
            seen.setdefault(h, None)
        return list(seen)[:5]


# ────────────────────────────────────────────────────────────────────
# 2. LLM 实现（primary）
# ────────────────────────────────────────────────────────────────────


_ENTITY_PROMPT = """\
Extract named entities (person names, pet names, place names, project
names, specific objects) from the user query. Output only a JSON list
of strings, e.g. ["旺财", "Mike", "上海"]. If no entities, output [].

Common stopwords to AVOID: 我的、我们、今天、这个、什么、怎么、了吗 等

Query: {query}

Output JSON only.
"""


def _parse_entities(raw: str) -> list[str]:
    """从 LLM 输出抽 JSON list。容忍前后 markdown 围栏 / 噪声 prose。"""
    if not raw:
        return []
    text = raw.strip()
    # 容忍 ```json ... ``` 围栏
    if text.startswith("```"):
        text = text.strip("`")
        # 去 "json\n" 前缀
        if "\n" in text:
            text = text.split("\n", 1)[1]
    # 容忍前面有噪声 prose —— 只取第一个 `[` 到最后一个 `]`
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(arr, list):
        return []
    out: list[str] = []
    for item in arr:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


class LLMEntityExtractor:
    """调用 ``(prompt: str) -> str`` 形状的 LLM call 抽实体。

    LLM 抛异常 / 返回空 / 解析失败 → 返 ``[]``。永不冒泡，让 Composite
    顺利降级 regex。
    """

    def __init__(self, llm_call: Callable[[str], Awaitable[str]]) -> None:
        self._llm = llm_call

    async def extract(self, query: str) -> list[str]:
        if not query or not query.strip():
            return []
        if self._llm is None:
            return []
        try:
            raw = await self._llm(_ENTITY_PROMPT.format(query=query))
        except Exception as exc:  # noqa: BLE001 — 任何 LLM 错误都降级
            log.debug("LLMEntityExtractor failed: %s", exc)
            return []
        entities = _parse_entities(raw)
        # 同样过停用词（防 LLM 偶尔把"我们"当 entity）
        filtered = _filter_stopwords(entities)
        return filtered[:5]


# ────────────────────────────────────────────────────────────────────
# 3. Noop 实现（最终兜底）
# ────────────────────────────────────────────────────────────────────


class NoopEntityExtractor:
    """永远返 ``[]``。entity 路相当于关闭，但保持接口兼容。"""

    async def extract(self, query: str) -> list[str]:  # noqa: ARG002
        return []


# ────────────────────────────────────────────────────────────────────
# 4. Composite 降级链（LLM → Regex）
# ────────────────────────────────────────────────────────────────────


class CompositeEntityExtractor:
    """LLM 抽空 → 自动降级 regex。

    "抽空"判定：LLM extractor 返回空 list（含 LLM 调用失败的兜底）。
    LLM 命中至少 1 个就直接采用，不再混 regex。
    """

    def __init__(
        self, llm_ex: EntityExtractor, regex_ex: EntityExtractor
    ) -> None:
        self._llm = llm_ex
        self._regex = regex_ex

    async def extract(self, query: str) -> list[str]:
        result = await self._llm.extract(query)
        if result:
            return result
        return await self._regex.extract(query)
