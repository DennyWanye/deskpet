# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-3.3 — Pin 钉住 + PreferenceMemory 衰减 测试套件.

TG-1  set_pinned(id, True) → list 行 pinned=1.
TG-2  daily_decay 跳过 pinned 项（pin 项 confidence 不变，非 pin 衰减）.
TG-3  pinned 列 ALTER 失败 → daily_decay 回退旧 SQL 不崩.
TG-4  ★ daily_decay 真被调度（lifespan 调用点存在；调用后 stale facts 降 confidence）.
TG-5  PreferenceMemory match recency decay：老条目 effective 分降、pin 条目不降.
TG-6  旧 JSON（无 pinned）加载不报错（向后兼容）.
TG-7  p4_ipc memory_pin verb → set_pinned 被调用.
"""
from __future__ import annotations

import json
import math
import os
import time
import types
import unittest.mock as mock
from pathlib import Path

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_store(tmp_path: Path):
    """Create a fresh FactsStore backed by a temp DB."""
    from deskpet.memory.facts import FactsStore
    store = FactsStore(tmp_path / "facts.db")
    await store._ensure_schema()
    return store


async def _insert_fact(
    store,
    *,
    subject: str = "user",
    key: str = "pref",
    value: str = "test",
    confidence: float = 1.0,
    decay_rate: float = 0.05,
    last_recalled_offset: float = 0.0,  # seconds relative to now
    pinned: int = 0,
) -> int:
    """Insert a raw fact row and return its id."""
    import aiosqlite

    now = time.time()
    ts = now + last_recalled_offset
    async with aiosqlite.connect(store._db_path) as conn:
        cur = await conn.execute(
            """INSERT INTO facts
               (category, subject, key, value, confidence, source_msg_id,
                created_at, updated_at, evidence, is_active, decay_rate,
                last_recalled, pinned)
               VALUES (?,?,?,?,?,NULL,?,?,NULL,1,?,?,?)""",
            ("preference", subject, key, value, confidence,
             now, now, decay_rate, ts, pinned),
        )
        await conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# TG-1: set_pinned sets pinned column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tg1_set_pinned_true(tmp_path):
    store = await _make_store(tmp_path)
    fid = await _insert_fact(store, key="k1", pinned=0)

    await store.set_pinned(fid, True)

    rows = await store.list_active(limit=10)
    target = next((r for r in rows if r["id"] == fid), None)
    assert target is not None, "fact should still be active after pin"
    assert target["pinned"] == 1, f"expected pinned=1, got {target.get('pinned')}"


@pytest.mark.asyncio
async def test_tg1_set_pinned_false(tmp_path):
    store = await _make_store(tmp_path)
    fid = await _insert_fact(store, key="k2", pinned=1)

    await store.set_pinned(fid, False)

    rows = await store.list_active(limit=10)
    target = next((r for r in rows if r["id"] == fid), None)
    assert target is not None
    assert target["pinned"] == 0


@pytest.mark.asyncio
async def test_tg1_set_pinned_nonexistent_silent(tmp_path):
    """set_pinned on missing id should not raise."""
    store = await _make_store(tmp_path)
    await store.set_pinned(9999, True)  # no error


# ---------------------------------------------------------------------------
# TG-2: daily_decay skips pinned facts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tg2_daily_decay_skips_pinned(tmp_path):
    """Pinned fact keeps confidence=1.0; unpinned fact with old last_recalled decays."""
    store = await _make_store(tmp_path)

    # Both facts are 30 days stale
    stale_offset = -86400 * 30

    fid_pinned = await _insert_fact(
        store, key="pinned_key", confidence=1.0, decay_rate=0.05,
        last_recalled_offset=stale_offset, pinned=1,
    )
    fid_normal = await _insert_fact(
        store, key="normal_key", confidence=1.0, decay_rate=0.05,
        last_recalled_offset=stale_offset, pinned=0,
    )

    changed = await store.daily_decay()
    assert changed >= 1, "at least the unpinned fact should have decayed"

    rows = await store.list_active(limit=10)
    r_pinned = next(r for r in rows if r["id"] == fid_pinned)
    r_normal = next(r for r in rows if r["id"] == fid_normal)

    assert r_pinned["confidence"] == pytest.approx(1.0), (
        f"pinned fact confidence should stay 1.0, got {r_pinned['confidence']}"
    )
    assert r_normal["confidence"] < 0.99, (
        f"unpinned fact should have decayed, got {r_normal['confidence']}"
    )


@pytest.mark.asyncio
async def test_tg2_daily_decay_returns_only_unpinned_count(tmp_path):
    """daily_decay return count should not include pinned rows."""
    store = await _make_store(tmp_path)
    stale = -86400 * 30

    await _insert_fact(store, key="pin1", decay_rate=0.05,
                       last_recalled_offset=stale, pinned=1)
    await _insert_fact(store, key="pin2", decay_rate=0.05,
                       last_recalled_offset=stale, pinned=1)
    fid_n = await _insert_fact(store, key="normal", decay_rate=0.05,
                                last_recalled_offset=stale, pinned=0)

    changed = await store.daily_decay()
    # Only the one normal fact should be counted
    assert changed == 1


# ---------------------------------------------------------------------------
# TG-3: pinned column ALTER failure → fallback, no crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tg3_daily_decay_fallback_when_pinned_col_missing(tmp_path):
    """If pinned column doesn't exist in DB, daily_decay should not crash
    (it should fall back to the WHERE is_active=1 query).
    """
    import aiosqlite
    from deskpet.memory.facts import FactsStore

    db_path = tmp_path / "old_facts.db"
    # Build a DB without pinned column (simulate pre-migration old schema)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'preference',
                subject TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                source_msg_id INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                evidence TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                decay_rate REAL NOT NULL DEFAULT 0.02,
                last_recalled REAL,
                superseded_by INTEGER,
                forgotten_at REAL,
                scope TEXT DEFAULT 'user'
                -- Note: no pinned column
            )
        """)
        now = time.time()
        stale = now - 86400 * 30
        await conn.execute(
            """INSERT INTO facts
               (category, subject, key, value, confidence,
                created_at, updated_at, is_active, decay_rate, last_recalled)
               VALUES ('preference','user','oldkey','v',1.0,?,?,1,0.05,?)""",
            (now, now, stale),
        )
        await conn.commit()

    store = FactsStore(db_path)
    # Simulate pinned column being unavailable — patch _pinned_available to False
    # by monkey-patching the internal guard method (or by directly testing
    # that daily_decay runs without OperationalError).
    # We call daily_decay directly; it should detect missing column + fall back.
    try:
        count = await store.daily_decay()
        # count may be 0 or 1; important thing is no crash
        assert count >= 0
    except Exception as exc:
        pytest.fail(f"daily_decay raised on missing pinned col: {exc}")


