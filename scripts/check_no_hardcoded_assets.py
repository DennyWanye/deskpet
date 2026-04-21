#!/usr/bin/env python3
"""P3-S1 CI guard: no backend code may hardcode ``"assets"`` path segments.

After the P3-S1 rename ``backend/assets/`` → ``backend/models/`` and the
introduction of :mod:`backend.paths`, every model path must flow through
``resolve_model_dir(config.xxx.model_dir)``. A stray ``Path(...) / "assets"``
will silently break PyInstaller packaging (P3-S4) because the frozen layout
has no ``assets`` folder.

This script scans ``backend/*.py`` (excluding tests) and fails if it finds:

- String literals containing ``"assets/"`` or ``'assets/'``
- Path segments ``/ "assets"`` or ``/ 'assets'``

Exit 0 = clean. Exit 1 = offender list printed.

Intended for pre-commit + CI. Run manually with::

    python scripts/check_no_hardcoded_assets.py
"""
from __future__ import annotations

import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"

# Match either a quoted "assets/..." / "assets" literal, or a Path / "assets"
# segment. Comments and docstrings legitimately mention the old name (e.g.
# migration notes) — the regex deliberately targets path-shaped usage only.
_PATTERNS = [
    re.compile(r'["\']assets/[^"\']*["\']'),
    re.compile(r'/ *["\']assets["\']'),
]


def _is_comment_or_docstring_line(line: str) -> bool:
    """Best-effort filter: skip pure-comment lines.

    Triple-quoted docstrings aren't parsed (expensive); callers are expected
    to phrase migration notes as ``# assets`` comments or bare words without
    quoting the path literally.
    """
    stripped = line.lstrip()
    return stripped.startswith("#")


def find_offenders() -> list[str]:
    offenders: list[str] = []
    if not _BACKEND.exists():
        print(f"[check_no_hardcoded_assets] WARNING: {_BACKEND} not found")
        return offenders
    for py_file in _BACKEND.rglob("*.py"):
        rel = py_file.relative_to(_ROOT)
        # Skip test files — they legitimately reference legacy values
        # (e.g. test_legacy_tts_model_dir_normalized).
        if py_file.name.startswith("test_") or "/tests/" in str(rel).replace("\\", "/"):
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _is_comment_or_docstring_line(line):
                continue
            # Per-line escape hatch for legitimate legacy-migration code
            # (e.g. backend/config.py's legacy_prefixes tuple).
            if "p3-s1-allow-assets" in line.lower():
                continue
            for pat in _PATTERNS:
                if pat.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
                    break
    return offenders


def main() -> int:
    offenders = find_offenders()
    if offenders:
        print("Hardcoded 'assets' path detected (P3-S1 forbids this):")
        for line in offenders:
            print(f"  {line}")
        print(
            "\nFix: replace with `paths.resolve_model_dir(config.xxx.model_dir)`.\n"
            "See docs/superpowers/plans/2026-04-21-p3s1-model-dir-config.md."
        )
        return 1
    print("[check_no_hardcoded_assets] OK — no hardcoded 'assets' paths in backend/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
