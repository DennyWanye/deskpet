# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("deskpet.agent.trace")


class IterationTracer:
    def __init__(
        self,
        *,
        trace_dir: Path,
        session_id: str = "",
        task_id: str = "",
    ) -> None:
        self.trace_dir = Path(trace_dir)
        self.session_id = session_id
        self.task_id = task_id or session_id or "trace"
        self._lock = threading.Lock()
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_dir_create_failed path=%s err=%s", self.trace_dir, exc)

    @property
    def path(self) -> Path:
        return self.trace_dir / f"{self.task_id}.jsonl"

    def record(self, event: dict) -> None:
        try:
            row = dict(event)
            row.setdefault("session_id", self.session_id)
            row.setdefault("task_id", self.task_id)
            row["ts"] = time.time()
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                self.trace_dir.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_record_failed err=%s", exc)
