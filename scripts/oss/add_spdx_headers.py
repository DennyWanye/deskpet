#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Add SPDX-FileCopyrightText + SPDX-License-Identifier headers to source files.

Idempotent: files already containing 'SPDX-License-Identifier' are skipped.

Scope (git-tracked only):
  - .py  -> `# ` comment
  - .ts  -> `// ` comment (excluding .d.ts)
  - .tsx -> `// ` comment
  - .rs  -> `// ` comment

Excluded path prefixes:
  .claude/, node_modules/, .venv/, venv/, .uv-python/, .uv-cache/,
  target/, dist/, build/, __pycache__/

Special handling:
  - Shebang lines (#! ...): header inserted AFTER the shebang
  - Python encoding declaration (# -*- coding: ... -*-): header inserted AFTER it
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

COPYRIGHT = "SPDX-FileCopyrightText: 2026 DennyWanye"
LICENSE_ID = "SPDX-License-Identifier: BUSL-1.1"

COMMENT = {
    ".py":  "# ",
    ".ts":  "// ",
    ".tsx": "// ",
    ".rs":  "// ",
}

EXCLUDED_PREFIXES = (
    ".claude/",
    "node_modules/",
    ".venv/",
    "venv/",
    ".uv-python/",
    ".uv-cache/",
    "target/",
    "dist/",
    "build/",
    "__pycache__/",
)


def is_excluded(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[: i + 1]) + "/"
        for ex in EXCLUDED_PREFIXES:
            if prefix.endswith("/" + ex) or prefix == ex:
                return True
    return False


def git_tracked_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "*.py", "*.ts", "*.tsx", "*.rs"],
        text=True,
    )
    return [line for line in out.splitlines() if line]


def already_has_spdx(text: str) -> bool:
    head = "\n".join(text.splitlines()[:15])
    return "SPDX-License-Identifier" in head


def make_header(ext: str) -> str:
    c = COMMENT[ext]
    return f"{c}{COPYRIGHT}\n{c}{LICENSE_ID}\n"


def insert_header(text: str, ext: str) -> str:
    header = make_header(ext)
    lines = text.splitlines(keepends=True)
    insert_at = 0

    # Skip BOM-only first line (rare)
    # Skip shebang
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    # For Python: skip encoding declaration if it follows the shebang or is first
    if ext == ".py":
        check_idx = insert_at
        if check_idx < len(lines) and "coding" in lines[check_idx] and (
            "-*-" in lines[check_idx] or "coding:" in lines[check_idx] or "coding=" in lines[check_idx]
        ):
            insert_at = check_idx + 1

    # If next line after insert point is non-empty, add a blank line after header
    suffix = ""
    if insert_at < len(lines) and lines[insert_at].strip() != "":
        suffix = "\n"

    return "".join(lines[:insert_at]) + header + suffix + "".join(lines[insert_at:])


def process_file(path: Path, check_only: bool = False) -> str:
    """Return one of: 'added', 'skipped-existing', 'skipped-empty', 'error',
    'missing' (check_only mode only)."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "error"
    if not text.strip():
        return "skipped-empty"
    if already_has_spdx(text):
        return "skipped-existing"
    if check_only:
        return "missing"
    new_text = insert_header(text, path.suffix)
    path.write_text(new_text, encoding="utf-8", newline="\n")
    return "added"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not modify files; exit 1 if any tracked source file lacks "
             "the SPDX header. Used by CI / oss-checks.yml.",
    )
    args = parser.parse_args()

    repo_root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip())

    files = git_tracked_files()
    counts = {"added": 0, "skipped-existing": 0, "skipped-empty": 0,
              "skipped-excluded": 0, "skipped-d.ts": 0, "error": 0,
              "missing": 0}
    missing_files: list[str] = []

    for rel in files:
        if is_excluded(rel):
            counts["skipped-excluded"] += 1
            continue
        if rel.endswith(".d.ts"):
            counts["skipped-d.ts"] += 1
            continue
        ext = Path(rel).suffix
        if ext not in COMMENT:
            continue
        path = repo_root / rel
        if not path.is_file():
            counts["error"] += 1
            continue
        result = process_file(path, check_only=args.check)
        counts[result] = counts.get(result, 0) + 1
        if result == "missing":
            missing_files.append(rel)

    mode = "check" if args.check else "apply"
    print(f"=== SPDX header pass complete (mode={mode}) ===")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    if args.check and counts["missing"] > 0:
        print()
        print(f"FAIL: {counts['missing']} file(s) missing SPDX header:")
        for rel in missing_files[:20]:
            print(f"  - {rel}")
        if len(missing_files) > 20:
            print(f"  ... and {len(missing_files) - 20} more")
        print()
        print("Fix locally: `python scripts/oss/add_spdx_headers.py`")
        return 1
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
