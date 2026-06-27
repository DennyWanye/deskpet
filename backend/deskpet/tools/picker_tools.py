# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Native file/folder picker tool — the user-consent gate for office skills.

The office skills (doc-edit / pdf-export / file-organize / ocr) need to
touch real files anywhere on disk, but :mod:`office_paths` only lets them
read/edit a path the *user* has explicitly chosen. This module is how the
user chooses: ``office_pick_file`` pops a real native Windows dialog.

Why a backend PowerShell dialog instead of a Tauri IPC roundtrip:

* The backend already runs on the user's desktop (``ppt_tools`` shells
  out to ``explorer /select``) — spawning a WinForms dialog is the same
  trust level, one layer, no Rust/JS glue, fully unit-testable.
* The picked path is authorized in-process immediately via
  :func:`office_paths.authorize_path` — no cross-process handoff.

The dialog runs in a short-lived ``powershell.exe`` (STA by default, so
WinForms dialogs work). A hidden top-most owner form keeps the dialog in
front of the pet window. If the user cancels, the tool returns
``{"ok": false, "cancelled": true}`` — not an error.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from . import office_paths

# ---------------------------------------------------------------------------
# PowerShell dialog snippets
# ---------------------------------------------------------------------------
# A hidden TopMost form is created as the dialog owner so the picker
# appears above the (always-on-top) pet window. We print a sentinel-
# prefixed line so parsing is unambiguous even if WinForms emits noise.
_SENTINEL = "DESKPET_PICK::"

_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
try {
__DIALOG__
} finally {
  $owner.Close()
}
"""

_DIALOG_FILE = r"""
  $d = New-Object System.Windows.Forms.OpenFileDialog
  $d.Title = '__TITLE__'
  $d.Filter = '__FILTER__'
  $d.Multiselect = $false
  $d.CheckFileExists = $true
  if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output ('DESKPET_PICK::' + $d.FileName)
  } else {
    Write-Output 'DESKPET_PICK::__CANCELLED__'
  }
"""

_DIALOG_SAVE = r"""
  $d = New-Object System.Windows.Forms.SaveFileDialog
  $d.Title = '__TITLE__'
  $d.Filter = '__FILTER__'
  $d.OverwritePrompt = $true
  if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output ('DESKPET_PICK::' + $d.FileName)
  } else {
    Write-Output 'DESKPET_PICK::__CANCELLED__'
  }
"""

_DIALOG_DIR = r"""
  $d = New-Object System.Windows.Forms.FolderBrowserDialog
  $d.Description = '__TITLE__'
  if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output ('DESKPET_PICK::' + $d.SelectedPath)
  } else {
    Write-Output 'DESKPET_PICK::__CANCELLED__'
  }
"""

_FILTERS = {
    "office": "Office 文档|*.docx;*.xlsx;*.pptx;*.doc;*.xls;*.ppt|所有文件|*.*",
    "doc": "Word 文档|*.docx;*.doc|所有文件|*.*",
    "image": "图片|*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp|所有文件|*.*",
    "any": "所有文件|*.*",
}

_CANCELLED = "__CANCELLED__"


def _build_script(kind: str, title: str, file_filter: str) -> str:
    if kind == "dir":
        dialog = _DIALOG_DIR
    elif kind == "save":
        dialog = _DIALOG_SAVE
    else:
        dialog = _DIALOG_FILE
    dialog = (
        dialog.replace("__TITLE__", _ps_escape(title))
        .replace("__FILTER__", _ps_escape(file_filter))
    )
    return _PS_TEMPLATE.replace("__DIALOG__", dialog)


def _ps_escape(s: str) -> str:
    """Escape a value for a single-quoted PowerShell string literal."""
    return s.replace("'", "''")


def _run_dialog(kind: str, title: str, filter_key: str, timeout: float) -> dict[str, Any]:
    file_filter = _FILTERS.get(filter_key, _FILTERS["any"])
    script = _build_script(kind, title, file_filter)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "picker_timeout", "retriable": False}
    except FileNotFoundError:
        return {"ok": False, "error": "powershell_unavailable", "retriable": False}

    out = proc.stdout or ""
    line = ""
    for raw in out.splitlines():
        if raw.strip().startswith(_SENTINEL):
            line = raw.strip()[len(_SENTINEL):]
            break
    if not line:
        return {
            "ok": False,
            "error": f"picker produced no result (exit={proc.returncode})",
            "retriable": False,
        }
    if line == _CANCELLED:
        return {"ok": False, "cancelled": True}
    authorized = office_paths.authorize_path(line)
    return {"ok": True, "path": authorized}


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------
def office_pick(kind: str = "file", *, title: str = "", filter_key: str = "office") -> dict[str, Any]:
    """Open a native picker. ``kind`` ∈ {file, save, dir}. Library entry
    point (tests call this directly with a patched subprocess)."""
    kind = (kind or "file").lower()
    if kind not in ("file", "save", "dir"):
        return {"ok": False, "error": f"unknown picker kind: {kind}", "retriable": False}
    default_titles = {
        "file": "请选择要打开的文件",
        "save": "请选择保存位置",
        "dir": "请选择文件夹",
    }
    return _run_dialog(kind, title or default_titles[kind], filter_key, timeout=300.0)


_SCHEMA = {
    "name": "office_pick_file",
    "description": (
        "Open a native Windows file/folder picker so the USER can choose a "
        "file or directory. You MUST call this before reading or editing any "
        "existing file on disk — office tools refuse paths the user has not "
        "picked. Returns the chosen absolute path (now authorized for this "
        "session). kind='file' to open an existing file, kind='dir' to pick a "
        "folder, kind='save' to choose a save destination."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["file", "save", "dir"],
                "default": "file",
                "description": "file=open existing, save=choose save path, dir=pick folder.",
            },
            "title": {
                "type": "string",
                "description": "Dialog title shown to the user (optional).",
            },
            "filter_key": {
                "type": "string",
                "enum": ["office", "doc", "image", "any"],
                "default": "office",
                "description": "Which file-type filter to show.",
            },
        },
        "required": [],
    },
}


def _handle(args: dict[str, Any], task_id: str = "") -> str:
    result = office_pick(
        str(args.get("kind") or "file"),
        title=str(args.get("title") or ""),
        filter_key=str(args.get("filter_key") or "office"),
    )
    return json.dumps(result, ensure_ascii=False)


def _register() -> None:
    try:
        from .registry import registry

        registry.register(
            "office_pick_file",
            "office",
            _SCHEMA,
            _handle,
            permission_category="read_file",
            timeout_seconds=310.0,
        )
    except Exception:  # noqa: BLE001
        pass


_register()

__all__ = ["office_pick"]
