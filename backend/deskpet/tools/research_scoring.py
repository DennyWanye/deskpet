# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Source scoring for deep-research — ported from DeepResearch V8's
``source_evaluator``, adapted to DeskPet (library, no CLI/shell).

Why this exists
---------------
The original ``research_tools.score_passage`` used a tiny hand-curated
authority map + literal keyword coverage. V8 brings a far richer model:

* **Tiered domain authority** (TIER_1/2/3) that explicitly includes
  **Chinese sources** (cnki / xinhua / 36kr / zhihu / csdn / …) plus a
  ``.gov``/``.edu``/``.cn`` boost — critical now that Chinese queries
  actually return Chinese results (see ``search_provider`` region fix).
* **Recency** scored against a *topic-velocity* curve (fast/medium/slow).
* **Diversity** + **sub-question coverage** checks so one domain can't
  dominate the evidence pool.

The relevance/depth dimensions stay caller-supplied (the pipeline fills
them from keyword coverage and, when available, BGE-M3 semantic
similarity — see ``research_tools``).

Everything here is pure + deterministic (no network, no LLM) so it is
trivially unit-testable, mirroring V8's "deterministic helpers" stance.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

# === Domain authority tiers (ported + de-duped from V8) ===

TIER_1 = {  # 9-10: peer-reviewed / official bodies / primary
    "nature.com", "science.org", "thelancet.com", "nejm.org",
    "cell.com", "pnas.org", "ieee.org", "acm.org",
    "who.int", "nih.gov", "cdc.gov", "europa.eu",
    "arxiv.org", "semanticscholar.org",
    # Reference-grade (V8's list omitted these; they are curated + cited)
    "wikipedia.org", "britannica.com",
    # Chinese academic / official
    "cnki.net", "wanfangdata.com.cn", "cqvip.com",
    "cas.cn", "nsfc.gov.cn", "xueshu.baidu.com", "gov.cn",
    # Chinese first-party disclosure (Phase-2 direct sources)
    "cninfo.com.cn", "sse.com.cn", "szse.cn",
}

TIER_2 = {  # 7-8: reputable news / established industry
    "reuters.com", "apnews.com", "bbc.com", "nytimes.com",
    "theguardian.com", "washingtonpost.com", "economist.com",
    "techcrunch.com", "arstechnica.com", "wired.com",
    "github.com", "stackoverflow.com", "hbr.org",
    "mckinsey.com", "bcg.com", "gartner.com",
    # Chinese reputable news / tech media
    "xinhuanet.com", "people.com.cn", "thepaper.cn",
    "36kr.com", "infoq.cn", "juejin.cn", "jiqizhixin.com",
    "leiphone.com", "geekpark.net", "caixin.com",
}

TIER_3 = {  # 5-6: industry blogs / vendor / community
    "medium.com", "substack.com", "dev.to",
    "engineering.fb.com", "blog.google", "aws.amazon.com",
    "openai.com", "anthropic.com", "deepmind.google",
    "huggingface.co", "pytorch.org", "tensorflow.org",
    # Chinese industry / community
    "zhihu.com", "csdn.net", "segmentfault.com",
    "oschina.net", "toutiao.com", "sspai.com",
    "volcengine.com", "cloud.tencent.com", "aliyun.com",
}

# 自媒体/转帖农场 —— 明确**低于** unknown 基线(3.0)再降权。codex 评审实测:
# DuckDuckGo 中文结果里 sohu/百家号/网易号 大量转帖(含 AI 生成内容)混进证据池,
# 撑起了"2025 产量/企业产能"等关键数字,证据层级不够。这些平台既有少量原创新闻
# 也有海量洗稿转帖,域名层无法区分 → 统一降到 2.0(低于 unknown),让真权威源(官方/
# 学术/一线媒体)在打分里稳压它们;一手核查仍由 synth 阶段的口径提示兜。
SELF_MEDIA = {
    "sohu.com", "baijiahao.baidu.com", "163.com", "dy.163.com",
    "baidu.com", "sina.com.cn", "ifeng.com", "qq.com",
    "bilibili.com", "xiaohongshu.com", "weixin.qq.com",
    "uc.cn", "kuaishou.com", "douyin.com",
}

GOV_EDU_SUFFIXES = (".gov", ".edu", ".gov.cn", ".edu.cn", ".ac.uk", ".ac.jp", ".ac.cn")

