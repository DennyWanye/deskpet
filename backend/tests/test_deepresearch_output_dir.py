# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

import paths
from deskpet.tools import research_tools as r
from deskpet.tools.research_tools import Citation, ResearchReport


def _report(
    *,
    topic: str = "测试主题",
    filename_id: int = 1,
    coverage: dict | None = None,
) -> ResearchReport:
    cov = {
        "n_sources": 2,
        "n_domains": 2,
        "mode": "deep",
        "n_sub_questions": 3,
        "rounds": 1,
        "topic_velocity": "slow",
        "cite_check_ok": True,
    }
    if coverage:
        cov.update(coverage)
    return ResearchReport(
        topic=topic,
        summary="summary",
        report_md=f"# {topic}\n\n正文 {filename_id} [^1]\n\n[^1]: source\n",
        citations=[
            Citation(
                n=1,
                url=f"https://example{filename_id}.com/a",
                title="source",
                snippet="snippet",
                fetched_at=1.0,
            )
        ],
        sub_questions=["q1", "q2", "q3"],
        coverage=cov,
        errors=[],
    )


def test_deepresearch_dir_env_override_has_highest_priority(monkeypatch, tmp_path):
    override = tmp_path / "custom-dr"
    monkeypatch.setenv("DESKPET_DEEPRESEARCH_DIR", str(override))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "install" / "backend" / "deskpet.exe"))

    assert paths.deepresearch_dir() == override
    assert override.is_dir()


def test_deepresearch_dir_frozen_uses_install_root(monkeypatch, tmp_path):
    exe = tmp_path / "DeskPet" / "backend" / "deskpet-backend.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.delenv("DESKPET_DEEPRESEARCH_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    target = paths.deepresearch_dir()

    assert target == tmp_path / "DeskPet" / "DeepResearch"
    assert target.is_dir()


