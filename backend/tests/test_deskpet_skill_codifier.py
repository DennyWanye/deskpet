# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""WI-4.3 — 技能自创闭环 backend 测试。

覆盖：
 - detect_trigger 各分支（≥5工具/recovered/corrected/non-obvious/降级路径）
 - generate_candidate → pending entry，未落盘 SKILL.md
 - confirm(accept=true)  → SKILL.md 落盘 + reload + list_skills 可见
 - confirm(accept=false) → pending 删除，无落盘
 - requires_script=false 硬编码
 - 垃圾候选（steps<2）不被提出
 - SkillMemoryStore pending 不进 list_all/recall
 - config flag 默认 False
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from deskpet.agent.tool_path import ToolPath, ToolStep
from deskpet.skills.skill_codifier import (
    SkillCodifier,
    SkillCandidateStore,
    detect_trigger,
    render_skill_md,
)
from deskpet.memory.reflection import SkillMemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path(steps_list: list[tuple], *, recovered=False, corrected=False) -> ToolPath:
    """Build a ToolPath from a list of (name, ok) tuples."""
    steps = [
        ToolStep(name=name, ok=ok, recovered=recovered and i == 0, corrected=corrected and i == 0)
        for i, (name, ok) in enumerate(steps_list)
    ]
    return ToolPath(
        session_id="s1",
        goal_id="g1",
        goal_text="test goal",
        steps=steps,
    )


def _make_llm(candidate: dict | None) -> AsyncMock:
    """LLM call mock returning a JSON string."""
    async def _call(prompt: str) -> str:
        if candidate is None:
            return ""
        return json.dumps(candidate)
    return _call


# ---------------------------------------------------------------------------
# detect_trigger
# ---------------------------------------------------------------------------

class TestDetectTrigger:
    def test_five_or_more_tools_triggers(self):
        path = _path([("a", True), ("b", True), ("c", True), ("d", True), ("e", True)])
        assert detect_trigger(path) is True

    def test_four_calls_same_two_tools_no_trigger(self):
        """4 calls but only 2 distinct tools, no corrected/recovered, < 5 → no trigger."""
        path = _path([("a", True), ("b", True), ("a", True), ("b", True)])
        assert detect_trigger(path) is False

    def test_recovered_triggers_even_with_fewer_tools(self):
        """recovered=True on any step → trigger."""
        steps = [
            ToolStep(name="a", ok=False, recovered=True),
            ToolStep(name="a", ok=True),
            ToolStep(name="b", ok=True),
        ]
        path = ToolPath(session_id="s1", goal_id="g1", goal_text="t", steps=steps)
        assert detect_trigger(path) is True

    def test_corrected_triggers_even_with_fewer_tools(self):
        """corrected=True on any step → trigger."""
        steps = [
            ToolStep(name="a", ok=True, corrected=True),
            ToolStep(name="b", ok=True),
        ]
        path = ToolPath(session_id="s1", goal_id="g1", goal_text="t", steps=steps)
        assert detect_trigger(path) is True

    def test_non_obvious_workflow_triggers(self):
        """≥3 distinct tools in a specific multi-step order → trigger."""
        # 3 distinct tools with at least 3 steps in a specific sequence
        path = _path([("a", True), ("b", True), ("c", True)])
        assert detect_trigger(path) is True

    def test_obvious_workflow_same_tool_repeated_no_trigger(self):
        """Same tool repeated is obvious (not multi-tool workflow)."""
        path = _path([("a", True), ("a", True), ("a", True), ("a", True)])
        assert detect_trigger(path) is False

    def test_empty_path_no_trigger(self):
        path = ToolPath(session_id="s1", goal_id="g1", goal_text="t", steps=[])
        assert detect_trigger(path) is False

    def test_degraded_mode_no_corrected_recovered_only_5plus_and_non_obvious(self):
        """In degraded mode (no corrected/recovered signals), only 2 triggers apply."""
        # 4 tools, no corrected/recovered, 2 distinct tools → degraded: no trigger
        path = _path([("a", True), ("b", True), ("a", True), ("b", True)])
        assert detect_trigger(path, degraded_mode=True) is False

    def test_degraded_mode_five_tools_triggers(self):
        path = _path([("a", True)] * 5)
        assert detect_trigger(path, degraded_mode=True) is True

    def test_degraded_mode_corrected_ignored(self):
        """degraded_mode=True: corrected signal ignored (only 2 triggers active)."""
        steps = [
            ToolStep(name="a", ok=True, corrected=True),
            ToolStep(name="b", ok=True),
        ]
        path = ToolPath(session_id="s1", goal_id="g1", goal_text="t", steps=steps)
        assert detect_trigger(path, degraded_mode=True) is False