# 字典/词义/单字百科类站点 —— 对**任何**实质研究都无引据价值,却常因主题词被拆成
# 单字(如"特斯拉"被搜成"特")命中"X字的意思"页污染结果池(真机查特斯拉报告暴露:9 源里
# 4 个是字典页)。命中即在 _passage_from 阶段直接剔除(与 ai_generated/mojibake 同级)。
LOW_QUALITY = {
    "hanyuguoxue.com", "hgcha.com", "qianp.com", "cidian.qianp.com",
    "zidian.qianp.com", "zdic.net", "cidian.911cha.com", "tool.httpcn.com",
    "chazidian.com", "zd9999.com", "guoxuedashi.net", "kxue.com",
    "obsky.com", "5156edu.com",
    # 真机豆瓣调研暴露的漏网字典/词义站(精确域名列表追不完 → 下面补模式匹配)
    "chienwen.net", "gushici.net", "kmcha.com", "hwxnet.com", "cha88.cn",
}

# 字典/词义站**模式匹配**(精确列表追不完): 子域 dictionary./zidian./cidian./hanyu./
# cihai./chengyu./ciku./xinhua. 或路径含 /cidian//zidian//chengyu/ → 几乎必是字典词条页。
# (真机豆瓣调研:dictionary.chienwen.net / dictionary.cambridge.org / zidian.gushici.net
#  混进报告引用 —— 用模式一次性根治,不再逐个追域名。)
_LOW_QUALITY_HOST_RE = re.compile(
    r"(^|\.)(dictionary|zidian|cidian|hanyu|cihai|chengyu|ciku|xinhuazidian)\."
)
_LOW_QUALITY_PATH_RE = re.compile(r"/(cidian|zidian|chengyu|cihai)/")


def is_low_quality(url: str) -> bool:
    """字典/词义站 → True(应剔除)。精确域名列表 + 子域/路径模式双判,覆盖其子域。"""
    if get_domain(url) in LOW_QUALITY:
        return True
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if _LOW_QUALITY_HOST_RE.search(host):
        return True
    path = (parsed.path or "").lower()
    if _LOW_QUALITY_PATH_RE.search(path):
        return True
    return False

# AI 生成内容声明 —— 命中即视为不可作正式引据(codex 评审抓到 [^3] 明示"包含人工
# 智能生成内容"却撑核心事实)。研究管线据此直接剔除该来源。
_AI_DISCLAIMER_RE = re.compile(
    r"(包含|由|本(文|内容|页|图)).{0,8}(人工智能|AI|ai|智能).{0,6}(生成|创作|辅助生成)"
    r"|AI[- ]?generated|generated by AI|本文部分内容由AI",
)

_MULTI_TLDS = (
    ".com.cn", ".gov.cn", ".edu.cn", ".ac.cn", ".org.cn",
    ".co.uk", ".ac.uk", ".org.uk", ".co.jp", ".ac.jp",
)

_BLOG_PLATFORMS = ("medium.com", "substack.com", "zhihu.com", "csdn.net", "dev.to")


def get_domain(url: str) -> str:
    """Registrable domain (handles multi-part TLDs like .com.cn / .co.uk)."""
    hostname = (urlparse(url).hostname or "").lower()
    parts = hostname.split(".")
    for tld in _MULTI_TLDS:
        if hostname.endswith(tld):
            n = tld.count(".")
            return ".".join(parts[-(n + 1):]) if len(parts) > n else hostname
    return ".".join(parts[-2:]) if len(parts) >= 2 else hostname


