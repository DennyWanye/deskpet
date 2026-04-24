"""Generate task ids for agent loop runs.

Format: `task_<YYMMDDHHMMSS>_<short_uuid>` — sortable prefix gives
grep-friendly grouping by day, short suffix avoids collisions within
the same second. Example: `task_260424113045_a3f21b8c`.
"""
from __future__ import annotations

import datetime as _dt
import uuid


def new_task_id() -> str:
    """Return a fresh task id string."""
    now = _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%y%m%d%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"task_{stamp}_{short}"
