# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TDD T1 — excel_tools.py (excel_create)."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import office_paths as op
from deskpet.tools import excel_tools as xl

pytestmark = pytest.mark.skipif(not xl._HAS_OPENPYXL, reason="openpyxl not installed")


@pytest.fixture(autouse=True)
def _auth(tmp_path: Path):
    op.clear_authorizations()
    op.authorize_path(tmp_path)
    yield
    op.clear_authorizations()


def _load(path: str):
    from openpyxl import load_workbook

    return load_workbook(path)


def test_t1_1_single_sheet_data(tmp_path: Path):
    out = tmp_path / "a.xlsx"
    spec = {"sheets": [{"name": "S1", "rows": [["x", "y"], [1, 2]]}]}
    r = xl.excel_create(spec, output_path=str(out))
    assert r["ok"], r
    wb = _load(r["path"])
    assert wb["S1"]["A2"].value == 1


def test_t1_2_multi_sheet(tmp_path: Path):
    spec = {"sheets": [
        {"name": "Alpha", "rows": [["a"]]},
        {"name": "Beta", "rows": [["b"]]},
    ]}
    r = xl.excel_create(spec, output_path=str(tmp_path / "m.xlsx"))
    assert r["ok"]
    wb = _load(r["path"])
    assert wb.sheetnames == ["Alpha", "Beta"]


def test_t1_3_formula_cell(tmp_path: Path):
    spec = {"sheets": [{"name": "S", "rows": [[1], [2], ["=SUM(A1:A2)"]]}]}
    r = xl.excel_create(spec, output_path=str(tmp_path / "f.xlsx"))
    assert r["ok"]
    wb = _load(r["path"])
    assert str(wb["S"]["A3"].value).startswith("=SUM")


def test_t1_4_chart_present(tmp_path: Path):
    spec = {"sheets": [{
        "name": "S",
        "rows": [["cat", "val"], ["a", 3], ["b", 5]],
        "chart": {"type": "bar", "title": "T", "data": "B1:B3", "categories": "A2:A3", "anchor": "D2"},
    }]}
    r = xl.excel_create(spec, output_path=str(tmp_path / "c.xlsx"))
    assert r["ok"]
    wb = _load(r["path"])
    assert len(wb["S"]._charts) >= 1


def test_t1_5_conditional_format(tmp_path: Path):
    spec = {"sheets": [{
        "name": "S",
        "rows": [["v"], [1], [9]],
        "conditional_format": {"range": "A2:A3", "rule": "color_scale"},
    }]}
    r = xl.excel_create(spec, output_path=str(tmp_path / "cf.xlsx"))
    assert r["ok"]
    wb = _load(r["path"])
    assert len(list(wb["S"].conditional_formatting)) >= 1


def test_t1_6_header_style_and_width(tmp_path: Path):
    spec = {"sheets": [{
        "name": "S",
        "rows": [["Head1", "Head2"], [1, 2]],
        "header_row": True,
        "column_widths": {"A": 25},
    }]}
    r = xl.excel_create(spec, output_path=str(tmp_path / "h.xlsx"))
    assert r["ok"]
    wb = _load(r["path"])
    ws = wb["S"]
    assert ws["A1"].font.bold
    assert ws.column_dimensions["A"].width == 25


def test_t1_7_spec_as_json_string(tmp_path: Path):
    spec = '{"sheets": [{"name": "S", "rows": [["a", 1]]}]}'
    r = xl.excel_create(spec, output_path=str(tmp_path / "j.xlsx"))
    assert r["ok"]


def test_t1_8_bad_spec_no_crash():
    r = xl.excel_create({"not_sheets": []})
    assert not r["ok"] and r["retriable"] is False


def test_t1_9_no_output_path_goes_to_temp():
    r = xl.excel_create({"sheets": [{"name": "S", "rows": [["a"]]}]})
    assert r["ok"]
    assert Path(r["path"]).exists()
    Path(r["path"]).unlink(missing_ok=True)


def test_t1_10_system_path_refused():
    r = xl.excel_create(
        {"sheets": [{"name": "S", "rows": [["a"]]}]},
        output_path="C:\\Windows\\evil.xlsx",
    )
    assert not r["ok"] and r["retriable"] is False


def test_t1_11_empty_sheets_list_rejected():
    r = xl.excel_create({"sheets": []})
    assert not r["ok"]
