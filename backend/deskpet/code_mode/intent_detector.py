# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Lightweight keyword-based intent detector for "wants to start a project".

Used by the chat handler: when a user message in plain (non-code) mode
matches one of these patterns, the backend sends a one-shot
``code_mode_suggest`` control message to the frontend, which renders a
yellow banner with a Yes/Dismiss action. The current chat turn STILL
runs as a normal companion chat — we don't hijack the user's message.

We don't try to be clever (no LLM classifier) — false positives are
cheap (one banner the user dismisses), false negatives are also cheap
(user can manually click the 🔧 toolbar button). Keep this list short
and obvious.
"""
from __future__ import annotations

import re

# Cleaned-text triggers. Keep short; we lowercase + strip punctuation
# before matching.
_TRIGGERS = (
    # Chinese
    "做一个项目",
    "搞一个项目",
    "建个项目",
    "做个项目",
    "新项目",
    "做一个 app",
    "做一个应用",
    "做一个工具",
    "搭个脚手架",
    "生成代码",
    "写个 cli",
    "写一个 cli",
    "做一个 cli",
    "做个网站",
    "做个 demo",
    # English
    "scaffold",
    "build me a",
    "create a project",
    "create a new project",
    "set up a project",
    "make a cli",
    "make a script",
    "build an app",
    "starter project",
)

_PUNCT_RE = re.compile(r"[^\w\s一-鿿]+")


def maybe_suggest_code_mode(text: str) -> bool:
    """True if the message looks like "I want to start a project".

    Lowercases + strips punctuation before substring matching so things
    like "scaffold!" / "Scaffold." also fire.
    """
    if not text:
        return False
    cleaned = _PUNCT_RE.sub(" ", text.lower())
    return any(trigger in cleaned for trigger in _TRIGGERS)
