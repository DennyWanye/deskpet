"""Tests for L1 FileMemory — P4-S4.

Covers spec scenarios from
``openspec/changes/p4-poseidon-agent-harness/specs/memory-system/spec.md``
(Requirement "File Memory (L1)" + "Frozen Snapshot Pattern").
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from deskpet.memory.file_memory import FileMemory, _parse_entries, _serialize_entries


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def fm(base_dir: Path) -> FileMemory:
    return FileMemory(base_dir=base_dir)


# ---------------------------------------------------------------------------
# Happy path: append + read
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_tool_add_writes_to_file_with_separator(fm: FileMemory, base_dir: Path):
    """Spec: memory tool add writes to file.

    append → file exists → `§` separator between multiple entries.
    """
    await fm.append("memory", "主人晚上 9 点后不喜欢高音")
    await fm.append("memory", "主人猫的名字叫咪咪")

    content = (base_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "主人晚上 9 点后不喜欢高音" in content
    assert "咪咪" in content
    # Default salience (0.5) → no tag written.
    assert "salience=" not in content
    # Separator lives between the two entries.
    assert "\n§\n" in content


@pytest.mark.asyncio
async def test_append_writes_salience_tag_when_non_default(fm: FileMemory, base_dir: Path):
    await fm.append("memory", "重要偏好", salience=0.95)
    content = (base_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "重要偏好" in content
    assert "{{salience=0.95}}" in content


@pytest.mark.asyncio
async def test_read_snapshot_empty_when_files_missing(fm: FileMemory):
    snap = await fm.read_snapshot()
    assert snap == {"memory": "", "user": ""}


@pytest.mark.asyncio
async def test_read_snapshot_returns_current_files(fm: FileMemory, base_dir: Path):
    (base_dir / "MEMORY.md").write_text("hello world", encoding="utf-8")
    (base_dir / "USER.md").write_text("is a cat person", encoding="utf-8")
    snap = await fm.read_snapshot()
    assert snap["memory"] == "hello world"
    assert snap["user"] == "is a cat person"


@pytest.mark.asyncio
async def test_list_entries_round_trip(fm: FileMemory):
    await fm.append("user", "主人是程序员", salience=0.8)
    await fm.append("user", "主人喜欢喝拿铁")
    entries = await fm.list_entries("user")
    assert [e["text"] for e in entries] == ["主人是程序员", "主人喜欢喝拿铁"]
    assert entries[0]["salience"] == pytest.approx(0.8)
    assert entries[1]["salience"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Size-cap eviction
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_size_cap_eviction_keeps_highest_salience(base_dir: Path):
    """Spec: Size cap eviction — write past 50KB, file stays ≤ cap, highest-
    salience entries kept.

    We shrink the cap to 2KB to keep the test snappy (~200 entries is plenty).
    """
    fm = FileMemory(base_dir=base_dir, memory_md_max_kb=2, user_md_max_kb=1)

    # Low-salience filler (0.1) — should be evicted first.
    filler = "X" * 100  # 100 bytes of filler
    for i in range(20):
        await fm.append("memory", f"{filler}-{i}", salience=0.1)

    # High-salience lessons (0.9) — should survive.
    high_entries = [f"CORE lesson #{i}" for i in range(3)]
    for entry in high_entries:
        await fm.append("memory", entry, salience=0.9)

    # Another burst of filler to push over cap for sure.
    for i in range(40):
        await fm.append("memory", f"{filler}-tail-{i}", salience=0.1)

    path = base_dir / "MEMORY.md"
    size = path.stat().st_size
    assert size <= 2 * 1024, f"file size {size} exceeded 2KB cap"

    entries = await fm.list_entries("memory")
    surviving_texts = [e["text"] for e in entries]
    for core in high_entries:
        assert core in surviving_texts, f"lost high-salience entry: {core}"


@pytest.mark.asyncio
async def test_size_cap_older_evicted_first_on_tie(base_dir: Path):
    """When two entries share salience, the older one is evicted first."""
    fm = FileMemory(base_dir=base_dir, memory_md_max_kb=1)

    # Fill with equal-salience entries large enough that only one fits.
    big = "Y" * 400
    await fm.append("memory", f"older-{big}", salience=0.5)
    await fm.append("memory", f"newer-{big}", salience=0.5)
    await fm.append("memory", f"newest-{big}", salience=0.5)

    entries = await fm.list_entries("memory")
    texts = [e["text"] for e in entries]
    # Older must have been evicted first; whatever survives must NOT include
    # the very-oldest on the tie.
    assert not any(t.startswith("older-") for t in texts)


# ---------------------------------------------------------------------------
# Corrupt / edge parsing
# ---------------------------------------------------------------------------
def test_corrupted_salience_tag_falls_back_to_default():
    """Malformed salience tags must not crash — default to 0.5."""
    raw = (
        "entry one {{salience=not_a_number}}\n§\n"
        "entry two {{salience=}}\n§\n"
        "entry three {{salience=0.8}}"
    )
    parsed = _parse_entries(raw, "\n§\n")
    # First two keep their literal (malformed) trailing tag in text; the
    # regex only matches a well-formed number so the tag isn't stripped.
    assert parsed[0]["salience"] == 0.5
    assert parsed[1]["salience"] == 0.5
    assert parsed[2]["salience"] == pytest.approx(0.8)
    # Third entry has its tag stripped cleanly.
    assert parsed[2]["text"] == "entry three"


def test_parse_ignores_blank_blocks():
    raw = "\n§\nhello\n§\n   \n§\nworld\n§\n"
    parsed = _parse_entries(raw, "\n§\n")
    assert [e["text"] for e in parsed] == ["hello", "world"]


def test_serialize_round_trip_preserves_default_cleanly():
    entries = [
        {"text": "one", "salience": 0.5},
        {"text": "two", "salience": 0.9},
    ]
    s = _serialize_entries(entries, "\n§\n")
    assert s == "one\n§\ntwo {{salience=0.9}}"
    assert _parse_entries(s, "\n§\n") == entries


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_appends_all_land(fm: FileMemory, base_dir: Path):
    """10 parallel append() calls: every entry present at the end."""
    texts = [f"concurrent-entry-{i}" for i in range(10)]
    await asyncio.gather(*(fm.append("memory", t) for t in texts))

    entries = await fm.list_entries("memory")
    got = sorted(e["text"] for e in entries)
    assert got == sorted(texts)


@pytest.mark.asyncio
async def test_targets_do_not_cross_contaminate(fm: FileMemory, base_dir: Path):
    """memory and user files must be independent."""
    await fm.append("memory", "pet observation")
    await fm.append("user", "user trait")
    mem = (base_dir / "MEMORY.md").read_text(encoding="utf-8")
    usr = (base_dir / "USER.md").read_text(encoding="utf-8")
    assert "pet observation" in mem and "user trait" not in mem
    assert "user trait" in usr and "pet observation" not in usr


@pytest.mark.asyncio
async def test_empty_content_is_ignored(fm: FileMemory, base_dir: Path):
    await fm.append("memory", "   ")
    await fm.append("memory", "")
    path = base_dir / "MEMORY.md"
    # File may or may not exist; either way it must not contain stray separators.
    if path.exists():
        assert path.read_text(encoding="utf-8").strip() == ""


@pytest.mark.asyncio
async def test_invalid_target_raises(fm: FileMemory):
    with pytest.raises(ValueError):
        await fm.append("bogus", "x")
    with pytest.raises(ValueError):
        await fm.list_entries("bogus")


@pytest.mark.asyncio
async def test_salience_clamped_to_unit_interval(fm: FileMemory):
    await fm.append("memory", "clamp-low", salience=-5.0)
    await fm.append("memory", "clamp-high", salience=5.0)
    entries = await fm.list_entries("memory")
    sal_by_text = {e["text"]: e["salience"] for e in entries}
    assert sal_by_text["clamp-low"] == 0.0
    assert sal_by_text["clamp-high"] == 1.0