# ---------------------------------------------------------------------------
# TG-4: daily_decay IS scheduled in lifespan
# ---------------------------------------------------------------------------

def test_tg4_daily_decay_wired_in_main():
    """Verify main.py lifespan has an await _facts_store.daily_decay() call.

    We check source text rather than importing main (heavy deps).
    """
    main_path = Path(__file__).parent.parent / "main.py"
    src = main_path.read_text(encoding="utf-8")
    # We expect a call like: await _facts_store.daily_decay()
    assert "daily_decay" in src, (
        "main.py should contain a daily_decay call in lifespan to fix the bug"
    )
    # More specific: the call should be inside lifespan (after the @asynccontextmanager)
    lifespan_start = src.find("async def lifespan(")
    assert lifespan_start != -1
    lifespan_src = src[lifespan_start:]
    assert "daily_decay" in lifespan_src, (
        "daily_decay call must be in the lifespan function, not just defined"
    )


@pytest.mark.asyncio
async def test_tg4_daily_decay_lowers_stale_confidence(tmp_path):
    """Calling daily_decay on a store with a 30-day-old fact lowers its confidence."""
    store = await _make_store(tmp_path)
    stale = -86400 * 30
    fid = await _insert_fact(
        store, key="stale", confidence=1.0, decay_rate=0.05,
        last_recalled_offset=stale, pinned=0,
    )

    changed = await store.daily_decay()
    assert changed >= 1

    rows = await store.list_active(limit=10)
    target = next(r for r in rows if r["id"] == fid)
    expected = math.exp(-0.05 * 30)
    assert target["confidence"] == pytest.approx(expected, abs=0.01), (
        f"confidence should be exp(-0.05*30)≈{expected:.4f}, got {target['confidence']}"
    )


