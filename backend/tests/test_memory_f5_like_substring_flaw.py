# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G2 — F5「LIKE 整串匹配缺陷」专项测试（记忆系统严测 Phase 1）。

## 背景
`facts.search()`（facts.py:432）、`workspace.recall()`（workspace.py:223）、
`facts.find_by_entities()`（facts.py:452）都用 `LIKE '%{整个 query}%'` 整串子串
匹配。agent 传自然语言 query（如 "宠物的名字"、"刚读的文档"），字段内容（如
path `.../README.md`、value `我家猫叫旺财`）**不可能包含整个自然语言串** → 几乎
永远 0 命中。

这是 2026-06-01 F1~F4 真机 GUI 终验时挖出的缺陷（详见
plans/2026-05-31-memory-tools-flag-gating-bugs.md §F5）：用户在 code 模式让桌宠
`workspace_recall` 查"刚读过哪些文件"，agent 传 query
`"...README.md file access read touch"`，workspace_state 表里明明有 README 那行，
却召回 0 条。

## 本文件的两阶段策略
- **Phase 1（现状钉死，本文件当前形态）**：断言缺陷存在 —— 自然语言 query
  返回空。这些测试**现在 PASS**，钉死"修复前行为"，防止无意中以为它能用。
- **Phase 2（修复后翻转）**：F5 修复（分词 / FTS / 向量召回）后，把
  `EXPECT_NL_QUERY_HITS` 改 True，断言自然语言 query 能命中。届时这些
  `test_*_nl_query_currently_misses` 会失败，提示需改成正向断言版本。

子串路（精确子串 query）必须始终工作 —— G2.4 防止修 F5 时破坏子串路。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.embedder import Embedder
from deskpet.memory.facts import FactsStore
from deskpet.memory.workspace import WorkspaceMemoryStore
from deskpet.tools import memory_tools as _mt

# F5 修复后改 True，本文件的"currently misses"测试会失败 → 提示改成正向版本。
EXPECT_NL_QUERY_HITS = False


@pytest_asyncio.fixture
async def facts_store(tmp_path: Path) -> FactsStore:
    return FactsStore(tmp_path / "facts.db")


@pytest_asyncio.fixture
async def ws_store(tmp_path: Path) -> WorkspaceMemoryStore:
    return WorkspaceMemoryStore(tmp_path / "ws.db")


# ----------------------------------------------------------------------
# G2.1 — facts.search 自然语言 query（缺陷：搜不出语义相关但文本不同的）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g2_1_facts_search_nl_query_currently_misses(
    facts_store: FactsStore,
) -> None:
    """存"我家猫叫旺财，三岁橘猫"，用自然语言 query "宠物" 搜 → 当前 0 命中。

    "宠物" 不是 value/key/evidence 的子串 → LIKE '%宠物%' 匹配不到。
    这正是 F5：用户问"宠物"，记忆里有"猫叫旺财"，却搜不出来。
    """
    await facts_store.upsert(
        category="profile",
        subject="user",
        key="pet",
        value="我家猫叫旺财，三岁橘猫",
        confidence=0.9,
        source_msg_id=1,
        evidence="用户说他家猫叫旺财",
    )
    hits = await facts_store.search("宠物", limit=10)
    if EXPECT_NL_QUERY_HITS:
        assert hits, "F5 已修复：自然语言 query '宠物' 应命中'我家猫叫旺财'"
    else:
        # 钉死缺陷：当前 LIKE 整串匹配 → '宠物' 不是任何字段子串 → 0 命中
        assert hits == [], (
            "F5 现状：facts.search LIKE 整串匹配，'宠物' 搜不到'我家猫叫旺财'。"
            "若此断言失败，说明 F5 已被修复 —— 把 EXPECT_NL_QUERY_HITS 改 True。"
        )


# ----------------------------------------------------------------------
# G2.2 — workspace.recall 自然语言 query（F5 真机终验暴露的那条）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g2_2_workspace_recall_nl_query_currently_misses(
    ws_store: WorkspaceMemoryStore,
) -> None:
    """存 README.md read 动作，自然语言 query "刚读的文档" → 当前 0 命中。

    复现 2026-06-01 真机终验：agent 传描述性 query，path 字段
    `G:/projects/.../README.md` 不含"刚读的文档" → recall 返回空。
    """
    await ws_store.record_action(
        session_id="s1",
        path="G:/projects/test-research-helper/README.md",
        action="read",
        content="# ResearchFlow\nAI 驱动的科学研究助手",
    )
    hits = await ws_store.recall("刚读的文档", session_id="s1")
    if EXPECT_NL_QUERY_HITS:
        assert hits, "F5 已修复：自然语言 query '刚读的文档' 应召回 README"
    else:
        assert hits == [], (
            "F5 现状：workspace.recall LIKE 整串匹配，'刚读的文档' 搜不到 README。"
            "若此断言失败，说明 F5 已修复 —— 把 EXPECT_NL_QUERY_HITS 改 True。"
        )


