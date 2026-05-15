"""write_file tool — `write_file(path, content, overwrite=False)`.

Permission category: ``write_file``. Creates parent directories
automatically. Refuses to overwrite an existing file unless
``overwrite=True``.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. Legacy ``error`` strings preserved for back-compat.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_EXAMPLES = [
    {"path": "./notes/today.md", "content": "# Today\n- ..."},
    {"path": "main.go", "content": "package main\n"},
    {"path": "existing.txt", "content": "new", "overwrite": True},
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


def write_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    overwrite = bool(args.get("overwrite", False))

    # OpenSpec §D3 — companion session write-scope。chat handler 给陪伴
    # session 经 ToolRegistry.set_session_context 注入 ``_write_scope_root``；
    # 越界写直接拒绝（不是沙箱，是 session 类型语义）。code session /
    # write_scope_enforced=false 时不会注入该键 → scope_root=None → 不拦。
    _scope_root = args.get("_write_scope_root")
    if isinstance(path, str) and path and _scope_root:
        from agent.write_scope import write_scope_check as _ws_check

        _violation = _ws_check(path, scope_root=_scope_root)
        if _violation is not None:
            return _err(_violation, _violation, path=path)

    if not isinstance(path, str) or not path:
        # P6 bugfix 2026-05-14 (live-test R4): LLM 连续 3 次 write_file
        # 漏掉 path 字段（只发 content），触发 circuit breaker 熔断。
        # 加强 hint：明确指出当前漏字段 + 列出收到的 keys 帮 LLM debug。
        _received_keys = sorted(args.keys()) if isinstance(args, dict) else []
        return _err(
            "path required",
            "write_file 必须同时提供 path 和 content 两个字段。你这次只发了 "
            f"{_received_keys}，缺少 path。请重新生成完整的 tool_call，确保 "
            "arguments JSON 同时包含 path（目标文件路径）和 content（文件内容）。"
            "如果你想追加而不是覆盖现有文件，请改用 edit_file 工具。",
            received_keys=_received_keys,
        )
    # P5-S2 Phase 0 fix: require content key explicitly. Defaulting to
    # "" silently writes empty files which is rarely intended; force
    # the LLM to be explicit.
    if "content" not in args:
        return _err(
            "content required",
            "write_file 的 content 字段必填。如要写空文件请显式传 content=\"\"。"
            "例如 {\"path\": \"./hello.txt\", \"content\": \"hi\"}。",
        )
    content = args.get("content", "")
    if not isinstance(content, str):
        return _err(
            "content must be string",
            "write_file 的 content 必须是字符串。"
            f"当前传入的是 {type(content).__name__}。"
            "如要写二进制请先 base64 编码成字符串。",
            expected="string",
            got=type(content).__name__,
        )

    # P5-S2 G3 (2026-05-12): hard cap content length at the tool layer.
    # Models (deepseek-v4-pro etc.) corrupt JSON escapes in long tool_call
    # args ~30-40% of the time when streaming — we've observed parse_ok=
    # False on 6882/7150/7552/7850-char write_file args in production.
    # Even when the JSON does parse cleanly, the streaming reliability
    # drops sharply past ~4KB. Reject early with explicit append-mode
    # guidance so the LLM splits the call instead of wasting a turn.
    # The error envelope is classified `permanent_tool_error` by the
    # agent loop, which short-circuits the iteration — no wasted LLM
    # round-trip generating another doomed 7KB args.
    _CONTENT_HARD_CAP = 4096
    if len(content) > _CONTENT_HARD_CAP:
        return _err(
            "content too long",
            f"content 长度 {len(content)} > {_CONTENT_HARD_CAP} 字符上限。"
            "超过 4KB 的单次 write_file 在流式输出中 JSON 转义失败率很高，"
            "我已拒绝以避免浪费一轮 LLM 调用。请改用以下模式：\n"
            "  1. 第一次：write_file(path=..., content=<前 ≤4KB 部分>, mode='write')\n"
            "  2. 后续：write_file(path=..., content=<下一段>, mode='append')\n"
            "  3. 重复 append 直到完成。\n"
            "三个 3KB 的调用比一个 9KB 的调用快、稳、便宜。",
            content_length=len(content),
            limit=_CONTENT_HARD_CAP,
            suggested_chunks=max(1, (len(content) + _CONTENT_HARD_CAP - 1) // _CONTENT_HARD_CAP),
            alternatives=["split into multiple write_file calls with mode='append'"],
        )

    p = Path(path)
    if p.exists() and not overwrite:
        return _err(
            "FileExistsError",
            f"{path} 已存在。如要覆盖请加 overwrite: true，"
            "或改用 edit_file 做增量修改（更安全，不会丢老内容）。",
            path=path,
            alternatives=["pass overwrite=true", "use edit_file"],
        )

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        p.write_bytes(data)
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"写入 {path} 时操作系统报错。常见原因：路径是已存在的目录、"
            "父目录权限不足、磁盘已满、路径包含非法字符。"
            "请确认 path 是文件路径（非目录）且可写。",
            path=path,
        )

    return json.dumps(
        {"path": str(p.resolve()), "bytes_written": len(data)},
        ensure_ascii=False,
    )
