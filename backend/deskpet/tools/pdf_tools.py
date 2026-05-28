# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PDF export tool — LibreOffice headless wrapper (pdf-export builtin skill).

``pdf_export(input_path)`` converts ``.docx`` / ``.pptx`` / ``.xlsx`` /
``.odt`` / etc. to ``.pdf`` by shelling out to LibreOffice in headless
mode (``soffice --headless --convert-to pdf``).

LibreOffice discovery order:

1. ``DESKPET_SOFFICE_PATH`` env var — set by the Tauri shell to the
   bundled portable LibreOffice (``resources/libreoffice/program/
   soffice.exe``). This is the production path.
2. Standard install locations under ``Program Files``.
3. ``soffice`` / ``soffice.exe`` on ``PATH``.

If none is found the tool returns ``{"ok": false, "error":
"soffice_missing"}`` — the skill tells the user the PDF component is
unavailable. It NEVER pretends to succeed.

Path policy: ``input_path`` must be authorized via ``office_pick_file``
(or be a temp file just produced by another office tool, which is
already temp-writable). Output defaults next to a temp file.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from . import office_paths

log = logging.getLogger(__name__)

_SOFFICE_TIMEOUT = 90.0


def find_soffice() -> Optional[str]:
    """Locate the LibreOffice ``soffice`` executable, or None."""
    env = os.environ.get("DESKPET_SOFFICE_PATH")
    if env and Path(env).is_file():
        return env

    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c

    which = shutil.which("soffice") or shutil.which("soffice.exe")
    return which


def pdf_export(
    input_path: str,
    *,
    output_path: Optional[str] = None,
) -> dict[str, Any]:
    """Convert ``input_path`` to PDF. Returns the .pdf path on success."""
    resolved = office_paths.resolve_for_read(input_path)
    if resolved is None:
        return {
            "ok": False,
            "error": (
                "input file not authorized or not found — call "
                "office_pick_file first, or pass a file just created by "
                "another office tool"
            ),
            "retriable": False,
        }

    soffice = find_soffice()
    if not soffice:
        return {
            "ok": False,
            "error": "soffice_missing",
            "message": (
                "PDF 组件 (LibreOffice) 未找到，无法导出 PDF。"
                "请确认安装包是否包含 LibreOffice。"
            ),
            "retriable": False,
        }

    # soffice writes "<stem>.pdf" into --outdir; we convert into a temp
    # dir then move to the requested destination.
    with tempfile.TemporaryDirectory(prefix="deskpet-pdf-") as tmpdir:
        try:
            proc = subprocess.run(
                [
                    soffice, "--headless", "--norestore",
                    "--convert-to", "pdf",
                    "--outdir", tmpdir,
                    str(resolved),
                ],
                capture_output=True,
                text=True,
                timeout=_SOFFICE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "pdf_export_timeout", "retriable": True}
        except OSError as exc:
            return {"ok": False, "error": f"soffice spawn failed: {exc}", "retriable": False}

        produced = Path(tmpdir) / (resolved.stem + ".pdf")
        if proc.returncode != 0 or not produced.is_file():
            return {
                "ok": False,
                "error": "pdf conversion failed",
                "detail": (proc.stderr or proc.stdout or "")[:300],
                "retriable": True,
            }

        try:
            dest = office_paths.resolve_for_write(
                output_path, default_prefix="deskpet-pdf", default_suffix=".pdf"
            )
        except office_paths.PathError as exc:
            return {"ok": False, "error": str(exc), "retriable": False}

        try:
            shutil.move(str(produced), str(dest))
        except OSError as exc:
            return {"ok": False, "error": f"cannot place output pdf: {exc}", "retriable": True}

    size = dest.stat().st_size if dest.is_file() else 0
    # WI-T1.2 D1：显式 emit artifacts[]（一等公民路径，保 BC）
    return {
        "ok": True,
        "path": str(dest),
        "size_bytes": size,
        "artifacts": [{
            "kind": "file",
            "path": str(dest),
            "mime": "application/pdf",
            "title": Path(str(dest)).name,
        }],
    }


# ---------------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------------
_SCHEMA = {
    "name": "pdf_export",
    "description": (
        "Convert a Word / PowerPoint / Excel / ODF document to PDF using the "
        "bundled LibreOffice engine. Returns the .pdf path. If the PDF engine "
        "is unavailable the result has error='soffice_missing' — tell the "
        "user honestly, never claim success. The input file must have been "
        "picked via office_pick_file or just created by another office tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input_path": {"type": "string", "description": "Absolute path of the source document."},
            "output_path": {
                "type": "string",
                "description": "Absolute .pdf path; omit to auto-create in temp.",
            },
        },
        "required": ["input_path"],
    },
}


def _handle(args: dict[str, Any], task_id: str = "") -> str:
    result = pdf_export(
        str(args.get("input_path", "")),
        output_path=(str(args["output_path"]) if args.get("output_path") else None),
    )
    return json.dumps(result, ensure_ascii=False)


def _register() -> None:
    try:
        from .registry import registry

        registry.register(
            "pdf_export",
            "office",
            _SCHEMA,
            _handle,
            permission_category="write_file",
            timeout_seconds=100.0,
        )
    except Exception:  # noqa: BLE001
        pass


_register()

__all__ = ["pdf_export", "find_soffice"]
