"""Provider response sanitizer — 2026-05-17 deepseek-inline-cot-dsml-sanitize.

chinzy-served deepseek-v4-pro sometimes emits its chain-of-thought and tool
calls *inline inside `content`* using deepseek delimiters
(`<｜begin▁of▁thinking｜>…<｜end▁of▁thinking｜>`) and its native textual
tool-call protocol (`<｜｜DSML｜｜tool_calls>` / `invoke` / `parameter`),
instead of the structured `reasoning_content` / OpenAI `tool_calls` fields.
Unsanitized, that markup leaked into `write_file` arguments and corrupted a
real user source file. These are pure functions (no provider/network deps)
so they unit-test red→green and live at the single provider chokepoint.

`｜` = U+FF5C (fullwidth vertical line), `▁` = U+2581.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_THINK_PAIR_RE = re.compile(
    r"<｜begin▁of▁thinking｜>.*?<｜end▁of▁thinking｜>", re.DOTALL
)
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_ORPHAN_THINK_RE = re.compile(r"<｜(?:begin|end)▁of▁thinking｜>")
_DSML_OPEN = "<｜｜DSML｜｜tool_calls>"
_DSML_BLOCK_RE = re.compile(r"<｜｜DSML｜｜tool_calls>.*", re.DOTALL)
_DSML_INVOKE_RE = re.compile(r'<｜｜DSML｜｜invoke name="([^"]+)">')
_DSML_PARAM_RE = re.compile(r'<｜｜DSML｜｜parameter name="([^"]+)"[^>]*>')

_MARKUP_HINTS = ("<｜", "<think>")


def _has_markup(text: str) -> bool:
    return any(tok in text for tok in _MARKUP_HINTS)


def strip_inline_reasoning(text: str) -> str:
    """Remove inline CoT delimiter blocks. Clean text returns unchanged.

    Order: matched deepseek pair → <think> pair → orphan handling
    (drop from a lone delimiter up to the first DSML marker, never past
    a real answer) → final guard removing any residual delimiter token.
    """
    if not text or not _has_markup(text):
        return text
    out = _THINK_PAIR_RE.sub("", text)
    out = _THINK_TAG_RE.sub("", out)
    m = _ORPHAN_THINK_RE.search(out)
    if m:
        dsml = out.find(_DSML_OPEN, m.end())
        if dsml != -1:
            # Incident shape: drop orphan delimiter + CoT, keep prefix
            # and the DSML block (the extractor consumes it next).
            out = out[: m.start()] + out[dsml:]
        # else: fall through to the token-only guard below — never
        # silently eat trailing text that might be a real answer.
    out = _ORPHAN_THINK_RE.sub("", out)
    return out


def extract_dsml_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Parse an inline `<｜｜DSML｜｜tool_calls>` block into structured tool
    calls and strip the block from the text. Malformed param payloads
    fall back to the raw string (logged WARNING) — markup is always
    removed so it can never leak downstream.
    """
    if not text or _DSML_OPEN not in text:
        return text, []
    bm = _DSML_BLOCK_RE.search(text)
    if not bm:
        return text, []
    block = bm.group(0)
    clean = text[: bm.start()].rstrip()
    calls: list[dict] = []
    invokes = list(_DSML_INVOKE_RE.finditer(block))
    for i, inv in enumerate(invokes):
        name = inv.group(1)
        seg_end = invokes[i + 1].start() if i + 1 < len(invokes) else len(block)
        seg = block[inv.end() : seg_end]
        args: dict = {}
        params = list(_DSML_PARAM_RE.finditer(seg))
        for j, pm in enumerate(params):
            pname = pm.group(1)
            p_end = params[j + 1].start() if j + 1 < len(params) else len(seg)
            payload = seg[pm.end() : p_end].strip()
            try:
                value = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "dsml_param_parse_failed name=%s preview=%s",
                    pname,
                    payload[:120],
                )
                value = payload
            args[pname] = value
        calls.append({"id": f"dsml_{i}", "name": name, "arguments": args})
    return clean, calls


def sanitize_response(
    content: str | None,
    tool_calls: list[dict] | None,
    *,
    enabled: bool = True,
) -> tuple[str, list[dict], bool]:
    """Orchestrator wired into the provider chokepoint.

    Returns (clean_content, tool_calls, extracted_any).
    - enabled=False → identity passthrough (legacy / rollback).
    - structured tool_calls present → trusted, DSML NOT re-parsed
      (no double-execute); markup still stripped from content.
    - no structured tool_calls + inline DSML → extracted into tool_calls.
    """
    tool_calls = tool_calls or []
    if not enabled:
        return content if content is not None else "", tool_calls, False
    text = content or ""
    clean = strip_inline_reasoning(text)
    if tool_calls:
        if _DSML_OPEN in clean:
            clean, _ = extract_dsml_tool_calls(clean)
        return clean, tool_calls, False
    clean, extracted = extract_dsml_tool_calls(clean)
    if extracted:
        return clean, extracted, True
    return clean, tool_calls, False