def is_platform_blog(url: str) -> bool:
    """User-content page on a platform (zhihu/p/, medium user blog) → lower
    authority than the platform's editorial content."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    domain = get_domain(url)
    if domain in _BLOG_PLATFORMS and hostname not in (domain, f"www.{domain}"):
        return True
    if domain == "zhihu.com" and "/p/" in parsed.path.lower():
        return True
    return False


def is_ai_generated(text: str) -> bool:
    """页面正文是否自带"包含 AI 生成内容"声明 → 不可作正式引据。"""
    if not text:
        return False
    return bool(_AI_DISCLAIMER_RE.search(text[:4000]))


# 中文最常用字(覆盖真实中文文本的大头);乱码(锛鐢绗...)里几乎不出现。
_COMMON_CJK = set(
    "的一是不了在人有我他这中大来上国个到说们为子和你地出道也时"
    "年得就那要下以生会自着去之过家学对可她里后小么心多天而能好"
)


def is_mojibake(text: str) -> bool:
    """正文是否解码乱码(编码声明错→trafilatura 抽出 mojibake)→ 不可引据。

    覆盖三类常见乱码(codex 评审抓到 [^11] 整段乱码):
      1. U+FFFD 替换字符成片(GBK 字节当 UTF-8 解)。
      2. Latin-1 补充区垃圾成片(UTF-8 字节当 latin1 解,出 Ã Â à 串)。
      3. CJK-as-GBK 乱码(UTF-8 当 GBK 解,出 锛鐢绗): 文本以 CJK 为主却几乎
         没有常用字 → 判乱码。
    纯英文/正常中文/短文本均不误杀。"""
    if not text:
        return False
    s = text[:3000]
    n = len(s)
    if n < 30:
        return False
    if s.count("�") / n > 0.02:
        return True
    latin1 = sum(1 for c in s if " " <= c <= "ÿ")
    if latin1 / n > 0.15:
        return True
    cjk = [c for c in s if "一" <= c <= "鿿"]
    if len(cjk) >= n * 0.3:  # 以 CJK 为主 → 应是中文
        common = sum(1 for c in cjk if c in _COMMON_CJK)
        if common / len(cjk) < 0.02:  # 常用字占比极低 → 乱码
            return True
    return False


def score_authority(url: str) -> float:
    """0-10 domain authority."""
    hostname = (urlparse(url).hostname or "").lower()
    for suffix in GOV_EDU_SUFFIXES:
        if hostname.endswith(suffix):
            return 8.5
    domain = get_domain(url)
    if domain in TIER_1:
        return 9.5
    if domain in TIER_2:
        return 7.5 - (1.5 if is_platform_blog(url) else 0.0)
    if domain in TIER_3:
        return 5.5 - (1.0 if is_platform_blog(url) else 0.0)
    if domain in SELF_MEDIA:
        return 2.0  # 转帖农场/自媒体:低于 unknown,稳被真权威源压
    return 3.0  # unknown baseline


_VELOCITY_THRESHOLDS = {
    "fast": [(180, 10), (365, 7), (730, 4)],
    "medium": [(365, 10), (1095, 7), (1825, 4)],
    "slow": [(1825, 10), (3650, 7), (7300, 4)],
}


def score_recency(date_str: str, *, topic_velocity: str = "medium", now: Optional[float] = None) -> float:
    """0-10 recency vs a velocity curve. Unknown/garbage date → 3.0."""
    if not date_str:
        return 3.0
    pub = None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            pub = time.strptime(str(date_str)[: len(fmt) + 2], fmt)
            break
        except (ValueError, TypeError):
            continue
    if pub is None:
        return 3.0
    now_s = now if now is not None else time.time()
    age_days = (now_s - time.mktime(pub)) / 86400.0
    for max_days, score in _VELOCITY_THRESHOLDS.get(topic_velocity, _VELOCITY_THRESHOLDS["medium"]):
        if age_days <= max_days:
            return float(score)
    return 2.0


def composite_score(
    *, authority: float, recency: float, relevance: float, depth: float,
    topic_velocity: str = "medium",
) -> float:
    """Weighted blend. Slow topics down-weight recency heavily."""
    if topic_velocity == "slow":
        return authority * 0.35 + recency * 0.05 + relevance * 0.35 + depth * 0.25
    return authority * 0.3 + recency * 0.2 + relevance * 0.3 + depth * 0.2


def diversity_report(urls: list[str]) -> dict[str, Any]:
    """Domain spread + type mix. ``passes`` when ≥5 unique domains AND no
    single domain holds >25% of the pool (V8 thresholds)."""
    domains = [get_domain(u) for u in urls if u]
    counts: dict[str, int] = {}
    for d in domains:
        counts[d] = counts.get(d, 0) + 1
    total = len(domains) or 1
    max_share = max(counts.values(), default=0) / total
    types: set[str] = set()
    for d in domains:
        if d in TIER_1:
            types.add("academic")
        elif d in TIER_2:
            types.add("news/industry")
        elif d in TIER_3:
            types.add("blog/community")
        else:
            types.add("other")
    return {
        "unique_domains": len(set(domains)),
        "max_single_domain_share": round(max_share, 2),
        "dominant_domain": max(counts, key=counts.get, default="none") if counts else "none",
        "source_types": sorted(types),
        "type_diversity": len(types) >= 3,
        "passes": len(set(domains)) >= 5 and max_share <= 0.25,
    }


def infer_topic_velocity(topic: str) -> str:
    """Cheap heuristic: tech/news topics move fast; law/history slow."""
    t = (topic or "").lower()
    fast = ("ai", "llm", "gpt", "模型", "新能源", "股", "crypto", "芯片", "最新", "2026", "趋势", "发布")
    slow = ("history", "历史", "哲学", "理论", "数学", "physics", "law", "宪法", "古")
    if any(k in t for k in slow):
        return "slow"
    if any(k in t for k in fast):
        return "fast"
    return "medium"


__all__ = [
    "TIER_1", "TIER_2", "TIER_3", "SELF_MEDIA",
    "get_domain", "is_platform_blog", "score_authority", "score_recency",
    "composite_score", "diversity_report", "infer_topic_velocity",
    "is_ai_generated", "is_mojibake",
]