@pytest.mark.asyncio
async def test_g2_2b_workspace_recall_descriptive_query_now_hits(
    ws_store: WorkspaceMemoryStore,
) -> None:
    """复现真机终验那条，F5 分词修复后**应命中**（正向断言，已翻转）。

    2026-06-01 真机终验 agent 传 query
    "test-research-helper README.md file access read touch"，旧整串 LIKE
    匹配不到 path → 0 命中（钉死过）。F5 Step 1 分词 OR LIKE 修复后：query
    分出 test-research-helper / README / ... 等词，path 含 README + 路径段
    → 命中。这是 F5 ① 层（词在但整串不匹配）修复的核心证据。

    注：纯语义 / 跨语言（"宠物"↔"橘猫"，见 G2.1/G2.2）分词仍修不了，
    那属 F5 ② 层，须走向量召回（Step 2）。
    """
    await ws_store.record_action(
        session_id="s1",
        path="G:/projects/test-research-helper/README.md",
        action="read",
        content="# ResearchFlow",
    )
    hits = await ws_store.recall(
        "test-research-helper README.md file access read touch",
        session_id="s1",
    )
    # F5 ① 修复后：分词命中 path 里的 README / test-research-helper
    assert hits, (
        "F5 分词修复后：描述性 query 应分词命中 README（真机终验那条）。"
        "若为空说明分词 OR LIKE 回归。"
    )
    assert any("README" in h["path"] for h in hits), (
        f"应召回 README.md 那条: {[h['path'] for h in hits]}"
    )


# ----------------------------------------------------------------------
# G2.4 — 精确子串路必须始终工作（防修 F5 时破坏子串匹配）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g2_4_facts_search_exact_substring_always_works(
    facts_store: FactsStore,
) -> None:
    """回归保护：精确子串 query 任何时候都该命中（F5 修复不能破坏这条）。"""
    await facts_store.upsert(
        category="preference",
        subject="user",
        key="favorite_drink",
        value="oolong tea",
        confidence=0.9,
        source_msg_id=1,
        evidence="user likes oolong",
    )
    # "oolong" 是 value "oolong tea" 的精确子串
    hits = await facts_store.search("oolong", limit=10)
    assert len(hits) == 1, "精确子串 'oolong' 必须命中 'oolong tea'（F5 修复后仍须保持）"
    assert "oolong" in hits[0]["value"]


@pytest.mark.asyncio
async def test_g2_4b_workspace_recall_exact_substring_always_works(
    ws_store: WorkspaceMemoryStore,
) -> None:
    """回归保护：path 段落子串 query 任何时候都该命中。"""
    await ws_store.record_action(
        session_id="s1",
        path="src/components/Header.tsx",
        action="write",
        content="export const Header = () => null",
    )
    # "Header" 是 path 的精确子串
    hits = await ws_store.recall("Header", session_id="s1")
    assert len(hits) == 1, "精确子串 'Header' 必须命中 path（F5 修复后仍须保持）"
    assert "Header" in hits[0]["path"]


# ----------------------------------------------------------------------
# G2.5 — F5 ② 层（纯语义）：memory_search 向量优先（真 BGE-M3）+ mock 对照
# ----------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.model_required
async def test_g2_5_memory_search_vector_semantic_real(tmp_path: Path) -> None:
    """F5 ② 层修复验证：真 embedder 下 memory_search 向量优先召回纯语义。

    存"我家养了一只橘猫"+"今天股票涨了三个点"，query"宠物"（文本零重叠）。
    LIKE 路（G2.1）下"宠物"必 0 命中；向量路下"橘猫"语义相关 → 排第一。
    断言：retrieval=vector（确实走了向量）+ 橘猫 rank 在股票之前。
    """
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _model_path import resolve_bge_m3

    model = resolve_bge_m3()
    if model is None:
        pytest.skip("BGE-M3 模型未找到；装模型或设 DESKPET_BGE_M3_DIR 后再跑")
    e = Embedder(model_path=model, use_mock_when_missing=False)
    await e.warmup()
    try:
        fs = FactsStore(tmp_path / "f.db", embedder=e)
        await fs.upsert(
            category="profile", subject="user", key="pet",
            value="我家养了一只橘猫", confidence=0.9,
            source_msg_id=1, evidence="x",
        )
        await fs.upsert(
            category="profile", subject="user", key="stock",
            value="今天股票涨了三个点", confidence=0.9,
            source_msg_id=2, evidence="y",
        )
        _mt.bind(facts_store=fs, embedder=e, llm_call=None,
                 enable_natural_language=False)
        r = json.loads(
            await _mt._memory_search_handle({"query": "宠物", "top_k": 3}, "t")
        )
        assert r["ok"] is True
        # 走了向量路（非 LIKE）
        assert r.get("retrieval") == "vector", (
            f"embedder 可用时应走向量路: {r.get('retrieval')}"
        )
        vals = [x["value"] for x in r["results"]]
        assert any("橘猫" in v for v in vals), f"语义应召回'橘猫': {vals}"
        # rank 断言：语义相关的橘猫排在无关的股票之前
        cat_i = next(i for i, v in enumerate(vals) if "橘猫" in v)
        stock_i = next((i for i, v in enumerate(vals) if "股票" in v), 999)
        assert cat_i < stock_i, f"'橘猫'应排在'股票'之前: {vals}"
    finally:
        _mt.bind(facts_store=None, embedder=None, llm_call=None,  # type: ignore[arg-type]
                 enable_natural_language=False)
        await e.close()
