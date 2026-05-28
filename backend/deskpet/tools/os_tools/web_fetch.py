# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""web_fetch tool — `web_fetch(url, max_bytes=1_000_000)`.

Permission category: ``network``. Refuses non-http(s) schemes
(no file://, ftp://, javascript:, etc.).
Strips HTML to readable text for LLM consumption.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. Legacy ``error`` strings preserved.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse


_EXAMPLES = [
    {"url": "https://example.com"},
    {"url": "https://api.github.com/repos/python/cpython", "max_bytes": 50000},
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


def _strip_html(html: str) -> str:
    """Lightweight HTML→text. Tries trafilatura if available, else regex."""
    try:
        import trafilatura  # type: ignore
        out = trafilatura.extract(html) or ""
        if out:
            return out
    except Exception:
        pass
    # Fallback: drop tags
    import re
    text = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def web_fetch(args: dict[str, Any], task_id: str = "") -> str:
    url = args.get("url", "")
    max_bytes = int(args.get("max_bytes", 1_000_000) or 1_000_000)

    if not isinstance(url, str) or not url:
        return _err(
            "url required",
            "web_fetch 的 url 字段必填，必须是完整的 http(s) URL。"
            "例如 {\"url\": \"https://example.com\"}。",
        )

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return _err(
            f"scheme must be http(s), got {parsed.scheme!r}",
            f"web_fetch 只支持 http / https 协议，收到 {parsed.scheme!r}。"
            "如果要读本地文件请用 read_file；FTP / file:// 等协议不支持。",
            expected_schemes=["http", "https"],
            got_scheme=parsed.scheme,
        )

    try:
        import httpx
    except ImportError:
        return _err(
            "httpx not installed",
            "缺少 httpx 依赖，无法发起 HTTP 请求。"
            "请运行 pip install httpx 后重试。",
        )

    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            r = client.get(url)
        body_bytes = r.content[:max_bytes]
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            text = _strip_html(body_bytes.decode("utf-8", errors="replace"))
        else:
            text = body_bytes.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return _err(
            f"{type(exc).__name__}: {exc}",
            f"请求 {url} 失败。"
            "常见原因：网络不通、域名解析失败、对方服务器超时、SSL 证书错误。"
            "请确认 URL 可访问。",
            url=url,
        )

    return json.dumps(
        {
            "url": url,
            "status": r.status_code,
            "text": text[:max_bytes],
            "content_type": ctype,
        },
        ensure_ascii=False,
    )