# ---------------------------------------------------------------------------
# render_skill_md
# ---------------------------------------------------------------------------

class TestRenderSkillMd:
    def test_requires_script_is_always_false(self):
        candidate = {
            "name": "my-skill",
            "description": "does stuff",
            "trigger_pattern": "when user wants X",
            "steps": ["step 1", "step 2"],
        }
        md = render_skill_md(candidate)
        assert "requires_script: false" in md

    def test_frontmatter_fields_present(self):
        candidate = {
            "name": "weather-ppt",
            "description": "查天气并生成PPT",
            "trigger_pattern": "user wants weather report as PPT",
            "steps": ["check weather", "generate ppt"],
        }
        md = render_skill_md(candidate)
        assert "name: weather-ppt" in md
        assert "description:" in md
        assert "author: self-codified" in md
        assert "when_to_use:" in md

    def test_body_contains_steps(self):
        candidate = {
            "name": "x",
            "description": "d",
            "trigger_pattern": "t",
            "steps": ["first step", "second step"],
        }
        md = render_skill_md(candidate)
        assert "first step" in md
        assert "second step" in md

    def test_version_field_present(self):
        candidate = {
            "name": "x",
            "description": "d",
            "trigger_pattern": "t",
            "steps": ["a", "b"],
        }
        md = render_skill_md(candidate)
        assert "version:" in md


# ---------------------------------------------------------------------------
# SkillCandidateStore — pending CRUD, not entering list_all/recall
# ---------------------------------------------------------------------------

class TestSkillCandidateStore:
    @pytest.fixture
    def tmp_db(self, tmp_path):
        return str(tmp_path / "test.db")

    @pytest.mark.asyncio
    async def test_write_and_fetch_pending(self, tmp_db):
        store = SkillCandidateStore(tmp_db)
        candidate = {
            "name": "test-skill",
            "description": "a test",
            "trigger_pattern": "when X",
            "steps": ["step1", "step2"],
        }
        cid = await store.write_pending(candidate)
        assert cid > 0
        fetched = await store.fetch_pending(cid)
        assert fetched is not None
        assert fetched["name"] == "test-skill"
        assert fetched["status"] == "pending"

    @pytest.mark.asyncio
    async def test_delete_pending(self, tmp_db):
        store = SkillCandidateStore(tmp_db)
        candidate = {"name": "x", "description": "d", "trigger_pattern": "t", "steps": ["a", "b"]}
        cid = await store.write_pending(candidate)
        deleted = await store.delete_pending(cid)
        assert deleted is True
        fetched = await store.fetch_pending(cid)
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, tmp_db):
        store = SkillCandidateStore(tmp_db)
        deleted = await store.delete_pending(99999)
        assert deleted is False

    @pytest.mark.asyncio
    async def test_pending_not_in_skill_memory_list_all(self, tmp_db):
        """pending candidates must NOT appear in SkillMemoryStore.list_all()."""
        skill_store = SkillMemoryStore(tmp_db)
        candidate_store = SkillCandidateStore(tmp_db)
        await candidate_store.write_pending(
            {"name": "pending-skill", "description": "d", "trigger_pattern": "t", "steps": ["a", "b"]}
        )
        all_skills = await skill_store.list_all()
        names = [s["name"] for s in all_skills]
        assert "pending-skill" not in names

    @pytest.mark.asyncio
    async def test_pending_not_in_skill_memory_recall(self, tmp_db):
        """pending candidates must NOT appear in SkillMemoryStore.recall()."""
        skill_store = SkillMemoryStore(tmp_db)
        candidate_store = SkillCandidateStore(tmp_db)
        await candidate_store.write_pending(
            {"name": "pending-recall-test", "description": "recall test", "trigger_pattern": "t", "steps": ["a", "b"]}
        )
        results = await skill_store.recall("pending-recall-test")
        names = [s["name"] for s in results]
        assert "pending-recall-test" not in names


