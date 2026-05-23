"""Word document tools — python-docx wrapper (beta builtin skill).

Three tools power the ``doc-edit`` skill:

* ``doc_create(spec)``        — build a brand-new ``.docx`` from a
                                 structured element list.
* ``doc_read(file_path)``     — read an existing ``.docx`` into a
                                 structured outline so the model can
                                 decide *where* to edit.
* ``doc_edit(file_path, ops)``— apply a list of targeted edit ops to an
                                 existing document, preserving styles.

Path policy: ``doc_read`` / ``doc_edit`` only accept a path the user has
picked through ``office_pick_file`` (see :mod:`office_paths`). ``doc_create``
writes to temp or a user-picked directory.

Edit philosophy — *targeted ops, never whole-doc rewrite*. We replace
text inside the existing run when possible (formatting fully preserved),
and only fall back to a paragraph-level rebuild (paragraph style kept,
run formatting collapsed to run[0]) when the match spans runs. This is
what keeps "把甲方名字改一下" from mangling the rest of a contract.

Failure philosophy: bad input → ``{"ok": false, "error": ...}``; a
single failing op is marked ``skipped`` and the rest still apply.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from . import office_paths

log = logging.getLogger(__name__)

try:
    import docx
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    _HAS_DOCX = True
except Exception:  # noqa: BLE001
    _HAS_DOCX = False


_ALIGN = {
    "left": "LEFT",
    "center": "CENTER",
    "right": "RIGHT",
    "justify": "JUSTIFY",
}


def _coerce(obj: Any) -> Optional[Any]:
    """Accept a Python object or a JSON string of one."""
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except json.JSONDecodeError:
            return None
    return obj


# ---------------------------------------------------------------------------
# doc_create
# ---------------------------------------------------------------------------
def _add_element(document, el: dict[str, Any]) -> None:
    etype = (el.get("type") or "paragraph").lower()
    if etype == "heading":
        level = int(el.get("level", 1))
        level = max(0, min(level, 9))
        document.add_heading(str(el.get("text", "")), level=level)
    elif etype == "page_break":
        document.add_page_break()
    elif etype == "table":
        rows = el.get("rows") or []
        if not rows:
            return
        ncols = max((len(r) for r in rows), default=0)
        table = document.add_table(rows=len(rows), cols=ncols)
        table.style = "Table Grid"
        for ri, row in enumerate(rows):
            for ci in range(ncols):
                val = row[ci] if ci < len(row) else ""
                cell = table.cell(ri, ci)
                cell.text = str(val)
                if ri == 0 and el.get("header"):
                    for para in cell.paragraphs:
                        for run in para.runs:
                            run.bold = True
    else:  # paragraph
        para = document.add_paragraph()
        run = para.add_run(str(el.get("text", "")))
        if el.get("bold"):
            run.bold = True
        if el.get("italic"):
            run.italic = True
        size = el.get("font_size")
        if size:
            try:
                from docx.shared import Pt

                run.font.size = Pt(float(size))
            except Exception:  # noqa: BLE001
                pass
        align = _ALIGN.get(str(el.get("align", "")).lower())
        if align:
            para.alignment = getattr(WD_ALIGN_PARAGRAPH, align)


def doc_create(spec: Any, *, output_path: Optional[str] = None) -> dict[str, Any]:
    """Create a new .docx from ``spec`` = {title?, elements: [...]}.

    Element types: heading{text,level}, paragraph{text,bold,italic,
    align,font_size}, table{rows,header}, page_break.
    """
    if not _HAS_DOCX:
        return {"ok": False, "error": "python-docx not installed", "retriable": False}

    spec_obj = _coerce(spec)
    if not isinstance(spec_obj, dict):
        return {"ok": False, "error": "spec must be a dict or JSON object string", "retriable": False}

    elements = spec_obj.get("elements")
    if not isinstance(elements, list):
        return {"ok": False, "error": "spec.elements must be a list", "retriable": False}

    try:
        out_path = office_paths.resolve_for_write(
            output_path, default_prefix="deskpet-doc", default_suffix=".docx"
        )
    except office_paths.PathError as exc:
        return {"ok": False, "error": str(exc), "retriable": False}

    try:
        document = Document()
        title = spec_obj.get("title")
        for el in elements:
            if isinstance(el, dict):
                _add_element(document, el)
        if title:
            try:
                document.core_properties.title = str(title)
            except Exception:  # noqa: BLE001
                pass
        document.save(str(out_path))
    except Exception as exc:  # noqa: BLE001
        log.warning("doc_create failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"render failed: {exc}", "retriable": True}

    # WI-T1.2 D1：显式 emit artifacts[]（一等公民路径，保 BC）
    return {
        "ok": True,
        "path": str(out_path),
        "element_count": len(elements),
        "artifacts": [{
            "kind": "file",
            "path": str(out_path),
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "title": Path(str(out_path)).name,
        }],
    }


# ---------------------------------------------------------------------------
# doc_read
# ---------------------------------------------------------------------------
def doc_read(file_path: str) -> dict[str, Any]:
    """Read an existing .docx into a structured outline.

    Returns paragraphs (index, text, style) + tables (index, rows, cols).
    The path must have been authorized via the file picker.
    """
    if not _HAS_DOCX:
        return {"ok": False, "error": "python-docx not installed", "retriable": False}

    resolved = office_paths.resolve_for_read(file_path)
    if resolved is None:
        return {
            "ok": False,
            "error": (
                "file not authorized or not found — call office_pick_file "
                "first so the user can choose the document"
            ),
            "retriable": False,
        }

    try:
        document = Document(str(resolved))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"cannot open document: {exc}", "retriable": False}

    paragraphs = []
    for i, para in enumerate(document.paragraphs):
        style = ""
        try:
            style = para.style.name if para.style else ""
        except Exception:  # noqa: BLE001
            pass
        paragraphs.append({"index": i, "text": para.text, "style": style})

    tables = []
    for i, table in enumerate(document.tables):
        tables.append({
            "index": i,
            "rows": len(table.rows),
            "cols": len(table.columns),
        })

    return {
        "ok": True,
        "path": str(resolved),
        "paragraphs": paragraphs,
        "tables": tables,
    }


# ---------------------------------------------------------------------------
# doc_edit
# ---------------------------------------------------------------------------
def _replace_in_paragraph(para, find: str, replace: str) -> bool:
    """Replace ``find`` with ``replace`` inside one paragraph.

    Run-level first (formatting fully preserved). If the match spans
    runs, rebuild: put the whole replaced text into run[0] (keeps run[0]
    formatting + the paragraph style) and clear the rest.
    """
    if not find or find not in para.text:
        return False
    # Fast path: match contained in a single run.
    for run in para.runs:
        if find in run.text:
            run.text = run.text.replace(find, replace)
            return True
    # Slow path: match spans runs — rebuild paragraph text.
    new_text = para.text.replace(find, replace)
    if para.runs:
        para.runs[0].text = new_text
        for run in para.runs[1:]:
            run.text = ""
    else:
        para.add_run(new_text)
    return True


def _apply_op(document, op: dict[str, Any]) -> dict[str, Any]:
    kind = (op.get("op") or "").lower()
    paragraphs = document.paragraphs

    if kind == "replace":
        find = str(op.get("find", ""))
        replace = str(op.get("replace", ""))
        idx = op.get("paragraph_index")
        changed = 0
        if idx is not None:
            try:
                target = paragraphs[int(idx)]
            except (IndexError, ValueError, TypeError):
                return {"op": kind, "status": "skipped", "reason": "bad paragraph_index"}
            if _replace_in_paragraph(target, find, replace):
                changed = 1
        else:
            for para in paragraphs:
                if _replace_in_paragraph(para, find, replace):
                    changed += 1
        if changed == 0:
            return {"op": kind, "status": "skipped", "reason": f"no match for {find!r}"}
        return {"op": kind, "status": "ok", "changed": changed}

    if kind == "set_paragraph_text":
        try:
            target = paragraphs[int(op.get("index"))]
        except (IndexError, ValueError, TypeError):
            return {"op": kind, "status": "skipped", "reason": "bad index"}
        text = str(op.get("text", ""))
        if target.runs:
            target.runs[0].text = text
            for run in target.runs[1:]:
                run.text = ""
        else:
            target.add_run(text)
        return {"op": kind, "status": "ok"}

    if kind == "insert_paragraph":
        text = str(op.get("text", ""))
        style = op.get("style")
        after = op.get("after_index")
        new_para = document.add_paragraph(text)
        if style:
            try:
                new_para.style = str(style)
            except Exception:  # noqa: BLE001
                pass
        if after is not None:
            try:
                anchor = paragraphs[int(after)]
                anchor._p.addnext(new_para._p)
            except (IndexError, ValueError, TypeError):
                return {"op": kind, "status": "ok", "note": "appended (bad after_index)"}
        return {"op": kind, "status": "ok"}

    if kind == "set_table_cell":
        try:
            table = document.tables[int(op.get("table_index"))]
            cell = table.cell(int(op.get("row")), int(op.get("col")))
        except (IndexError, ValueError, TypeError):
            return {"op": kind, "status": "skipped", "reason": "bad table coordinates"}
        cell.text = str(op.get("text", ""))
        return {"op": kind, "status": "ok"}

    return {"op": kind or "?", "status": "skipped", "reason": "unknown op"}


def doc_edit(file_path: str, ops: Any) -> dict[str, Any]:
    """Apply a list of edit ops to an existing .docx, in place.

    Ops: replace{find,replace,paragraph_index?}, set_paragraph_text{index,
    text}, insert_paragraph{text,after_index?,style?}, set_table_cell{
    table_index,row,col,text}.

    A failing op is recorded as ``skipped`` — the rest still apply.
    """
    if not _HAS_DOCX:
        return {"ok": False, "error": "python-docx not installed", "retriable": False}

    resolved = office_paths.resolve_for_read(file_path)
    if resolved is None:
        return {
            "ok": False,
            "error": (
                "file not authorized or not found — call office_pick_file "
                "first so the user can choose the document"
            ),
            "retriable": False,
        }

    ops_list = _coerce(ops)
    if ops_list is None:
        ops_list = []
    if not isinstance(ops_list, list):
        return {"ok": False, "error": "ops must be a list", "retriable": False}

    try:
        document = Document(str(resolved))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"cannot open document: {exc}", "retriable": False}

    results = []
    for op in ops_list:
        if not isinstance(op, dict):
            results.append({"op": "?", "status": "skipped", "reason": "op not a dict"})
            continue
        try:
            results.append(_apply_op(document, op))
        except Exception as exc:  # noqa: BLE001 — one bad op never aborts the rest
            results.append({"op": op.get("op", "?"), "status": "skipped", "reason": str(exc)})

    applied = sum(1 for r in results if r.get("status") == "ok")
    if applied > 0:
        try:
            document.save(str(resolved))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"save failed: {exc}", "retriable": True}

    return {
        "ok": True,
        "path": str(resolved),
        "applied": applied,
        "ops": results,
    }


# ---------------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------------
_CREATE_SCHEMA = {
    "name": "doc_create",
    "description": (
        "Create a new Word .docx locally from a structured spec. Elements: "
        "heading{text,level}, paragraph{text,bold,italic,align,font_size}, "
        "table{rows,header}, page_break. Returns the file path."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spec": {"description": "Dict (or JSON string) {title?, elements: [...]}."},
            "output_path": {
                "type": "string",
                "description": "Absolute .docx path; omit to auto-create in temp.",
            },
        },
        "required": ["spec"],
    },
}

_READ_SCHEMA = {
    "name": "doc_read",
    "description": (
        "Read an existing .docx into a structured outline (paragraphs with "
        "index/text/style, tables with dimensions). Call this BEFORE doc_edit "
        "to learn where to edit. The file must first be chosen by the user "
        "via office_pick_file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the .docx."},
        },
        "required": ["file_path"],
    },
}

_EDIT_SCHEMA = {
    "name": "doc_edit",
    "description": (
        "Apply targeted edits to an existing .docx in place, preserving "
        "styles. Ops: replace{find,replace,paragraph_index?}, "
        "set_paragraph_text{index,text}, insert_paragraph{text,after_index?,"
        "style?}, set_table_cell{table_index,row,col,text}. The file must "
        "first be chosen by the user via office_pick_file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the .docx."},
            "ops": {"description": "List of edit-op dicts (or a JSON string of one)."},
        },
        "required": ["file_path", "ops"],
    },
}


def _handle_create(args: dict[str, Any], task_id: str = "") -> str:
    result = doc_create(
        args.get("spec"),
        output_path=(str(args["output_path"]) if args.get("output_path") else None),
    )
    return json.dumps(result, ensure_ascii=False)


def _handle_read(args: dict[str, Any], task_id: str = "") -> str:
    return json.dumps(doc_read(str(args.get("file_path", ""))), ensure_ascii=False)


def _handle_edit(args: dict[str, Any], task_id: str = "") -> str:
    result = doc_edit(str(args.get("file_path", "")), args.get("ops"))
    return json.dumps(result, ensure_ascii=False)


def _register() -> None:
    try:
        from .registry import registry

        registry.register("doc_create", "office", _CREATE_SCHEMA, _handle_create,
                           permission_category="write_file", timeout_seconds=30.0)
        registry.register("doc_read", "office", _READ_SCHEMA, _handle_read,
                           permission_category="read_file", timeout_seconds=20.0)
        registry.register("doc_edit", "office", _EDIT_SCHEMA, _handle_edit,
                           permission_category="write_file", timeout_seconds=30.0)
    except Exception:  # noqa: BLE001
        pass


_register()

__all__ = ["doc_create", "doc_read", "doc_edit"]
