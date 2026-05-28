# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TDD — picker_tools.py (office_pick_file native dialog)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deskpet.tools import office_paths as op
from deskpet.tools import picker_tools as pk


@pytest.fixture(autouse=True)
def _clean():
    op.clear_authorizations()
    yield
    op.clear_authorizations()


def test_pick_success_authorizes_path(tmp_path: Path, monkeypatch):
    picked = tmp_path / "chosen.docx"
    picked.write_text("x")

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, f"DESKPET_PICK::{picked}\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = pk.office_pick("file")
    assert r["ok"] and r["path"]
    assert op.is_authorized(picked)


def test_pick_cancelled(monkeypatch):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, "DESKPET_PICK::__CANCELLED__\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = pk.office_pick("file")
    assert r["ok"] is False and r["cancelled"] is True


def test_pick_timeout(monkeypatch):
    def fake_run(args, **kw):
        raise subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = pk.office_pick("file")
    assert not r["ok"] and r["error"] == "picker_timeout"


def test_pick_unknown_kind():
    r = pk.office_pick("banana")
    assert not r["ok"]


def test_pick_dir_kind(tmp_path: Path, monkeypatch):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, f"DESKPET_PICK::{tmp_path}\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = pk.office_pick("dir")
    assert r["ok"] and op.is_authorized(tmp_path)