def test_deepresearch_dir_dev_uses_repo_root(monkeypatch, tmp_path):
    fake_paths = tmp_path / "repo" / "backend" / "paths.py"
    fake_paths.parent.mkdir(parents=True)
    fake_paths.write_text("", encoding="utf-8")
    monkeypatch.delenv("DESKPET_DEEPRESEARCH_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(paths, "__file__", str(fake_paths))

    target = paths.deepresearch_dir()

    assert target == tmp_path / "repo" / "DeepResearch"
    assert target.is_dir()


def test_deepresearch_dir_frozen_unwritable_falls_back_home_not_user_data(monkeypatch, tmp_path):
    install_backend = tmp_path / "install" / "backend"
    home = tmp_path / "home"
    user_data = tmp_path / "roaming" / "deskpet"
    original_write_bytes = Path.write_bytes

    def fail_install_probe(self: Path, data: bytes) -> int:
        if self.name.startswith(".deskpet-dr-write-probe-") and tmp_path / "install" in self.parents:
            raise OSError("read-only install root")
        return original_write_bytes(self, data)

    monkeypatch.delenv("DESKPET_DEEPRESEARCH_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths, "_install_dir", lambda: install_backend)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(paths, "user_data_dir", lambda: user_data)
    monkeypatch.setattr(Path, "write_bytes", fail_install_probe)

    target = paths.deepresearch_dir()

    assert target == home / "DeskPet" / "DeepResearch"
    assert target.is_dir()
    assert target != user_data / "DeepResearch"


def test_install_root_for_deepresearch_uses_pid_probe_name(monkeypatch, tmp_path):
    backend_dir = tmp_path / "install" / "backend"
    seen: list[str] = []
    original_write_bytes = Path.write_bytes

    def record_write(self: Path, data: bytes) -> int:
        seen.append(self.name)
        return original_write_bytes(self, data)

    monkeypatch.setattr(paths, "_install_dir", lambda: backend_dir)
    monkeypatch.setattr(Path, "write_bytes", record_write)

    target = paths._install_root_for_deepresearch()

    assert target == tmp_path / "install" / "DeepResearch"
    assert seen == [f".deskpet-dr-write-probe-{os.getpid()}"]
    assert not (target / seen[0]).exists()


def test_insert_row_after_header_inserts_after_separator_and_keeps_newest_first():
    old_row = "| 2026-01-01 | old | [old](old.md) | 1 | 1 | flat | 1 |\n"
    new_row = "| 2026-01-02 | new | [new](new.md) | 2 | 2 | deep | 2 |\n"
    existing = r._INDEX_HEADER + old_row

    updated = r._insert_row_after_header(existing, new_row)

    assert updated.index(new_row) < updated.index(old_row)
    assert updated.startswith(r._INDEX_HEADER)


def test_insert_row_after_header_rebuilds_when_separator_missing():
    row = "| 2026-01-02 | new | [new](new.md) | 2 | 2 | deep | 2 |\n"

    updated = r._insert_row_after_header("# broken\n", row)

    assert updated == r._INDEX_HEADER + row + "\n"


@pytest.mark.asyncio
async def test_update_deepresearch_index_creates_header_dedupes_and_preserves_utf8(tmp_path):
    report_path = tmp_path / "中文报告.md"
    report_path.write_text("# report", encoding="utf-8")
    report = _report(topic="中文|主题\n换行")

    await r._update_deepresearch_index(report_path, "中文|主题\n换行", report)
    await r._update_deepresearch_index(report_path, "中文|主题\n换行", report)

    text = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert text.startswith(r._INDEX_HEADER)
    assert text.count("[中文报告.md](中文报告.md)") == 1
    assert "中文/主题 换行" in text
    assert "[中文报告.md](中文报告.md)" in text
    # 原子写：os.replace 落地后正式 index.md 存在，临时 .md.tmp 不残留
    assert (tmp_path / "index.md").exists()
    assert not (tmp_path / "index.md.tmp").exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_index_mode_column_is_flat_not_depth(tmp_path):
    # cov["mode"]="deep"(档位) 不应泄漏到模式列；扁平跑模式列必须是 "flat"
    report_path = tmp_path / "flatrun.md"
    report_path.write_text("# r", encoding="utf-8")
    await r._update_deepresearch_index(report_path, "flat topic", _report(topic="flat topic"))
    text = (tmp_path / "index.md").read_text(encoding="utf-8")
    row = [l for l in text.splitlines() if "flatrun.md" in l][0]
    assert "| flat |" in row
    assert "| deep |" not in row  # 档位绝不能当模式列


@pytest.mark.asyncio
async def test_index_mode_column_is_fanout_when_subagent_fanout_present(tmp_path):
    report_path = tmp_path / "fanrun.md"
    report_path.write_text("# r", encoding="utf-8")
    rep = _report(topic="fan topic", coverage={"subagent_fanout": {"enabled": True, "n_subagents": 3}})
    await r._update_deepresearch_index(report_path, "fan topic", rep)
    text = (tmp_path / "index.md").read_text(encoding="utf-8")
    row = [l for l in text.splitlines() if "fanrun.md" in l][0]
    assert "| fanout |" in row


@pytest.mark.asyncio
async def test_update_deepresearch_index_serializes_concurrent_writes(tmp_path):
    report_a = tmp_path / "a.md"
    report_b = tmp_path / "b.md"
    report_a.write_text("# a", encoding="utf-8")
    report_b.write_text("# b", encoding="utf-8")

    await asyncio.gather(
        r._update_deepresearch_index(report_a, "topic a", _report(topic="topic a", filename_id=1)),
        r._update_deepresearch_index(report_b, "topic b", _report(topic="topic b", filename_id=2)),
    )

    text = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert text.count(".md)") == 2
    assert "a.md" in text
    assert "b.md" in text


def test_save_report_uses_deepresearch_dir_not_output_research(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKPET_DEEPRESEARCH_DIR", str(tmp_path / "DeepResearch"))

    saved = r._save_report("中文主题", _report(topic="中文主题"))

    assert saved is not None
    assert saved.parent == tmp_path / "DeepResearch"
    assert saved.exists()
    assert "OutPut" not in str(saved)