# ---------------------------------------------------------------------------
# SkillCodifier — generate_candidate + full confirm flow
# ---------------------------------------------------------------------------

class TestSkillCodifier:
    @pytest.fixture
    def tmp_path_with_dirs(self, tmp_path):
        """Setup temp dirs for skills + DB."""
        (tmp_path / "skills" / "user").mkdir(parents=True)
        return tmp_path

    @pytest.fixture
    def skill_dirs(self, tmp_path_with_dirs):
        return [
            tmp_path_with_dirs / "skills" / "built-in",
            tmp_path_with_dirs / "skills" / "user",
        ]

    @pytest.mark.asyncio
    async def test_generate_candidate_returns_pending_entry(self, tmp_path_with_dirs):
        """6 tools + 1 recovered step → detect_trigger True; pending written, no SKILL.md."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        candidate_store = SkillCandidateStore(db_path)
        skill_dirs = [tmp_path_with_dirs / "skills" / "user"]

        good_candidate = {
            "name": "multi-step-workflow",
            "description": "Executes a multi-step data workflow",
            "trigger_pattern": "when user needs multi-step data processing",
            "steps": ["fetch data", "process data", "save result"],
        }
        llm = _make_llm(good_candidate)

        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=skill_dirs[-1],
            llm_call=llm,
        )

        # Build path: 6 tools including one recovered step
        steps = [
            ToolStep(name="web_search", ok=True),
            ToolStep(name="file_read", ok=False, recovered=True),
            ToolStep(name="file_read", ok=True),
            ToolStep(name="ppt_create", ok=True),
            ToolStep(name="file_write", ok=True),
            ToolStep(name="send_email", ok=True),
        ]
        path = ToolPath(session_id="s1", goal_id="g1", goal_text="complex workflow", steps=steps)

        cid = await codifier.propose(path)
        assert cid is not None
        assert cid > 0

        # Pending entry exists
        pending = await candidate_store.fetch_pending(cid)
        assert pending is not None
        assert pending["name"] == "multi-step-workflow"
        assert pending["status"] == "pending"

        # SKILL.md NOT written yet
        skill_md = tmp_path_with_dirs / "skills" / "user" / "multi-step-workflow" / "SKILL.md"
        assert not skill_md.exists()

    @pytest.mark.asyncio
    async def test_confirm_accept_writes_skill_md_and_reloads(self, tmp_path_with_dirs):
        """confirm(accept=true) → SKILL.md written, reload called, skill in list_skills."""
        from deskpet.skills.loader import SkillLoader

        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"
        user_dir.mkdir(parents=True, exist_ok=True)

        candidate_store = SkillCandidateStore(db_path)
        skill_loader = SkillLoader(
            skill_dirs=[tmp_path_with_dirs / "skills" / "built-in", user_dir],
            enable_watch=False,
        )
        await skill_loader.start()

        good_candidate = {
            "name": "confirm-skill",
            "description": "A confirmed skill",
            "trigger_pattern": "when user needs X",
            "steps": ["step one", "step two"],
        }
        llm = _make_llm(good_candidate)

        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=llm,
            skill_loader=skill_loader,
        )

        # Write a candidate manually
        cid = await candidate_store.write_pending(good_candidate)

        # Confirm accept=True
        result = await codifier.confirm(cid, accept=True)
        assert result is True

        # SKILL.md written
        skill_md = user_dir / "confirm-skill" / "SKILL.md"
        assert skill_md.exists()
        content = skill_md.read_text(encoding="utf-8")
        assert "requires_script: false" in content
        assert "confirm-skill" in content

        # Skill appears in list_skills (returns dicts)
        skills = skill_loader.list_skills()
        assert any(s["name"] == "confirm-skill" for s in skills)

        # Pending entry deleted
        assert await candidate_store.fetch_pending(cid) is None

    @pytest.mark.asyncio
    async def test_confirm_reject_deletes_pending_no_file(self, tmp_path_with_dirs):
        """confirm(accept=false) → pending deleted, no SKILL.md."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"
        user_dir.mkdir(parents=True, exist_ok=True)

        candidate_store = SkillCandidateStore(db_path)
        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=_make_llm(None),
        )

        candidate = {
            "name": "reject-skill",
            "description": "To be rejected",
            "trigger_pattern": "never",
            "steps": ["a", "b"],
        }
        cid = await candidate_store.write_pending(candidate)

        result = await codifier.confirm(cid, accept=False)
        assert result is True

        # No file
        skill_md = user_dir / "reject-skill" / "SKILL.md"
        assert not skill_md.exists()

        # Pending deleted
        assert await candidate_store.fetch_pending(cid) is None

    @pytest.mark.asyncio
    async def test_no_trigger_for_few_obvious_tools(self, tmp_path_with_dirs):
        """<5 tools + obvious (single tool) → propose returns None."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"

        candidate_store = SkillCandidateStore(db_path)
        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=_make_llm({"name": "x", "description": "d", "trigger_pattern": "t", "steps": ["a", "b"]}),
        )
        # Only 2 tools, same name — no trigger
        path = _path([("a", True), ("a", True)])
        result = await codifier.propose(path)
        assert result is None

    @pytest.mark.asyncio
    async def test_garbage_candidate_steps_lt2_not_proposed(self, tmp_path_with_dirs):
        """LLM returns candidate with steps<2 → not proposed (returns None)."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"

        bad_candidate = {
            "name": "bad",
            "description": "desc",
            "trigger_pattern": "t",
            "steps": ["only one step"],
        }
        candidate_store = SkillCandidateStore(db_path)
        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=_make_llm(bad_candidate),
        )
        # 5+ tools to trigger detection
        path = _path([("a", True), ("b", True), ("c", True), ("d", True), ("e", True)])
        result = await codifier.propose(path)
        assert result is None

    @pytest.mark.asyncio
    async def test_garbage_candidate_empty_name_not_proposed(self, tmp_path_with_dirs):
        """LLM returns candidate with empty name → not proposed."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"

        bad_candidate = {
            "name": "",
            "description": "desc",
            "trigger_pattern": "t",
            "steps": ["step1", "step2"],
        }
        candidate_store = SkillCandidateStore(db_path)
        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=_make_llm(bad_candidate),
        )
        path = _path([("a", True), ("b", True), ("c", True), ("d", True), ("e", True)])
        result = await codifier.propose(path)
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_returns_empty_string_not_proposed(self, tmp_path_with_dirs):
        """LLM returns empty → not proposed."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"

        candidate_store = SkillCandidateStore(db_path)
        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=_make_llm(None),
        )
        path = _path([("a", True), ("b", True), ("c", True), ("d", True), ("e", True)])
        result = await codifier.propose(path)
        assert result is None

    @pytest.mark.asyncio
    async def test_requires_script_always_false_in_written_skill(self, tmp_path_with_dirs):
        """Even if LLM somehow emits requires_script: true, output must be false."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"
        user_dir.mkdir(parents=True, exist_ok=True)

        from deskpet.skills.loader import SkillLoader
        skill_loader = SkillLoader(
            skill_dirs=[user_dir],
            enable_watch=False,
        )
        await skill_loader.start()

        # Candidate with requires_script: true (should be overridden)
        candidate = {
            "name": "danger-skill",
            "description": "should not exec",
            "trigger_pattern": "never exec",
            "steps": ["safe step 1", "safe step 2"],
            "requires_script": True,  # codifier must ignore this
        }
        candidate_store = SkillCandidateStore(db_path)
        cid = await candidate_store.write_pending(candidate)

        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=_make_llm(candidate),
            skill_loader=skill_loader,
        )
        await codifier.confirm(cid, accept=True)

        skill_md = user_dir / "danger-skill" / "SKILL.md"
        assert skill_md.exists()
        content = skill_md.read_text(encoding="utf-8")
        assert "requires_script: false" in content
        assert "requires_script: true" not in content

    @pytest.mark.asyncio
    async def test_duplicate_name_gets_suffix(self, tmp_path_with_dirs):
        """If slug already exists, codifier writes to slug-v2 dir."""
        db_path = str(tmp_path_with_dirs / "mem.db")
        user_dir = tmp_path_with_dirs / "skills" / "user"
        user_dir.mkdir(parents=True, exist_ok=True)

        from deskpet.skills.loader import SkillLoader
        skill_loader = SkillLoader(skill_dirs=[user_dir], enable_watch=False)
        await skill_loader.start()

        # Pre-create an existing skill dir
        existing_dir = user_dir / "existing-skill"
        existing_dir.mkdir()
        (existing_dir / "SKILL.md").write_text(
            "---\nname: existing-skill\ndescription: d\nversion: 0.1\nauthor: me\n---\nbody\n",
            encoding="utf-8",
        )

        candidate = {
            "name": "existing-skill",
            "description": "duplicate",
            "trigger_pattern": "t",
            "steps": ["a", "b"],
        }
        candidate_store = SkillCandidateStore(db_path)
        cid = await candidate_store.write_pending(candidate)

        codifier = SkillCodifier(
            candidate_store=candidate_store,
            user_skill_dir=user_dir,
            llm_call=_make_llm(candidate),
            skill_loader=skill_loader,
        )
        result = await codifier.confirm(cid, accept=True)
        assert result is True

        # existing-skill dir untouched, new one is existing-skill-v2
        v2_md = user_dir / "existing-skill-v2" / "SKILL.md"
        assert v2_md.exists()


# ---------------------------------------------------------------------------
# Config flag default
# ---------------------------------------------------------------------------

class TestConfigFlag:
    def test_skills_codify_enabled_default_false(self):
        from config import AppConfig
        cfg = AppConfig()
        # Access skills.codify.enabled
        codify = getattr(cfg.skills, "codify", None)
        assert codify is not None
        assert codify.enabled is False

    def test_max_candidates_per_day_default(self):
        from config import AppConfig
        cfg = AppConfig()
        codify = cfg.skills.codify
        assert codify.max_candidates_per_day == 3


# ---------------------------------------------------------------------------
# WS Future-await wiring (unit-level — no live websocket needed)
# ---------------------------------------------------------------------------

class TestSkillCandidateWaiters:
    """Verify the _SKILL_CANDIDATE_WAITERS pattern from main.py resolves correctly."""

    @pytest.mark.asyncio
    async def test_future_resolves_on_confirm(self):
        """Simulate the future-await pattern used in main.py for skill_candidate_confirm."""
        from deskpet.skills.skill_codifier import SkillCandidateWaiters

        waiters = SkillCandidateWaiters()
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        waiters.add(42, fut)

        # Simulate WS handler calling resolve
        waiters.resolve(42, "accept")

        assert fut.done()
        assert fut.result() == "accept"

    @pytest.mark.asyncio
    async def test_future_resolve_nonexistent_is_noop(self):
        """Resolving a non-existent candidate id is a no-op (no exception)."""
        from deskpet.skills.skill_codifier import SkillCandidateWaiters

        waiters = SkillCandidateWaiters()
        # Should not raise
        waiters.resolve(999, "accept")

    @pytest.mark.asyncio
    async def test_future_pop_removes_waiter(self):
        from deskpet.skills.skill_codifier import SkillCandidateWaiters

        loop = asyncio.get_event_loop()
        waiters = SkillCandidateWaiters()
        fut: asyncio.Future = loop.create_future()
        waiters.add(1, fut)
        waiters.resolve(1, "reject")
        # After resolve, the waiter should be gone
        assert waiters.get(1) is None
