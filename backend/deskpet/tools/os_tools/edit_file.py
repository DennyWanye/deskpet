# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""edit_file tool — exact-string replacement, no regex.

Refuses if ``old_string`` is not unique unless ``replace_all=True``.
Permission category: ``write_file``.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. Legacy ``error`` strings preserved.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any


_EXAMPLES = [
    {"path": "main.py", "old_string": "old text", "new_string": "new text"},
    {
        "path": "config.toml",
        "old_string": "x",
        "new_string": "y",
        "replace_all": True,
    },
]


def _err(error: str, hint: str, **extra: Any) -> str:
    body: dict[str, Any] = {
        "ok": False,
        "error": error,
        "hint": hint,
        "examples": _EXAMPLES,
    }
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _whitespace_fallback(
    text: str,
    old: str,
    new: str,
) -> tuple[str, float] | None:
    old_lines = old.split("\n")
    if len(old_lines) != 1:
        return None

    target = old.strip()
    if not target:
        return None

    file_lines = text.split("\n")
    matches = [
        idx for idx, line in enumerate(file_lines) if line.strip() == target
    ]
    if len(matches) != 1:
        return None

    idx = matches[0]
    file_lines[idx] = _line_indent(file_lines[idx]) + new.strip()
    return "\n".join(file_lines), 1.0


def _anchor_fallback(
    text: str,
    old: str,
    new: str,
) -> tuple[str, float] | None:
    old_lines = old.split("\n")
    line_count = len(old_lines)
    if line_count < 2:
        return None

    file_lines = text.split("\n")
    if len(file_lines) < line_count:
        return None

    edge_count = 2 if line_count >= 4 else 1
    old_head = [line.strip() for line in old_lines[:edge_count]]
    old_tail = [line.strip() for line in old_lines[-edge_count:]]
    candidates: list[tuple[int, float]] = []

    for start in range(0, len(file_lines) - line_count + 1):
        window_lines = file_lines[start : start + line_count]
        window_head = [line.strip() for line in window_lines[:edge_count]]
        window_tail = [line.strip() for line in window_lines[-edge_count:]]
        if window_head != old_head or window_tail != old_tail:
            continue

        window_text = "\n".join(window_lines)
        confidence = difflib.SequenceMatcher(None, old, window_text).ratio()
        if confidence >= 0.85:
            candidates.append((start, confidence))

    if len(candidates) != 1:
        return None

    start, confidence = candidates[0]
    file_lines[start : start + line_count] = new.split("\n")
    return "\n".join(file_lines), confidence


def _did_you_mean_error(path: str, old: str, file_lines: list[str]) -> str:
    cands = difflib.get_close_matches(old, file_lines, n=3, cutoff=0.6)
    return _err(
        "no exact match for old_string",
        "未精确命中；见 did_you_mean 最相近行",
        path=path,
        matched_by="none",
        did_you_mean=[
            {"line": file_lines.index(cand) + 1, "text": cand} for cand in cands
        ],
    )


def edit_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    fuzzy = bool(args.get("fuzzy", True))

    # OpenSpec §D3 — companion session write-scope（见 write_file 注释）。
    _scope_root = args.get("_write_scope_root")
    if isinstance(path, str) and path and _scope_root:
        from agent.write_scope import write_scope_check as _ws_check

        _violation = _ws_check(path, scope_root=_scope_root)
        if _violation is not None:
            return _err(_violation, _violation, path=path)

    if not isinstance(path, str) or not path:
        return _err(
            "path required",
            "edit_file 的 path 字段必填，必须是已存在文件的路径。"
            "例如 {\"path\": \"main.py\", \"old_string\": \"a\", \"new_string\": \"b\"}。",
        )
    if not isinstance(old, str) or not old:
        return _err(
            "old_string required",
            "edit_file 的 old_string 字段必填，必须是要被替换的非空字符串。"
            "old_string 必须在文件中精确（区分大小写）匹配。",
        )
    if not isinstance(new, str):
        return _err(
            "new_string must be string",
            "edit_file 的 new_string 必须是字符串（可以是空字符串以删除片段）。",
            expected="string",
            got=type(new).__name__,
        )

    p = Path(path)
    if not p.exists() or not p.is_file():
        return _err(
            "FileNotFoundError",
            f"{path} 不存在或不是文件。请先用 list_directory 确认路径，"
            "或用 write_file 创建新文件。",
            path=path,
        )

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"读取 {path} 时操作系统报错。常见原因：路径是目录、"
            "权限不足、文件被占用。请确认 path 是可读的普通文件。",
            path=path,
        )

    count = text.count(old)
    if count == 0:
        if fuzzy:
            file_lines = text.split("\n")

            fallback = _whitespace_fallback(text, old, new)
            matched_by = "whitespace"
            if fallback is None:
                fallback = _anchor_fallback(text, old, new)
                matched_by = "anchor"

            if fallback is None:
                return _did_you_mean_error(path, old, file_lines)

            new_text, confidence = fallback
            try:
                p.write_text(new_text, encoding="utf-8")
            except OSError as exc:
                return _err(
                    f"OSError: {exc}",
                    f"写回 {path} 时操作系统报错。"
                    "常见原因：权限不足、磁盘已满、文件被其它进程占用。",
                    path=path,
                )

            return json.dumps(
                {
                    "replacements": 1,
                    "path": str(p.resolve()),
                    "matched_by": matched_by,
                    "confidence": confidence,
                },
                ensure_ascii=False,
            )

        return _err(
            "old_string not found",
            f"old_string 在 {path} 中找不到。请先用 read_file 看实际内容，"
            "确认 old_string 与文件中文本完全一致（包括大小写、空格、换行）。",
            path=path,
        )
    if count > 1 and not replace_all:
        return _err(
            f"old_string is not unique ({count} matches); use replace_all=true",
            f"old_string 在 {path} 中匹配到 {count} 次，不唯一。"
            "请加更多上下文让 old_string 在文件中唯一，"
            "或显式传 replace_all=true 一次性替换全部。",
            path=path,
            match_count=count,
            alternatives=["enlarge old_string for uniqueness", "pass replace_all=true"],
        )

    if replace_all:
        new_text = text.replace(old, new)
        replacements = count
    else:
        new_text = text.replace(old, new, 1)
        replacements = 1

    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"写回 {path} 时操作系统报错。"
            "常见原因：权限不足、磁盘已满、文件被其它进程占用。",
            path=path,
        )

    return json.dumps(
        {"replacements": replacements, "path": str(p.resolve())},
        ensure_ascii=False,
    )
