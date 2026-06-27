# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-3.1 — goal / decision / constraint 记忆抽取 unit tests.

Test groups:
  TG-1  flag on: mock LLM returns 3-category JSON → upsert 3 rows
  TG-2  _CATEGORY_DECAY has goal/decision/constraint with correct rates
  TG-3  scope column write + list_active filters by scope
  TG-6  flag off → prompt uses OLD version (4 categories, byte-level regression)
  TG-ex positive-exemption content checks (持续性目标 vs 一次性事件 in prompt text)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.facts import (
    FactsStore,
    FactExtractor,
    ExtractedFact,
    _CATEGORY_DECAY,
    _EXTRACT_PROMPT,
    _EXTRACT_PROMPT_WITH_GOALS,
    VALID_CATEGORIES,
)
from deskpet.memory.migrator import ensure_v9
from deskpet.memory.schema_v2_migrator import ensure_memory_v2_columns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    await ensure_v9(db)
    await ensure_memory_v2_columns(db)
    return db


class _FakeLLM:
    """Async callable returning a queued list of responses (or exceptions)."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            return "[]"
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


# ---------------------------------------------------------------------------
# TG-2: _CATEGORY_DECAY contains the three new categories with correct rates
# ---------------------------------------------------------------------------


class TestCategoryDecay:
    def test_goal_in_decay(self):
        assert "goal" in _CATEGORY_DECAY, "_CATEGORY_DECAY missing 'goal'"

    def test_decision_in_decay(self):
        assert "decision" in _CATEGORY_DECAY, "_CATEGORY_DECAY missing 'decision'"

    def test_constraint_in_decay(self):
        assert "constraint" in _CATEGORY_DECAY, "_CATEGORY_DECAY missing 'constraint'"

    def test_goal_rate_approx_200d_half_life(self):
        """goal=0.005 → half-life ≈ ln(2)/0.005 ≈ 138.6 days (≈200d range)."""
        rate = _CATEGORY_DECAY["goal"]
        assert rate == pytest.approx(0.005, rel=1e-6)
        # numeric: exp(-rate * half_life) == 0.5
        half_life = math.log(2) / rate
        assert 100.0 < half_life < 250.0, f"half_life={half_life:.1f} out of [100, 250]d"

    def test_decision_rate(self):
        assert _CATEGORY_DECAY["decision"] == pytest.approx(0.002, rel=1e-6)

    def test_constraint_rate(self):
        assert _CATEGORY_DECAY["constraint"] == pytest.approx(0.001, rel=1e-6)

    def test_valid_categories_includes_new(self):
        """VALID_CATEGORIES is derived from _CATEGORY_DECAY, so all three must be in."""
        for cat in ("goal", "decision", "constraint"):
            assert cat in VALID_CATEGORIES, f"VALID_CATEGORIES missing '{cat}'"

    def test_extracted_fact_is_valid_for_new_categories(self):
        for cat in ("goal", "decision", "constraint"):
            f = ExtractedFact(
                category=cat, subject="user", key="k", value="v", confidence=0.9,
                evidence="e",
            )
            assert f.is_valid(), f"is_valid() False for category={cat!r}"


# ---------------------------------------------------------------------------
# TG-6: flag=False → prompt is OLD version (no new categories / no exemption)
# ---------------------------------------------------------------------------


class TestPromptVersions:
    def test_old_prompt_lacks_goal_category(self):
        """_EXTRACT_PROMPT (flag-off version) must NOT contain 'goal'."""
        assert "goal" not in _EXTRACT_PROMPT, (
            "Old prompt must be BC — 'goal' must not appear"
        )

    def test_old_prompt_lacks_decision_category(self):
        assert "decision" not in _EXTRACT_PROMPT

    def test_old_prompt_lacks_constraint_category(self):
        assert "constraint" not in _EXTRACT_PROMPT

    def test_old_prompt_lacks_positive_exemption_text(self):
        """The Chinese exemption paragraph must NOT appear in the old prompt."""
        assert "持续性目标" not in _EXTRACT_PROMPT

    def test_new_prompt_contains_goal_category(self):
        """_EXTRACT_PROMPT_WITH_GOALS (flag-on version) must contain 'goal'."""
        assert "goal" in _EXTRACT_PROMPT_WITH_GOALS

    def test_new_prompt_contains_decision_category(self):
        assert "decision" in _EXTRACT_PROMPT_WITH_GOALS

    def test_new_prompt_contains_constraint_category(self):
        assert "constraint" in _EXTRACT_PROMPT_WITH_GOALS

    def test_new_prompt_positive_exemption_text(self):
        """New prompt must have the positive-whitelist exemption paragraph."""
        assert "持续性目标" in _EXTRACT_PROMPT_WITH_GOALS

    def test_new_prompt_excludes_one_off_mention(self):
        """New prompt still mentions one-off exclusion (一次性事件 still excluded)."""
        assert "一次性事件" in _EXTRACT_PROMPT_WITH_GOALS

    def test_old_prompt_has_four_base_categories(self):
        """Old prompt categories line: preference | profile | project | event."""
        for cat in ("preference", "profile", "project", "event"):
            assert cat in _EXTRACT_PROMPT, f"Old prompt missing category '{cat}'"


# ---------------------------------------------------------------------------
# TG-1: flag on → FactExtractor uses new prompt → upserts goal/decision/constraint
# ---------------------------------------------------------------------------


GOAL_DECISION_CONSTRAINT_JSON = json.dumps([
    {
        "category": "goal",
        "subject": "user",
        "key": "monthly_project_goal",
        "value": "[active] 月底前完成 PPT 生成模块",
        "confidence": 0.9,
        "evidence": "我想在月底前完成 PPT 生成模块",
    },
    {
        "category": "decision",
        "subject": "user",
        "key": "lang_decision",
        "value": "项目决定用 TypeScript",
        "confidence": 0.95,
        "evidence": "这个项目决定用 TypeScript",
    },
    {
        "category": "constraint",
        "subject": "user",
        "key": "budget_constraint",
        "value": "预算 2000 以内",
        "confidence": 0.92,
        "evidence": "预算 2000 以内",
    },
])


class TestFactExtractorWithGoalFlag:
    @pytest.mark.asyncio
    async def test_upserts_three_new_category_rows(self, db_path: Path):
        """TG-1: with goal_facts=True, mock LLM returning 3-category JSON
        → FactExtractor inserts 3 rows into the facts table."""
        llm = _FakeLLM([GOAL_DECISION_CONSTRAINT_JSON])
        store = FactsStore(db_path)
        extractor = FactExtractor(
            store,
            extract_llm=llm,
            goal_facts=True,
        )
        results = await extractor.process_message(
            message_id=1,
            content="我想在月底前完成 PPT 生成模块，这个项目决定用 TypeScript，预算 2000 以内",
            role="user",
        )
        assert len(results) == 3
        categories = {r["category"] for r in results}
        assert categories == {"goal", "decision", "constraint"}

    @pytest.mark.asyncio
    async def test_goal_facts_true_uses_new_prompt(self, db_path: Path):
        """When goal_facts=True, the LLM receives _EXTRACT_PROMPT_WITH_GOALS."""
        llm = _FakeLLM(["[]"])
        store = FactsStore(db_path)
        extractor = FactExtractor(store, extract_llm=llm, goal_facts=True)
        await extractor.process_message(
            message_id=1, content="我的预算是 2000 以内", role="user"
        )
        assert llm.prompts, "LLM was never called"
        sent_prompt = llm.prompts[0]
        assert "goal" in sent_prompt
        assert "持续性目标" in sent_prompt

    @pytest.mark.asyncio
    async def test_goal_facts_false_uses_old_prompt(self, db_path: Path):
        """TG-6: When goal_facts=False (default), the LLM receives the old prompt."""
        llm = _FakeLLM(["[]"])
        store = FactsStore(db_path)
        extractor = FactExtractor(store, extract_llm=llm, goal_facts=False)
        await extractor.process_message(
            message_id=1, content="我平时很喜欢喝咖啡，尤其是早上的那一杯美式", role="user"
        )
        assert llm.prompts, "LLM was never called"
        sent_prompt = llm.prompts[0]
        assert "goal" not in sent_prompt
        assert "持续性目标" not in sent_prompt

    @pytest.mark.asyncio
    async def test_default_goal_facts_is_false(self, db_path: Path):
        """FactExtractor default goal_facts=False (BC guard)."""
        llm = _FakeLLM(["[]"])
        store = FactsStore(db_path)
        extractor = FactExtractor(store, extract_llm=llm)  # no goal_facts param
        await extractor.process_message(
            message_id=1, content="我平时最爱喝的饮料是绿茶，特别是龙井", role="user"
        )
        sent_prompt = llm.prompts[0]
        # Old prompt must not contain new categories
        assert "goal" not in sent_prompt
        assert "constraint" not in sent_prompt


# ---------------------------------------------------------------------------
# TG-3: scope column write + list_active filters by scope
# ---------------------------------------------------------------------------


class TestScopeColumn:
    @pytest.mark.asyncio
    async def test_upsert_writes_scope_user(self, db_path: Path):
        """upsert with scope='user' → row has scope='user'."""
        store = FactsStore(db_path)
        fid = await store.upsert(
            category="goal",
            subject="user",
            key="test_goal",
            value="finish module",
            confidence=0.8,
            source_msg_id=None,
            evidence="I want to finish module",
            scope="user",
        )
        row = await store.get_by_id(fid)
        assert row is not None
        assert row.get("scope") == "user"

    @pytest.mark.asyncio
    async def test_upsert_writes_scope_session(self, db_path: Path):
        """upsert with scope='session' → row has scope='session'."""
        store = FactsStore(db_path)
        fid = await store.upsert(
            category="goal",
            subject="user",
            key="session_goal_abc",
            value="finish this session task",
            confidence=0.9,
            source_msg_id=None,
            evidence="I want to finish this task",
            scope="session",
        )
        row = await store.get_by_id(fid)
        assert row is not None
        assert row.get("scope") == "session"

    @pytest.mark.asyncio
    async def test_upsert_default_scope_is_user(self, db_path: Path):
        """upsert without scope param → defaults to 'user'."""
        store = FactsStore(db_path)
        fid = await store.upsert(
            category="preference",
            subject="user",
            key="fav_drink",
            value="coffee",
            confidence=0.8,
            source_msg_id=None,
            evidence="I drink coffee",
        )
        row = await store.get_by_id(fid)
        assert row is not None
        assert row.get("scope") in ("user", None, ""), (
            f"Unexpected scope value: {row.get('scope')!r}"
        )

    @pytest.mark.asyncio
    async def test_list_active_filters_by_scope(self, db_path: Path):
        """list_active with scope='session' returns only session-scoped rows."""
        store = FactsStore(db_path)
        # Insert one user-scoped and one session-scoped
        await store.upsert(
            category="goal", subject="user", key="user_goal",
            value="long-term goal", confidence=0.8,
            source_msg_id=None, evidence="e1", scope="user",
        )
        await store.upsert(
            category="goal", subject="user", key="session_goal",
            value="this session goal", confidence=0.9,
            source_msg_id=None, evidence="e2", scope="session",
        )
        session_rows = await store.list_active(scope="session")
        assert len(session_rows) == 1
        assert session_rows[0]["key"] == "session_goal"

    @pytest.mark.asyncio
    async def test_list_active_user_scope_excludes_session(self, db_path: Path):
        """list_active with scope='user' excludes session-scoped rows."""
        store = FactsStore(db_path)
        await store.upsert(
            category="goal", subject="user", key="u_goal",
            value="user goal", confidence=0.8,
            source_msg_id=None, evidence="e1", scope="user",
        )
        await store.upsert(
            category="goal", subject="user", key="s_goal",
            value="session goal", confidence=0.9,
            source_msg_id=None, evidence="e2", scope="session",
        )
        user_rows = await store.list_active(scope="user")
        keys = {r["key"] for r in user_rows}
        assert "u_goal" in keys
        assert "s_goal" not in keys

    @pytest.mark.asyncio
    async def test_list_active_no_scope_filter_returns_all(self, db_path: Path):
        """list_active without scope= returns both user and session rows (BC)."""
        store = FactsStore(db_path)
        await store.upsert(
            category="goal", subject="user", key="u_goal2",
            value="user goal", confidence=0.8,
            source_msg_id=None, evidence="e1", scope="user",
        )
        await store.upsert(
            category="goal", subject="user", key="s_goal2",
            value="session goal", confidence=0.9,
            source_msg_id=None, evidence="e2", scope="session",
        )
        all_rows = await store.list_active()
        keys = {r["key"] for r in all_rows}
        assert "u_goal2" in keys
        assert "s_goal2" in keys


# ---------------------------------------------------------------------------
# _fact_row_to_hit prefix test
# ---------------------------------------------------------------------------


class TestFactRowToHitPrefix:
    """Verify category-specific prefix in enhanced_retriever._fact_row_to_hit."""

    def test_goal_prefix(self):
        from deskpet.memory.enhanced_retriever import _fact_row_to_hit
        row = {
            "id": 1, "category": "goal", "key": "monthly_goal",
            "value": "finish by month end", "evidence": "",
            "updated_at": 0.0, "_score": 0.9,
        }
        hit = _fact_row_to_hit(row, source="facts")
        assert hit.text.startswith("[goal]"), f"Expected [goal] prefix, got: {hit.text!r}"

    def test_decision_prefix(self):
        from deskpet.memory.enhanced_retriever import _fact_row_to_hit
        row = {
            "id": 2, "category": "decision", "key": "lang_choice",
            "value": "TypeScript", "evidence": "",
            "updated_at": 0.0,
        }
        hit = _fact_row_to_hit(row, source="facts")
        assert hit.text.startswith("[decision]"), f"Expected [decision] prefix: {hit.text!r}"

    def test_constraint_prefix(self):
        from deskpet.memory.enhanced_retriever import _fact_row_to_hit
        row = {
            "id": 3, "category": "constraint", "key": "budget",
            "value": "under 2000", "evidence": "",
            "updated_at": 0.0,
        }
        hit = _fact_row_to_hit(row, source="facts")
        assert hit.text.startswith("[constraint]"), f"Expected [constraint] prefix: {hit.text!r}"

    def test_non_new_categories_use_fact_prefix(self):
        """preference/profile/project/event still use [fact] prefix (BC)."""
        from deskpet.memory.enhanced_retriever import _fact_row_to_hit
        for cat in ("preference", "profile", "project", "event"):
            row = {
                "id": 10, "category": cat, "key": "k", "value": "v",
                "evidence": "", "updated_at": 0.0,
            }
            hit = _fact_row_to_hit(row, source="facts")
            assert hit.text.startswith("[fact]"), (
                f"Expected [fact] prefix for category={cat!r}, got: {hit.text!r}"
            )


# ---------------------------------------------------------------------------
# FP-4 TC-4.5 真机暴露 bug:B-10 钩直接调 upsert(纯 INSERT,docstring 明说
# 调用方负责冲突消解)→ 每次 /goal set 堆一行,真机 15 行全 active 不去重。
# 修复:upsert_replacing 组合 find_active→upsert→mark_superseded。
# ---------------------------------------------------------------------------
import pytest as _pytest


@_pytest.mark.asyncio
async def test_upsert_replacing_dedups_same_key(tmp_path):
    from deskpet.memory.facts import FactsStore
    fs = FactsStore(db_path=str(tmp_path / "state.db"), embedder=None)
    kw = dict(category="goal", subject="user", key="goal_s1",
              confidence=0.9, source_msg_id=None, evidence="t", scope="session")
    id1 = await fs.upsert_replacing(value="目标A", **kw)
    id2 = await fs.upsert_replacing(value="目标B", **kw)
    assert id2 != id1
    active = await fs.find_active(subject="user", key="goal_s1")
    assert active is not None and active["value"] == "目标B"
    old = await fs.get_by_id(id1)
    assert old["is_active"] == 0 and old["superseded_by"] == id2


@_pytest.mark.asyncio
async def test_upsert_replacing_first_insert_plain(tmp_path):
    from deskpet.memory.facts import FactsStore
    fs = FactsStore(db_path=str(tmp_path / "state.db"), embedder=None)
    fid = await fs.upsert_replacing(
        category="goal", subject="user", key="goal_x", value="v",
        confidence=0.9, source_msg_id=None, evidence="t", scope="session")
    row = await fs.get_by_id(fid)
    assert row["is_active"] == 1


@_pytest.mark.asyncio
async def test_upsert_replacing_heals_legacy_duplicate_actives(tmp_path):
    """修复前堆积的多条 active 同 key 行(真机 15 行)在下一次写入时自愈。

    TC-4.5 真机复验(2026-06-11)发现:原实现只 supersede find_active 的
    最新一条,旧堆积永远留 active。现 supersede 全部 active 同 key 行。
    """
    from deskpet.memory.facts import FactsStore
    import aiosqlite
    fs = FactsStore(db_path=str(tmp_path / "state.db"), embedder=None)
    kw = dict(category="goal", subject="user", key="goal_dirty",
              confidence=0.9, source_msg_id=None, evidence="t", scope="session")
    # 模拟修复前脏数据:纯 upsert 堆 3 条全 active
    ids = [await fs.upsert(value=f"旧目标{i}", **kw) for i in range(3)]
    new_id = await fs.upsert_replacing(value="新目标", **kw)
    async with aiosqlite.connect(str(tmp_path / "state.db")) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM facts WHERE key='goal_dirty' AND is_active=1")
        n_active = (await cur.fetchone())[0]
    assert n_active == 1, f"自愈后应只 1 条 active,实际 {n_active}"
    for old_id in ids:
        row = await fs.get_by_id(old_id)
        assert row["is_active"] == 0 and row["superseded_by"] == new_id
