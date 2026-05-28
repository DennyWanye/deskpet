# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S1/S2: SessionSnapshot builder.

The supervisor LLM doesn't get the raw conversation — that's a deliberate
privacy + cost decision. Instead we build a structured snapshot per the
spec: ``session_id`` / ``status`` / ``last_activity_age_seconds`` /
``current_iteration`` / ``last_5_events`` / ``tool_signature_window`` /
``todos_state`` / ``last_error`` / ``context_token_pressure`` /
``user_goal`` (≤200 chars).

``user_goal`` comes from SessionDB's first user message in this sid,
truncated. Everything else is in-memory state from SessionActivity +
CodeModeManager + SessionDB's todo / message tables.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("deskpet.agent.snapshot")


# Rough char-per-token estimate — supervisor doesn't need precision,
# just a coarse "how much room does the agent's context have left"
# signal. 3.5 chars/token is a conservative middle ground for mixed
# CJK/English content.
_CHARS_PER_TOKEN_ESTIMATE = 3.5
_DEFAULT_CONTEXT_WINDOW = 200_000  # tokens; matches [agent].context_window_tokens


def _truncate(text: str, n: int) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) <= n:
        return text
    return text[: n - 3] + "..."


async def build_snapshot(
    sid: str,
    *,
    session_activity: Any,
    session_db: Optional[Any] = None,
    code_mode_manager: Optional[Any] = None,
    context_window_tokens: int = _DEFAULT_CONTEXT_WINDOW,
) -> dict[str, Any]:
    """Build a SessionSnapshot dict for the supervisor LLM.

    Returns ``{}`` if no session_activity entry exists (caller should
    skip — nothing to scan).
    """
    sa = await session_activity.get(sid)
    if sa is None:
        return {}

    snap: dict[str, Any] = sa.to_snapshot_dict()

    # ─── user_goal ──────────────────────────────────────────────────
    snap["user_goal"] = ""
    if session_db is not None:
        try:
            # Pull the first user message of this sid; the SessionDB
            # ``get_messages`` returns chronological order (oldest
            # first), so we just iterate until we find role=user.
            msgs = await session_db.get_messages(sid, limit=10)
            for m in msgs or []:
                if m.get("role") == "user" and m.get("content"):
                    snap["user_goal"] = _truncate(str(m["content"]), 200)
                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug("snapshot_user_goal_failed sid=%s error=%s", sid, exc)

    # ─── todos_state ────────────────────────────────────────────────
    snap["todos_state"] = []
    if session_db is not None and code_mode_manager is not None:
        try:
            csid = code_mode_manager.code_session_id(sid)
            if csid:
                todos = await session_db.get_code_todos(csid)
                now = time.time()
                # ``code_todos`` doesn't track per-row updated_at on
                # the existing schema; fall back to "all stale_seconds = 0"
                # rather than a wrong number. Watchdog still catches
                # stuck via the SessionActivity inactivity threshold.
                snap["todos_state"] = [
                    {
                        "content": _truncate(t.get("content", ""), 80),
                        "status": t.get("status", "pending"),
                        "stale_seconds": 0,
                    }
                    for t in (todos or [])
                ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("snapshot_todos_failed sid=%s error=%s", sid, exc)

    # ─── last_error (extract from recent_events ring buffer) ────────
    snap["last_error"] = ""
    for ev in reversed(snap.get("last_5_events", [])):
        if ev.get("type") == "error":
            snap["last_error"] = _truncate(ev.get("snippet", ""), 200)
            break

    # ─── context_token_pressure (rough estimate) ────────────────────
    snap["context_token_pressure"] = 0.0
    if session_db is not None and context_window_tokens > 0:
        try:
            msgs = await session_db.get_messages(sid, limit=200)
            total_chars = 0
            for m in msgs or []:
                total_chars += len(str(m.get("content") or ""))
                total_chars += len(str(m.get("reasoning_content") or ""))
            est_tokens = total_chars / _CHARS_PER_TOKEN_ESTIMATE
            snap["context_token_pressure"] = min(
                1.0, est_tokens / max(1, context_window_tokens)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("snapshot_token_pressure_failed sid=%s error=%s", sid, exc)

    return snap
