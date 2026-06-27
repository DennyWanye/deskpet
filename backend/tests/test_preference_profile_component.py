# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FP-4 WI-3.2 — PreferenceProfileComponent + B-10 双写钩 tests.

TG-1  facts 有 preference/profile → Slice 含画像块、priority=85、bucket=dynamic。
TG-2  facts_store=None → 空 Slice，不抛。
TG-3  Pin 项置顶 + 带 📌 标记。
TG-4  flag=False → component 不进 prefer / 返回空（字节级回归）。
TG-5  渲染不含任何粘性/讨好措辞（断言关键词黑名单）。
TG-6  双写钩：goal_store.set → bound callback fires → facts upsert category=goal
      (key=goal_<sid>, scope=session)；goal_store 不 import facts；
      同 session 重设 → replace not pile-up。
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.bundle import AssemblyPolicy, Slice
from deskpet.agent.assembler.components.preference_profile import (
    PreferenceProfileComponent,
)
from deskpet.agent.goal_store import SessionGoalStore


# ---------------------------------------------------------------------------
# Fake facts stores
# ---------------------------------------------------------------------------

class FakeFactsStore:
    """Minimal fake that lets tests control list_active return values."""

    def __init__(self, rows_by_category: dict[str, list[dict]] | None = None) -> None:
        self._rows = rows_by_category or {}
        self.upsert_calls: list[dict] = []

    async def list_active(
        self,
        *,
        subject: str | None = None,
        category: str | None = None,
        scope: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if category is not None:
            return self._rows.get(category, [])[:limit]
        # no filter — return all rows
        all_rows = []
        for rows in self._rows.values():
            all_rows.extend(rows)
        return all_rows[:limit]

    async def upsert(self, **kwargs) -> int:
        self.upsert_calls.append(kwargs)
        return len(self.upsert_calls)

    async def find_active(self, *, subject: str, key: str):
        """find_active used for dedup check — return None to force new insert."""
        return None


def _make_ctx(**kwargs) -> ComponentContext:
    defaults = dict(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat"),
        user_message="hi",
        session_id="sess-001",
    )
    defaults.update(kwargs)
    return ComponentContext(**defaults)


def _pref_row(key: str, value: str, confidence: float = 0.9, pinned: int = 0) -> dict:
    return {
        "id": 1,
        "category": "preference",
        "subject": "user",
        "key": key,
        "value": value,
        "confidence": confidence,
        "is_active": 1,
        "pinned": pinned,
        "updated_at": 1.0,
    }


def _profile_row(key: str, value: str, confidence: float = 0.8) -> dict:
    return {
        "id": 2,
        "category": "profile",
        "subject": "user",
        "key": key,
        "value": value,
        "confidence": confidence,
        "is_active": 1,
        "pinned": 0,
        "updated_at": 0.5,
    }


# ===========================================================================
# TG-1: facts 有 preference/profile → Slice 含画像块、priority=85、bucket=dynamic
# ===========================================================================

@pytest.mark.asyncio
async def test_tg1_basic_slice_shape():
    """TG-1: With pref + profile rows, Slice has right priority/bucket/content."""
    store = FakeFactsStore(
        rows_by_category={
            "preference": [_pref_row("饮料", "乌龙茶")],
            "profile": [_profile_row("称呼", "老王")],
        }
    )
    comp = PreferenceProfileComponent(store=store)
    ctx = _make_ctx()
    result = await comp.provide(ctx)

    assert isinstance(result, Slice)
    assert result.priority == 85
    assert result.bucket == "dynamic"
    assert result.text_content, "Slice text must not be empty when rows exist"
    assert "用户画像" in result.text_content or "PROFILE" in result.text_content
    assert "乌龙茶" in result.text_content
    assert "老王" in result.text_content


@pytest.mark.asyncio
async def test_tg1_slice_component_name():
    """TG-1: component_name == 'preference_profile'."""
    store = FakeFactsStore(rows_by_category={"preference": [_pref_row("k", "v")]})
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    assert result.component_name == "preference_profile"


@pytest.mark.asyncio
async def test_tg1_preference_label_in_text():
    """TG-1: Preference rows are prefixed with [preference]."""
    store = FakeFactsStore(rows_by_category={"preference": [_pref_row("饮料", "绿茶")]})
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    assert "[preference]" in result.text_content


@pytest.mark.asyncio
async def test_tg1_profile_label_in_text():
    """TG-1: Profile rows are prefixed with [profile]."""
    store = FakeFactsStore(rows_by_category={"profile": [_profile_row("name", "小明")]})
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    assert "[profile]" in result.text_content


# ===========================================================================
# TG-2: facts_store=None → 空 Slice，不抛
# ===========================================================================

@pytest.mark.asyncio
async def test_tg2_none_store_no_raise():
    """TG-2: store=None → empty Slice, status=no_store, no exception."""
    comp = PreferenceProfileComponent(store=None)
    result = await comp.provide(_make_ctx())
    assert isinstance(result, Slice)
    assert result.text_content == "" or result.text_content is None or not result.text_content
    assert result.meta.get("status") == "no_store"


@pytest.mark.asyncio
async def test_tg2_none_store_priority_and_bucket():
    """TG-2: Empty slice still carries priority=85, bucket=dynamic."""
    comp = PreferenceProfileComponent(store=None)
    result = await comp.provide(_make_ctx())
    assert result.priority == 85
    assert result.bucket == "dynamic"


@pytest.mark.asyncio
async def test_tg2_empty_store_no_raise():
    """TG-2 variant: store has no active rows → empty Slice, no exception."""
    store = FakeFactsStore(rows_by_category={})
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    assert isinstance(result, Slice)
    # empty rows → no text block emitted (don't waste tokens)
    assert not result.text_content or result.text_content.strip() == ""


# ===========================================================================
# TG-3: Pin 项置顶 + 带 📌 标记
# ===========================================================================

@pytest.mark.asyncio
async def test_tg3_pinned_item_marker():
    """TG-3: Pinned fact has 📌 marker in output."""
    store = FakeFactsStore(
        rows_by_category={
            "preference": [
                _pref_row("编辑器", "neovim", confidence=0.95, pinned=1),
                _pref_row("饮料", "咖啡", confidence=0.8, pinned=0),
            ]
        }
    )
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    assert "📌" in result.text_content, "Pinned item must have 📌 marker"


@pytest.mark.asyncio
async def test_tg3_pinned_item_first():
    """TG-3: Pinned item appears before non-pinned in output."""
    store = FakeFactsStore(
        rows_by_category={
            "preference": [
                # non-pinned first in the list (lower index)
                _pref_row("饮料", "咖啡", confidence=0.8, pinned=0),
                _pref_row("编辑器", "neovim", confidence=0.95, pinned=1),
            ]
        }
    )
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    idx_pinned = result.text_content.find("neovim")
    idx_normal = result.text_content.find("咖啡")
    assert idx_pinned < idx_normal, "Pinned item should appear before normal items"


@pytest.mark.asyncio
async def test_tg3_non_pinned_no_marker():
    """TG-3: Non-pinned items have no 📌 on their line."""
    store = FakeFactsStore(
        rows_by_category={
            "preference": [_pref_row("饮料", "可乐", confidence=0.7, pinned=0)]
        }
    )
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    # The line about 可乐 should not contain 📌
    for line in result.text_content.splitlines():
        if "可乐" in line:
            assert "📌" not in line, "Non-pinned item should not have 📌"


# ===========================================================================
# TG-4: flag=False → component 不进 prefer / 返回空（字节级回归）
# ===========================================================================

@pytest.mark.asyncio
async def test_tg4_flag_off_returns_empty_slice():
    """TG-4: When flag=False the component returns empty Slice without rendering."""
    store = FakeFactsStore(
        rows_by_category={"preference": [_pref_row("k", "v")]}
    )
    # flag_enabled=False mimics the persona_inject=False config gate
    comp = PreferenceProfileComponent(store=store, flag_enabled=False)
    result = await comp.provide(_make_ctx())
    assert not result.text_content or result.text_content.strip() == ""
    assert result.meta.get("status") == "flag_off"


@pytest.mark.asyncio
async def test_tg4_flag_on_renders_content():
    """TG-4: flag=True (default) renders content normally."""
    store = FakeFactsStore(
        rows_by_category={"preference": [_pref_row("饮料", "茶")]}
    )
    comp = PreferenceProfileComponent(store=store, flag_enabled=True)
    result = await comp.provide(_make_ctx())
    assert "茶" in result.text_content


def test_tg4_build_default_assembler_has_component():
    """TG-4: build_default_assembler accepts facts_store param and registers component."""
    from deskpet.agent.assembler import build_default_assembler
    # Should not raise when facts_store is None (flag_off path)
    assembler = build_default_assembler(facts_store=None)
    # Registry should contain "preference_profile"
    names = assembler._registry.names()
    assert "preference_profile" in names


# ===========================================================================
# TG-5: 渲染不含任何粘性/讨好措辞（关键词黑名单）
# ===========================================================================

_SYCOPHANCY_BLACKLIST = [
    "讨好",
    "最大化粘性",
    "延长在线",
    "取悦",
    "增加粘性",
    "留住用户",
    "engagement",
    "maximize",
    "stickiness",
    "flatter",
]


@pytest.mark.asyncio
async def test_tg5_no_sycophancy_keywords():
    """TG-5: Rendered output must not contain any sycophancy/manipulation wording."""
    store = FakeFactsStore(
        rows_by_category={
            "preference": [
                _pref_row("饮料", "乌龙茶"),
                _pref_row("编辑器", "neovim", pinned=1),
            ],
            "profile": [_profile_row("称呼", "老王")],
        }
    )
    comp = PreferenceProfileComponent(store=store)
    result = await comp.provide(_make_ctx())
    text_lower = result.text_content.lower()
    for keyword in _SYCOPHANCY_BLACKLIST:
        assert keyword.lower() not in text_lower, (
            f"Sycophancy keyword '{keyword}' found in rendered profile block. "
            "Profile must describe facts only, no manipulation instructions."
        )


# ===========================================================================
# TG-6: 双写钩 (B-10) tests
# ===========================================================================

@pytest.mark.asyncio
async def test_tg6_bind_on_goal_set_fires_callback():
    """TG-6: After bind_on_goal_set, calling set() fires the callback."""
    store = SessionGoalStore()
    callback_calls: list[tuple[str, str]] = []

    async def fake_callback(session_id: str, text: str) -> None:
        callback_calls.append((session_id, text))

    store.bind_on_goal_set(fake_callback)
    goal = store.set("sess-abc", "写完月报")
    # Need to await the background task if async
    # Give event loop a tick to process
    await asyncio.sleep(0)

    assert len(callback_calls) == 1
    assert callback_calls[0] == ("sess-abc", "写完月报")


@pytest.mark.asyncio
async def test_tg6_fanout_task_retained_until_done():
    """TG-6 GC-safe: fire-and-forget fanout task is held by a strong ref
    while pending (防 asyncio GC 在首个 await 挂起期回收 → 真机 facts 表静默为空根因)，
    并在完成后从 _fanout_tasks discard。"""
    import asyncio as _aio
    store = SessionGoalStore()
    started = _aio.Event()
    release = _aio.Event()
    done: list[bool] = []

    async def slow_callback(sid: str, text: str) -> None:
        started.set()
        await release.wait()  # 模拟 upsert 在 await 处挂起
        done.append(True)

    store.bind_on_goal_set(slow_callback)
    store.set("sess-gc", "目标")
    await started.wait()
    # 挂起期间：task 必须被强引用保留，否则可能被 GC
    assert len(store._fanout_tasks) == 1, "fanout task 未被保留 → GC 风险"
    release.set()
    await _aio.sleep(0)
    await _aio.sleep(0)
    assert done == [True]
    # 完成后 done_callback 应已 discard
    assert len(store._fanout_tasks) == 0, "完成后未从 _fanout_tasks 移除"


@pytest.mark.asyncio
async def test_tg6_callback_receives_correct_args():
    """TG-6: Callback receives (session_id, text) matching the set() call."""
    store = SessionGoalStore()
    received: list[tuple] = []

    async def capture(sid: str, text: str) -> None:
        received.append((sid, text))

    store.bind_on_goal_set(capture)
    store.set("sess-xyz", "月底完成 PPT 模块")
    await asyncio.sleep(0)

    assert received == [("sess-xyz", "月底完成 PPT 模块")]


@pytest.mark.asyncio
async def test_tg6_callback_safe_fail_does_not_block_set():
    """TG-6: If callback raises, set() still returns a valid goal (safe-fail)."""
    store = SessionGoalStore()

    async def bad_callback(sid: str, text: str) -> None:
        raise RuntimeError("simulated facts upsert error")

    store.bind_on_goal_set(bad_callback)
    # Should not raise even though callback raises
    goal = store.set("sess-fail", "some goal")
    await asyncio.sleep(0)  # allow callback to fire
    assert goal is not None
    assert goal.text == "some goal"


@pytest.mark.asyncio
async def test_tg6_no_callback_set_still_works():
    """TG-6: Without bind_on_goal_set, set() still works (BC)."""
    store = SessionGoalStore()
    goal = store.set("sess-no-cb", "goal text")
    assert goal.text == "goal text"


@pytest.mark.asyncio
async def test_tg6_same_session_reset_single_callback_call():
    """TG-6: Same session set() twice → callback fires twice (not pile-up in facts)."""
    store = SessionGoalStore()
    calls: list[tuple] = []

    async def capture(sid: str, text: str) -> None:
        calls.append((sid, text))

    store.bind_on_goal_set(capture)
    store.set("sess-replace", "第一个目标")
    await asyncio.sleep(0)
    store.set("sess-replace", "第二个目标")
    await asyncio.sleep(0)

    # Both calls fired — the facts side dedup is done by find_active key match
    assert len(calls) == 2
    assert calls[0] == ("sess-replace", "第一个目标")
    assert calls[1] == ("sess-replace", "第二个目标")


def test_tg6_goal_store_does_not_import_facts():
    """TG-6: goal_store module must NOT import deskpet.memory.facts (prevents import cycle)."""
    import deskpet.agent.goal_store as gs_module

    # Inspect source or check the module's __dict__ for any facts import
    source = inspect.getsource(gs_module)
    assert "from deskpet.memory.facts" not in source, (
        "goal_store.py must not import deskpet.memory.facts directly. "
        "Use injected callback to prevent agent←memory import cycle."
    )
    assert "import deskpet.memory.facts" not in source, (
        "goal_store.py must not import deskpet.memory.facts directly."
    )
    # Also check that the module's loaded dependencies don't include facts
    # (indirect import through module's own sys.modules footprint not tested here —
    # source inspection is sufficient to guarantee no static import cycle)


@pytest.mark.asyncio
async def test_tg6_facts_upsert_called_with_goal_category():
    """TG-6: main.py wiring — callback calls upsert with category='goal', scope='session'."""
    # Simulate what main.py's lifespan does: bind_on_goal_set with a facts upsert closure
    store = SessionGoalStore()
    fake_facts = FakeFactsStore()

    async def _goal_to_facts(sid: str, text: str) -> None:
        """Closure mirroring main.py lifespan wiring."""
        try:
            await fake_facts.upsert(
                category="goal",
                subject="user",
                key=f"goal_{sid}",
                value=text,
                confidence=0.9,
                source_msg_id=None,
                evidence=f"goal set for session {sid}",
                scope="session",
            )
        except Exception:  # noqa: BLE001
            pass

    store.bind_on_goal_set(_goal_to_facts)
    store.set("sess-wire", "做完 WI-3.2")
    await asyncio.sleep(0)

    assert len(fake_facts.upsert_calls) == 1
    call = fake_facts.upsert_calls[0]
    assert call["category"] == "goal"
    assert call["scope"] == "session"
    assert call["key"] == "goal_sess-wire"
    assert call["value"] == "做完 WI-3.2"


@pytest.mark.asyncio
async def test_tg6_same_key_replace_not_pile_up():
    """TG-6: Resetting goal for same session uses same key → facts side can dedup."""
    store = SessionGoalStore()
    fake_facts = FakeFactsStore()
    upsert_keys: list[str] = []

    async def _goal_to_facts(sid: str, text: str) -> None:
        key = f"goal_{sid}"
        upsert_keys.append(key)
        await fake_facts.upsert(
            category="goal", subject="user", key=key, value=text,
            confidence=0.9, source_msg_id=None, evidence="", scope="session",
        )

    store.bind_on_goal_set(_goal_to_facts)
    store.set("sess-dedup", "目标一")
    await asyncio.sleep(0)
    store.set("sess-dedup", "目标二（更新）")
    await asyncio.sleep(0)

    # Both calls use the same key — facts upsert is called twice with same key
    assert upsert_keys == ["goal_sess-dedup", "goal_sess-dedup"]
    # The facts store's own merge/replace logic handles dedup — verified via same key


@pytest.mark.asyncio
async def test_tg6_no_callback_no_attribute_error():
    """TG-6: bind_on_goal_set is optional — no AttributeError if not called."""
    store = SessionGoalStore()
    # Just call set without binding — must work
    g = store.set("sess-plain", "plain goal")
    assert g is not None


# ===========================================================================
# WI-CC-5: auto-memory learnings 注入（include_learnings flag）
# ===========================================================================


def _learning_row(key: str, value: str, confidence: float = 0.6) -> dict:
    return {
        "id": 9,
        "category": "learning",
        "subject": "user",
        "key": key,
        "value": value,
        "confidence": confidence,
        "is_active": 1,
        "pinned": 0,
        "updated_at": 2.0,
    }


@pytest.mark.asyncio
async def test_cc5_learning_not_injected_by_default():
    """CC-5 BC: include_learnings 默认 False → learning 行不被取/不注入。"""
    store = FakeFactsStore(
        rows_by_category={
            "preference": [_pref_row("饮料", "乌龙茶")],
            "learning": [_learning_row("ppt_theme", "上次 PPT 要深色主题")],
        }
    )
    comp = PreferenceProfileComponent(store=store)  # include_learnings 默认 False
    result = await comp.provide(_make_ctx())

    assert "乌龙茶" in result.text_content
    # learning 行不应出现（fetch 集合不含 learning）
    assert "深色主题" not in result.text_content


@pytest.mark.asyncio
async def test_cc5_learning_injected_when_flag_on():
    """CC-5: include_learnings=True → learning 行进画像块（注入）。"""
    store = FakeFactsStore(
        rows_by_category={
            "preference": [_pref_row("饮料", "乌龙茶")],
            "learning": [_learning_row("ppt_theme", "上次 PPT 要深色主题")],
        }
    )
    comp = PreferenceProfileComponent(store=store, include_learnings=True)
    result = await comp.provide(_make_ctx())

    assert "乌龙茶" in result.text_content
    assert "深色主题" in result.text_content
    assert "[learning]" in result.text_content


@pytest.mark.asyncio
async def test_cc5_flag_off_fetch_set_unchanged():
    """CC-5 BC: flag OFF 时 list_active 只被三个基础 category 调用（不查 learning）。"""
    calls: list[str] = []

    class _RecordingStore(FakeFactsStore):
        async def list_active(self, *, category=None, **kw):
            calls.append(category)
            return await super().list_active(category=category, **kw)

    store = _RecordingStore(rows_by_category={"preference": [_pref_row("k", "v")]})
    comp = PreferenceProfileComponent(store=store)  # OFF
    await comp.provide(_make_ctx())

    assert "learning" not in calls
    assert set(calls) == {"preference", "profile", "constraint"}
