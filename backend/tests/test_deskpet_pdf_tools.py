# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TDD T3 — pdf_tools.py (pdf_export)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deskpet.tools import office_paths as op
from deskpet.tools import pdf_tools as pdf


@pytest.fixture(autouse=True)
def _clean():
    op.clear_authorizations()
    yield
    op.clear_authorizations()


def _seed_docx(tmp_path: Path) -> Path:
    src = tmp_path / "in.docx"
    src.write_bytes(b"fake-docx")
    op.authorize_path(src)
    op.authorize_path(tmp_path)
    return src


def test_t3_1_soffice_missing(tmp_path: Path, monkeypatch):
    src = _seed_docx(tmp_path)
    monkeypatch.setattr(pdf, "find_soffice", lambda: None)
    r = pdf.pdf_export(str(src))
    assert not r["ok"] and r["error"] == "soffice_missing"


def test_t3_2_mock_soffice_success(tmp_path: Path, monkeypatch):
    src = _seed_docx(tmp_path)
    monkeypatch.setattr(pdf, "find_soffice", lambda: "soffice")

    def fake_run(args, **kw):
        # find --outdir and drop a <stem>.pdf there
        outdir = Path(args[args.index("--outdir") + 1])
        stem = Path(args[-1]).stem
        (outdir / f"{stem}.pdf").write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = pdf.pdf_export(str(src), output_path=str(tmp_path / "out.pdf"))
    assert r["ok"], r
    assert Path(r["path"]).is_file()


def test_t3_3_mock_soffice_failure(tmp_path: Path, monkeypatch):
    src = _seed_docx(tmp_path)
    monkeypatch.setattr(pdf, "find_soffice", lambda: "soffice")

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = pdf.pdf_export(str(src))
    assert not r["ok"]


def test_t3_4_mock_soffice_timeout(tmp_path: Path, monkeypatch):
    src = _seed_docx(tmp_path)
    monkeypatch.setattr(pdf, "find_soffice", lambda: "soffice")

    def fake_run(args, **kw):
        raise subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = pdf.pdf_export(str(src))
    assert not r["ok"] and r["error"] == "pdf_export_timeout"


def test_t3_5_input_not_found(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pdf, "find_soffice", lambda: "soffice")
    r = pdf.pdf_export(str(tmp_path / "ghost.docx"))
    assert not r["ok"]


def test_t3_6_input_unauthorized(tmp_path: Path, monkeypatch):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")  # exists but NOT authorized
    monkeypatch.setattr(pdf, "find_soffice", lambda: "soffice")
    r = pdf.pdf_export(str(src))
    assert not r["ok"] and r["retriable"] is False


def test_t3_7_find_soffice_env(tmp_path: Path, monkeypatch):
    fake = tmp_path / "soffice.exe"
    fake.write_bytes(b"x")
    monkeypatch.setenv("DESKPET_SOFFICE_PATH", str(fake))
    assert pdf.find_soffice() == str(fake)
