# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 Code Mode — engineering assistant mode for the desktop pet.

Public surface:
    - CodeModeManager: singleton on service_context, tracks per-session
      enable/disable + project root binding
    - resolve_project_root: file-system side of project root resolution
    - maybe_suggest_code_mode: lightweight intent detector for autosuggest

The actual heavy lifting (new tools, prompt template, etc.) lives in
sibling modules: ``backend/deskpet/tools/code_tools/`` for the tool
implementations.
"""
from __future__ import annotations

from .state import CodeModeManager, CodeModeState
from .project_root import resolve_project_root, sanitize_project_name
from .intent_detector import maybe_suggest_code_mode

__all__ = [
    "CodeModeManager",
    "CodeModeState",
    "resolve_project_root",
    "sanitize_project_name",
    "maybe_suggest_code_mode",
]