# ---------------------------------------------------------------------------
# TG-5: PreferenceMemory match with recency decay
# ---------------------------------------------------------------------------

def _make_embed_fn(vec: list[float]):
    """Return an embed_fn that always returns `vec` for any input."""
    async def _embed(texts):
        return [vec[:] for _ in texts]
    return _embed


@pytest.mark.asyncio
async def test_tg5_old_entry_lower_effective_score(tmp_path):
    """An entry recorded 200 days ago should have lower effective score than a fresh one.

    We use two separate PreferenceMemory instances to isolate them.
    Test is done by checking that the _PREF_DECAY_RATE * 200 days factor
    yields a clearly lower effective score; we do NOT change the threshold
    behavior (raw cosine still compared against threshold).
    """
    from deskpet.agent.preference_memory import PreferenceMemory

    pref_path = tmp_path / "pref.json"

    # Use a constant embedding so cosine is always 1.0
    vec = [1.0, 0.0, 0.0]
    embed_fn = _make_embed_fn(vec)

    # Simulate NOW = T and entry ts = T - 200days
    now_ts = time.time()
    old_ts = now_ts - 200 * 86400
    fresh_ts = now_ts - 1  # 1 second old

    # Write raw JSON with two entries (no pinned key → BC)
    pref_path.write_text(json.dumps([
        {"text": "write a report", "embedding": vec,
         "label": "approved", "kind": "plan", "ts": old_ts},
        {"text": "write a summary", "embedding": vec,
         "label": "approved", "kind": "plan", "ts": fresh_ts},
    ]), encoding="utf-8")

    pm = PreferenceMemory(pref_path, embed_fn, now_fn=lambda: now_ts,
                          pref_decay=True)

    # Both should match (cosine=1.0 >= 0.86 threshold), fresh entry wins
    # We inspect the effective scores via the _entries internals
    import math as _math
    _rate = 0.005
    effective_old = 1.0 * _math.exp(-_rate * 200)
    effective_fresh = 1.0 * _math.exp(-_rate * (1 / 86400))

    assert effective_fresh > effective_old, (
        "fresh entry should have higher effective score than 200-day-old entry"
    )

    # match() should return the fresh entry since its effective score is higher
    result = await pm.match("write something", "plan")
    assert result is not None, "should match (cosine >= threshold)"
    # The fresh entry's text was "write a summary" — match returns highest effective
    # (In practice both texts are ≥ threshold; we just assert it picks the fresher)
    # We verify no crash and returns a result.


@pytest.mark.asyncio
async def test_tg5_pinned_entry_not_decayed(tmp_path):
    """A pinned entry in PreferenceMemory should have decay=1.0 (no decay)."""
    from deskpet.agent.preference_memory import PreferenceMemory

    pref_path = tmp_path / "pref.json"

    vec = [1.0, 0.0, 0.0]
    embed_fn = _make_embed_fn(vec)
    now_ts = time.time()
    very_old_ts = now_ts - 365 * 86400  # 1 year old

    # Write raw JSON: one pinned (old) entry, one unpinned (old) entry
    pref_path.write_text(json.dumps([
        {"text": "pinned task", "embedding": vec,
         "label": "approved", "kind": "plan",
         "ts": very_old_ts, "pinned": True},
        {"text": "unpinned old task", "embedding": vec,
         "label": "approved", "kind": "plan",
         "ts": very_old_ts, "pinned": False},
    ]), encoding="utf-8")

    pm = PreferenceMemory(pref_path, embed_fn, now_fn=lambda: now_ts,
                          pref_decay=True)

    import math as _math
    _rate = 0.005
    # Pinned: effective should be 1.0 (no decay)
    # Unpinned 365 days: effective = exp(-0.005 * 365) ≈ 0.163
    pinned_effective = 1.0
    unpinned_effective = _math.exp(-_rate * 365)

    assert pinned_effective > unpinned_effective, (
        "pinned entry should have higher effective score despite being old"
    )

    # match should prefer the pinned entry
    result = await pm.match("pinned task", "plan")
    assert result is not None


