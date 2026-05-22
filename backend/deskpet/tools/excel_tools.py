"""Excel generation tool — openpyxl wrapper (beta builtin skill).

The LLM produces a structured ``spec`` (a dict, or a JSON string of one);
this module turns it into a real ``.xlsx`` on disk. Mirrors the design of
:mod:`ppt_tools`: the model decides *content + structure*, this module
owns the *rendering*.

Spec shape::

    {
      "sheets": [
        {
          "name": "开支",
          "rows": [["类别","金额"], ["餐饮", 1200], ["合计", "=SUM(B2:B2)"]],
          "header_row": true,
          "column_widths": {"A": 18, "B": 12},
          "freeze_panes": "A2",
          "conditional_format": {"range": "B2:B3", "rule": "color_scale"},
          "chart": {"type": "bar", "title": "开支",
                    "data": "B1:B3", "categories": "A2:A3", "anchor": "D2"}
        }
      ]
    }

* Any cell whose value is a string starting with ``=`` is written as a
  live formula (openpyxl handles this natively).
* ``header_row`` styles row 1 bold with a fill + autofits columns.
* ``conditional_format.rule`` ∈ {color_scale, data_bar, greater_than}.
* ``chart.type`` ∈ {bar, line, pie}.

Failure philosophy: a bad spec returns ``{"ok": false, "error": ...}`` —
never raises out of the handler. openpyxl missing → same.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import office_paths

log = logging.getLogger(__name__)

try:  # openpyxl is a hard dep for this skill, but degrade gracefully.
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.formatting.rule import ColorScaleRule, DataBarRule, CellIsRule
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    _HAS_OPENPYXL = True
except Exception:  # noqa: BLE001
    _HAS_OPENPYXL = False


_HEADER_FILL = "FF1F4E78"
_HEADER_FONT = "FFFFFFFF"


def _coerce_spec(spec: Any) -> Optional[dict[str, Any]]:
    """Accept a dict or a JSON string; return a dict or None."""
    if isinstance(spec, dict):
        return spec
    if isinstance(spec, str):
        try:
            parsed = json.loads(spec)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _apply_header_style(ws, ncols: int) -> None:
    fill = PatternFill("solid", fgColor=_HEADER_FILL)
    font = Font(bold=True, color=_HEADER_FONT)
    align = Alignment(horizontal="center", vertical="center")
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = fill
        cell.font = font
        cell.alignment = align


def _autofit_columns(ws, rows: list[list[Any]]) -> None:
    """Best-effort column auto-width from the longest cell in each column."""
    if not rows:
        return
    ncols = max((len(r) for r in rows), default=0)
    for c in range(ncols):
        longest = 0
        for r in rows:
            if c < len(r) and r[c] is not None:
                # CJK chars render ~2x wide; weight them.
                text = str(r[c])
                width = sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)
                longest = max(longest, width)
        ws.column_dimensions[get_column_letter(c + 1)].width = min(
            max(longest + 2, 8), 60
        )


def _apply_conditional_format(ws, cf: dict[str, Any]) -> None:
    rng = cf.get("range")
    rule = (cf.get("rule") or "color_scale").lower()
    if not rng:
        return
    if rule == "color_scale":
        ws.conditional_formatting.add(
            rng,
            ColorScaleRule(
                start_type="min", start_color="FFF8696B",
                mid_type="percentile", mid_value=50, mid_color="FFFFEB84",
                end_type="max", end_color="FF63BE7B",
            ),
        )
    elif rule == "data_bar":
        ws.conditional_formatting.add(
            rng,
            DataBarRule(start_type="min", end_type="max", color="FF638EC6"),
        )
    elif rule == "greater_than":
        threshold = cf.get("value", 0)
        ws.conditional_formatting.add(
            rng,
            CellIsRule(
                operator="greaterThan",
                formula=[str(threshold)],
                fill=PatternFill("solid", fgColor="FFFFC7CE"),
            ),
        )


def _add_chart(ws, chart_spec: dict[str, Any]) -> None:
    ctype = (chart_spec.get("type") or "bar").lower()
    if ctype == "line":
        chart = LineChart()
    elif ctype == "pie":
        chart = PieChart()
    else:
        chart = BarChart()
    chart.title = chart_spec.get("title") or None
    data_rng = chart_spec.get("data")
    cats_rng = chart_spec.get("categories")
    if data_rng:
        chart.add_data(
            Reference(ws, range_string=f"{ws.title}!{data_rng}"),
            titles_from_data=True,
        )
    if cats_rng:
        chart.set_categories(Reference(ws, range_string=f"{ws.title}!{cats_rng}"))
    anchor = chart_spec.get("anchor") or "H2"
    ws.add_chart(chart, anchor)


def _build_sheet(wb, sheet_spec: dict[str, Any], first: bool) -> None:
    name = str(sheet_spec.get("name") or "Sheet")[:31]  # Excel 31-char cap
    if first:
        ws = wb.active
        ws.title = name
    else:
        ws = wb.create_sheet(title=name)

    rows = sheet_spec.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    for r in rows:
        ws.append(list(r) if isinstance(r, (list, tuple)) else [r])

    ncols = max((len(r) for r in rows if isinstance(r, (list, tuple))), default=0)

    if sheet_spec.get("header_row") and rows:
        _apply_header_style(ws, ncols)

    # Explicit column widths win over autofit.
    widths = sheet_spec.get("column_widths") or {}
    if widths and isinstance(widths, dict):
        for col, w in widths.items():
            try:
                ws.column_dimensions[str(col)].width = float(w)
            except (ValueError, TypeError):
                pass
    elif rows:
        _autofit_columns(ws, [r for r in rows if isinstance(r, (list, tuple))])

    freeze = sheet_spec.get("freeze_panes")
    if freeze:
        ws.freeze_panes = str(freeze)

    cf = sheet_spec.get("conditional_format")
    if isinstance(cf, dict):
        _apply_conditional_format(ws, cf)

    chart = sheet_spec.get("chart")
    if isinstance(chart, dict):
        try:
            _add_chart(ws, chart)
        except Exception as exc:  # noqa: BLE001 — chart is non-critical
            log.warning("excel chart skipped: %s", exc)


def excel_create(
    spec: Any,
    *,
    output_path: Optional[str] = None,
) -> dict[str, Any]:
    """Render ``spec`` into a ``.xlsx``. See module docstring for shape.

    Returns ``{"ok": True, "path", "sheet_count"}`` or
    ``{"ok": False, "error": ...}``.
    """
    if not _HAS_OPENPYXL:
        return {
            "ok": False,
            "error": "openpyxl not installed; install with `pip install openpyxl`",
            "retriable": False,
        }

    spec_dict = _coerce_spec(spec)
    if spec_dict is None:
        return {"ok": False, "error": "spec must be a dict or JSON object string", "retriable": False}

    sheets = spec_dict.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        return {"ok": False, "error": "spec.sheets must be a non-empty list", "retriable": False}

    try:
        out_path = office_paths.resolve_for_write(
            output_path, default_prefix="deskpet-excel", default_suffix=".xlsx"
        )
    except office_paths.PathError as exc:
        return {"ok": False, "error": str(exc), "retriable": False}

    try:
        wb = Workbook()
        for i, sheet_spec in enumerate(sheets):
            if not isinstance(sheet_spec, dict):
                continue
            _build_sheet(wb, sheet_spec, first=(i == 0))
        wb.save(str(out_path))
    except Exception as exc:  # noqa: BLE001
        log.warning("excel_create failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"render failed: {exc}", "retriable": True}

    return {
        "ok": True,
        "path": str(out_path),
        "sheet_count": len(sheets),
    }


# ---------------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------------
_SCHEMA = {
    "name": "excel_create",
    "description": (
        "Generate a professional .xlsx spreadsheet locally from a structured "
        "spec. Supports multiple sheets, live formulas (any cell value "
        "starting with '='), charts (bar/line/pie), conditional formatting, "
        "header styling, frozen panes and column widths. Returns the file "
        "path. Decide the data layout first, then call this."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spec": {
                "description": (
                    "Dict (or JSON string of a dict) with a 'sheets' list. "
                    "Each sheet: {name, rows (list of row lists), header_row, "
                    "column_widths, freeze_panes, conditional_format, chart}. "
                    "A cell value like '=SUM(B2:B5)' becomes a live formula."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Absolute .xlsx path. Omit to auto-create in the temp dir. "
                    "A non-temp directory must have been picked by the user."
                ),
            },
        },
        "required": ["spec"],
    },
}


def _handle(args: dict[str, Any], task_id: str = "") -> str:
    result = excel_create(
        args.get("spec"),
        output_path=(str(args["output_path"]) if args.get("output_path") else None),
    )
    return json.dumps(result, ensure_ascii=False)


def _register() -> None:
    try:
        from .registry import registry

        registry.register(
            "excel_create",
            "office",
            _SCHEMA,
            _handle,
            permission_category="write_file",
            timeout_seconds=30.0,
        )
    except Exception:  # noqa: BLE001
        pass


_register()

__all__ = ["excel_create"]
