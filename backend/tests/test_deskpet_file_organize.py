# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TDD T4 — file_organize_tools.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import office_paths as op
from deskpet.tools import file_organize_tools as fo


@pytest.fixture(autouse=True)
def _clean():
    op.clear_authorizations()
    yield
    op.clear_authorizations()


def _seed(tmp_path: Path) -> Path:
    d = tmp_path / "messy"
    d.mkdir()
    (d / "photo.jpg").write_bytes(b"img-data")
    (d / "report.docx").write_bytes(b"doc-data")
    (d / "sheet.xlsx").write_bytes(b"xls-data")
    op.authorize_path(d)
    return d


def test_t4_1_dry_run_default_no_changes(tmp_path: Path):
    d = _seed(tmp_path)
    before = sorted(p.name for p in d.iterdir())
    r = fo.file_organize(str(d))
    assert r["ok"] and r["dry_run"] is True
    after = sorted(p.name for p in d.iterdir())
    assert before == after  # nothing moved


def test_t4_2_by_type_plan(tmp_path: Path):
    d = _seed(tmp_path)
    r = fo.file_organize(str(d), mode="by_type")
    targets = {item["file"]: item["target_dir"] for item in r["plan"]}
    assert targets["photo.jpg"] == "images"
    assert targets["report.docx"] == "documents"
    assert targets["sheet.xlsx"] == "spreadsheets"


def test_t4_3_by_date_plan(tmp_path: Path):
    d = _seed(tmp_path)
    r = fo.file_organize(str(d), mode="by_date")
    assert all(len(item["target_dir"]) == 7 and "-" in item["target_dir"] for item in r["plan"])


def test_t4_4_execute_by_type(tmp_path: Path):
    d = _seed(tmp_path)
    r = fo.file_organize(str(d), mode="by_type", dry_run=False)
    assert r["ok"] and r["moved"] == 3
    assert (d / "images" / "photo.jpg").exists()
    assert (d / "documents" / "report.docx").exists()


def test_t4_5_dedup(tmp_path: Path):
    d = tmp_path / "dups"
    d.mkdir()
    (d / "a.txt").write_bytes(b"same-content")
    (d / "b.txt").write_bytes(b"same-content")
    (d / "c.txt").write_bytes(b"different")
    op.authorize_path(d)
    r = fo.file_organize(str(d), mode="dedup")
    assert r["ok"]
    groups = r["duplicate_groups"]
    assert len(groups) == 1
    assert set(groups[0]) == {"a.txt", "b.txt"}


def test_t4_6_unauthorized_dir_rejected(tmp_path: Path):
    d = tmp_path / "messy"
    d.mkdir()
    r = fo.file_organize(str(d))
    assert not r["ok"] and r["retriable"] is False


def test_t4_7_empty_dir(tmp_path: Path):
    d = tmp_path / "empty"
    d.mkdir()
    op.authorize_path(d)
    r = fo.file_organize(str(d))
    assert r["ok"] and r["plan"] == []


def test_t4_8_collision_avoided(tmp_path: Path):
    d = tmp_path / "messy"
    d.mkdir()
    (d / "photo.jpg").write_bytes(b"one")
    (d / "images").mkdir()
    (d / "images" / "photo.jpg").write_bytes(b"existing")
    op.authorize_path(d)
    fo.file_organize(str(d), mode="by_type", dry_run=False)
    names = sorted(p.name for p in (d / "images").iterdir())
    assert "photo.jpg" in names and "photo (2).jpg" in names


def test_t4_9_dry_run_implicit_true(tmp_path: Path):
    d = _seed(tmp_path)
    # handler path: dry_run absent → must default True
    import json

    out = json.loads(fo._handle({"dir_path": str(d)}))
    assert out["dry_run"] is True
