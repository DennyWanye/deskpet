# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deep-research V8 — research_scoring (tiered authority, recency, diversity)."""
from __future__ import annotations

import time

from deskpet.tools import research_scoring as rs


# --- domain extraction (multi-part TLDs) ---

def test_get_domain_multipart_cn():
    assert rs.get_domain("https://news.xinhuanet.com/a/b") == "xinhuanet.com"
    assert rs.get_domain("https://x.edu.cn/p") == "x.edu.cn"


# --- authority tiers ---

def test_authority_tier1_academic():
    assert rs.score_authority("https://arxiv.org/abs/1234") == 9.5
    assert rs.score_authority("https://en.wikipedia.org/wiki/X") == 9.5


def test_authority_chinese_sources_recognized():
    assert rs.score_authority("https://www.cnki.net/x") == 9.5  # TIER_1 中文学术
    assert rs.score_authority("https://36kr.com/p/123") == 7.5  # TIER_2 中文科技媒体


def test_authority_gov_edu_boost():
    assert rs.score_authority("https://nasa.gov/page") == 8.5
    assert rs.score_authority("https://tsinghua.edu.cn/p") == 8.5


def test_authority_platform_blog_penalised():
    # zhihu /p/ user answer < zhihu editorial
    assert rs.score_authority("https://zhuanlan.zhihu.com/p/999") < 5.5


def test_authority_unknown_baseline():
    assert rs.score_authority("https://random-blog-xyz.net/post") == 3.0


# --- recency by velocity ---

def test_recency_fresh_vs_old():
    now = time.time()
    recent = time.strftime("%Y-%m-%d", time.localtime(now - 30 * 86400))
    old = time.strftime("%Y-%m-%d", time.localtime(now - 2000 * 86400))
    assert rs.score_recency(recent, topic_velocity="fast", now=now) == 10
    assert rs.score_recency(old, topic_velocity="fast", now=now) <= 2


def test_recency_unknown_date():
    assert rs.score_recency("", topic_velocity="medium") == 3.0
    assert rs.score_recency("not-a-date", topic_velocity="medium") == 3.0


# --- diversity / concentration ---

def test_diversity_passes_with_spread():
    urls = [
        "https://arxiv.org/a", "https://nature.com/b", "https://36kr.com/c",
        "https://reuters.com/d", "https://gov.cn/e", "https://zhihu.com/f",
    ]
    rep = rs.diversity_report(urls)
    assert rep["unique_domains"] == 6
    assert rep["passes"] is True
    assert len(rep["source_types"]) >= 2


def test_diversity_fails_when_one_domain_dominates():
    urls = ["https://x.com/1", "https://x.com/2", "https://x.com/3", "https://y.com/4"]
    rep = rs.diversity_report(urls)
    assert rep["max_single_domain_share"] > 0.25
    assert rep["passes"] is False
    assert rep["dominant_domain"] == "x.com"


# --- topic velocity heuristic ---

def test_velocity_fast_for_tech():
    assert rs.infer_topic_velocity("2026 大模型最新趋势") == "fast"


def test_velocity_slow_for_history():
    assert rs.infer_topic_velocity("宋朝历史研究") == "slow"


def test_velocity_default_medium():
    assert rs.infer_topic_velocity("城市垃圾分类政策") == "medium"


# --- 源质量过滤 (codex 评审驱动): 自媒体降权 + AI 生成内容检测 ---

def test_self_media_below_unknown_baseline():
    # 自媒体/转帖农场 2.0 < unknown 3.0 < TIER_3 5.5
    assert rs.score_authority("https://www.sohu.com/a/123_456") == 2.0
    assert rs.score_authority("https://baijiahao.baidu.com/s?id=999") == 2.0
    assert rs.score_authority("https://www.163.com/dy/article/X.html") == 2.0
    # 真权威源仍稳压自媒体
    assert rs.score_authority("https://www.gov.cn/x") > rs.score_authority("https://sohu.com/a")
    assert rs.score_authority("https://arxiv.org/abs/1") > rs.score_authority("https://163.com/dy/x")


def test_ai_generated_disclaimer_detected():
    assert rs.is_ai_generated("本文内容包含人工智能生成内容，请甄别。正文……")
    assert rs.is_ai_generated("免责声明：本文部分内容由AI生成。")
    assert rs.is_ai_generated("This article is AI-generated. Body...")


def test_ai_generated_clean_text_not_flagged():
    assert not rs.is_ai_generated("钠离子电池2025年产量达到3.45GWh，同比接近翻倍。")
    assert not rs.is_ai_generated("")


# --- 乱码源检测 (codex 复评抓到 [^11] 整段乱码) ---

def test_is_mojibake_replacement_chars():
    assert rs.is_mojibake("正常开头" + "�" * 50 + "结尾乱码一片") is True


def test_is_mojibake_cjk_as_gbk():
    # UTF-8 当 GBK 解的典型乱码: 全是不常用 CJK,几乎无常用字
    garbled = "锛阢绅钆婅皖銮旀姤" * 60
    assert rs.is_mojibake(garbled) is True


def test_is_mojibake_latin1_garbage():
    assert rs.is_mojibake("Ã©Ã¨Ã Ã¢Ã£Ã¤Ã¥Ã¦Ã§" * 30) is True


def test_is_mojibake_clean_chinese_not_flagged():
    assert rs.is_mojibake("2025年中国新能源汽车销量大幅增长，渗透率超过百分之五十，"
                          "纯电和插混都在上升，燃油车市场份额持续下降。" * 5) is False


def test_is_mojibake_clean_english_not_flagged():
    assert rs.is_mojibake("Solid state batteries are entering commercial production "
                          "in 2025 with rising energy density and falling costs. " * 5) is False


def test_is_mojibake_short_text_safe():
    assert rs.is_mojibake("短") is False
    assert rs.is_mojibake("") is False
