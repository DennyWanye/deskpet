"""web_fetch tool — `web_fetch(url, max_bytes=1_000_000)`.

Permission category: ``network``. Refuses non-http(s) schemes
(no file://, ftp://, javascript:, etc.).
Strips HTML to readable text for LLM consumption.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse


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
        return json.dumps({"error": "url required"})

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return json.dumps(
            {"error": f"scheme must be http(s), got {parsed.scheme!r}"}
        )

    try:
        import httpx
    except ImportError:
        return json.dumps({"error": "httpx not installed"})

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
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return json.dumps(
        {
            "url": url,
            "status": r.status_code,
            "text": text[:max_bytes],
            "content_type": ctype,
        },
        ensure_ascii=False,
    )