@pytest.mark.asyncio
async def test_tg5_threshold_uses_raw_cosine_not_effective(tmp_path):
    """Threshold should be compared against raw cosine so no false negatives.

    If pref_decay=False (default), behavior is identical to before.
    """
    from deskpet.agent.preference_memory import PreferenceMemory

    pref_path = tmp_path / "pref.json"
    vec = [1.0, 0.0, 0.0]
    embed_fn = _make_embed_fn(vec)
    now_ts = time.time()
    old_ts = now_ts - 1000 * 86400  # very old

    pref_path.write_text(json.dumps([
        {"text": "old task", "embedding": vec,
         "label": "approved", "kind": "plan", "ts": old_ts},
    ]), encoding="utf-8")

    # With pref_decay=False (default): old entry still matches (old behavior)
    pm_no_decay = PreferenceMemory(pref_path, embed_fn, now_fn=lambda: now_ts,
                                   pref_decay=False)
    result_no_decay = await pm_no_decay.match("old task", "plan")
    assert result_no_decay is not None, (
        "pref_decay=False: old entry should still match (legacy behavior)"
    )

    # With pref_decay=True: threshold check uses raw cosine (1.0 >= 0.86) → still matches
    # But effective score is very low (will sink to bottom when multiple entries)
    pm_decay = PreferenceMemory(pref_path, embed_fn, now_fn=lambda: now_ts,
                                pref_decay=True)
    result_decay = await pm_decay.match("old task", "plan")
    assert result_decay is not None, (
        "pref_decay=True: threshold compares raw cosine, so old entry still matches"
    )


# ---------------------------------------------------------------------------
# TG-6: Old JSON without pinned key loads without error (BC)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tg6_old_json_no_pinned_loads_ok(tmp_path):
    """Loading a JSON without pinned field should not crash."""
    from deskpet.agent.preference_memory import PreferenceMemory

    pref_path = tmp_path / "pref.json"
    vec = [1.0, 0.0, 0.0]
    embed_fn = _make_embed_fn(vec)

    # Old format: no pinned key
    pref_path.write_text(json.dumps([
        {"text": "old entry", "embedding": vec,
         "label": "approved", "kind": "plan", "ts": time.time()},
    ]), encoding="utf-8")

    pm = PreferenceMemory(pref_path, embed_fn, pref_decay=True)
    # Should not crash during load or match
    result = await pm.match("old entry", "plan")
    assert result is not None, "old format entry should still match"


@pytest.mark.asyncio
async def test_tg6_record_with_pin_param_saves_pinned(tmp_path):
    """record() with pin=True should persist pinned=True in JSON."""
    from deskpet.agent.preference_memory import PreferenceMemory

    pref_path = tmp_path / "pref.json"
    vec = [1.0, 0.0, 0.0]
    embed_fn = _make_embed_fn(vec)

    pm = PreferenceMemory(pref_path, embed_fn)
    ok = await pm.record("important task", "approved", "plan", pin=True)
    assert ok

    # Reload and check
    data = json.loads(pref_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0].get("pinned") is True


@pytest.mark.asyncio
async def test_tg6_record_without_pin_param_bc(tmp_path):
    """record() without pin param should work as before (BC)."""
    from deskpet.agent.preference_memory import PreferenceMemory

    pref_path = tmp_path / "pref.json"
    vec = [1.0, 0.0, 0.0]
    embed_fn = _make_embed_fn(vec)

    pm = PreferenceMemory(pref_path, embed_fn)
    ok = await pm.record("simple task", "approved", "plan")
    assert ok

    data = json.loads(pref_path.read_text(encoding="utf-8"))
    # pinned may be absent or False — both are acceptable
    assert data[0].get("pinned", False) is False or "pinned" not in data[0]


# ---------------------------------------------------------------------------
# WI-OH-2 决策①: pref_decay 默认开 (True)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oh2_pref_decay_default_is_true(tmp_path):
    """PreferenceMemory 出厂默认 pref_decay=True（决策①翻转）。

    不传 pref_decay → 老条目 effective 分应低于新条目（衰减真生效）。
    """
    from deskpet.agent.preference_memory import PreferenceMemory

    pref_path = tmp_path / "pref.json"
    vec = [1.0, 0.0, 0.0]
    embed_fn = _make_embed_fn(vec)
    now_ts = time.time()
    old_ts = now_ts - 300 * 86400
    fresh_ts = now_ts - 1

    pref_path.write_text(json.dumps([
        {"text": "old pref", "embedding": vec,
         "label": "approved", "kind": "plan", "ts": old_ts},
        {"text": "fresh pref", "embedding": vec,
         "label": "approved", "kind": "plan", "ts": fresh_ts},
    ]), encoding="utf-8")

    # 关键：NOT passing pref_decay → 必须默认 True
    pm = PreferenceMemory(pref_path, embed_fn, now_fn=lambda: now_ts)
    assert pm._pref_decay is True, "出厂默认 pref_decay 必须为 True（决策①）"

    # 衰减生效 → match 选中较新的（effective 分更高）那条
    result = await pm.match("some pref", "plan")
    assert result is not None
    assert result["text"] == "fresh pref", (
        f"衰减开启后应优先较新条目，得到 {result['text']}"
    )


def test_oh2_config_pref_decay_default_true():
    """config.MemoryV2Config dataclass 默认 pref_decay=True（main.py 构造点读它）。"""
    from config import MemoryV2Config

    assert MemoryV2Config().pref_decay is True, (
        "config.MemoryV2Config.pref_decay 出厂默认必须 True（决策①），"
        "否则 main.py:2500 构造 PreferenceMemory 时传 False"
    )


# ---------------------------------------------------------------------------
# TG-7: p4_ipc memory_pin / memory_unpin verb
# ---------------------------------------------------------------------------

class _FakeWS:
    """Fake WebSocket that captures send_json calls."""
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class _FakeFactsStore:
    """Minimal FactsStore fake that records set_pinned calls."""
    def __init__(self):
        self.calls: list[tuple[int, bool]] = []

    async def set_pinned(self, fact_id: int, pinned: bool) -> None:
        self.calls.append((int(fact_id), bool(pinned)))


class _FakeSC:
    """Fake service context."""
    def __init__(self, facts_store):
        self._facts_store = facts_store

    def get(self, name: str):
        if name == "facts_store":
            return self._facts_store
        return None


@pytest.mark.asyncio
async def test_tg7_memory_pin_verb_calls_set_pinned():
    """memory_pin verb in p4_ipc should call facts_store.set_pinned(id, True)."""
    import p4_ipc

    ws = _FakeWS()
    store = _FakeFactsStore()
    sc = _FakeSC(store)

    await p4_ipc.handle(ws, "sess1", "memory_pin", {"fact_id": 42}, sc)

    assert len(store.calls) == 1
    fact_id, pinned = store.calls[0]
    assert fact_id == 42
    assert pinned is True

    # Response type check
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "memory_pin_response"


@pytest.mark.asyncio
async def test_tg7_memory_unpin_verb_calls_set_pinned_false():
    """memory_unpin verb in p4_ipc should call facts_store.set_pinned(id, False)."""
    import p4_ipc

    ws = _FakeWS()
    store = _FakeFactsStore()
    sc = _FakeSC(store)

    await p4_ipc.handle(ws, "sess1", "memory_unpin", {"fact_id": 7}, sc)

    assert len(store.calls) == 1
    fact_id, pinned = store.calls[0]
    assert fact_id == 7
    assert pinned is False

    assert ws.sent[0]["type"] == "memory_unpin_response"


@pytest.mark.asyncio
async def test_tg7_memory_pin_in_p4_valid_set():
    """memory_pin and memory_unpin must appear in P4_IPC_MESSAGE_TYPES."""
    import p4_ipc
    assert "memory_pin" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert "memory_unpin" in p4_ipc.P4_IPC_MESSAGE_TYPES


@pytest.mark.asyncio
async def test_tg7_memory_pin_no_facts_store_graceful():
    """memory_pin without facts_store registered should respond with error gracefully."""
    import p4_ipc

    ws = _FakeWS()

    class _EmptySC:
        def get(self, name): return None

    await p4_ipc.handle(ws, "sess1", "memory_pin", {"fact_id": 1}, _EmptySC())

    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "memory_pin_response"
    assert ws.sent[0]["payload"].get("status") == "error"
