"""Daily USD budget tracker.

Persists usage to a JSON file (atomic rewrite) so restart doesn't leak
extra budget. Rolls over on UTC date change — we don't track timezone
here because the *provider* bills in UTC regardless of the user's local
time; warning-at-80% is an early warning, not a precise local midnight.

Why a separate budget tracker from backend/billing/?
    billing/ ledger tracks CNY spend against HybridRouter's cloud
    provider (qwen) for P3. That's per-message fine-grained accounting.
    This module tracks *USD* spend against the P4 multi-provider LLM
    layer. They ARE intentionally separate: the existing CNY ledger
    doesn't understand Anthropic cache pricing, and coercing it would
    break the P3 cost-cap behaviors users already rely on.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from pathlib import Path
from typing import Optional

from llm.pricing import estimate_cost_usd
from llm.types import ChatUsage


class DailyBudget:
    """USD cap enforced across all providers.

    Thread-safe (internal Lock) because agent loop may trigger concurrent
    LLM calls from parallel tool dispatch; check_allowed() / add_usage()
    races would otherwise undercount.
    """

    WARNING_THRESHOLD: float = 0.80

    def __init__(self, cap_usd: float, state_path: Path) -> None:
        self.cap_usd = float(cap_usd)
        self.state_path = Path(state_path)
        self._lock = threading.Lock()
        # Warning hysteresis: flip crossed flag once per threshold per day.
        # This way we emit exactly one 80% and one 100% notification per
        # rollover, no matter how many chats add_usage fires during.
        self._warned_80 = False
        self._warned_100 = False
        self._state = self._load_or_init()

    # ────────────────────── persistence helpers ──────────────────────

    def _load_or_init(self) -> dict:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
        else:
            raw = {}
        today = self._utc_date_str()
        if raw.get("utc_date") != today:
            raw = {"utc_date": today, "spent_usd": 0.0, "entries": []}
        return raw

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    @staticmethod
    def _utc_date_str() -> str:
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

    def _maybe_rollover(self) -> None:
        today = self._utc_date_str()
        if self._state.get("utc_date") != today:
            self._state = {"utc_date": today, "spent_usd": 0.0, "entries": []}
            self._warned_80 = False
            self._warned_100 = False
            self._save()

    # ────────────────────── public api ──────────────────────

    def add_usage(self, provider: str, model: str, usage: ChatUsage) -> float:
        """Add one call's cost to today's running total. Returns new total."""
        with self._lock:
            self._maybe_rollover()
            cost = estimate_cost_usd(
                provider,
                model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
            self._state["spent_usd"] = float(self._state.get("spent_usd", 0.0)) + cost
            self._state.setdefault("entries", []).append(
                {
                    "provider": provider,
                    "model": model,
                    "input": usage.input_tokens,
                    "output": usage.output_tokens,
                    "cache_read": usage.cache_read_tokens,
                    "cache_write": usage.cache_write_tokens,
                    "cost_usd": round(cost, 6),
                }
            )
            # Keep the file bounded: last 500 entries per day is plenty for
            # debugging and keeps each rewrite under ~80KB.
            if len(self._state["entries"]) > 500:
                self._state["entries"] = self._state["entries"][-500:]
            self._save()
            return float(self._state["spent_usd"])

    def get_spent(self) -> float:
        """Current day's cumulative USD spend (rolls over on UTC midnight)."""
        with self._lock:
            self._maybe_rollover()
            return float(self._state.get("spent_usd", 0.0))

    def check_allowed(self) -> bool:
        """False once spend ≥ cap. Callers MUST block the LLM call."""
        return self.get_spent() < self.cap_usd

    def warning_threshold_crossed(self) -> Optional[float]:
        """Return 0.8 / 1.0 exactly once per threshold per UTC day, else None.

        Caller (agent loop / backend IPC) pushes `llm.budget.warning` event
        to frontend on each non-None return. Idempotent by design so we
        don't spam the notification tray.
        """
        with self._lock:
            self._maybe_rollover()
            spent = float(self._state.get("spent_usd", 0.0))
            ratio = spent / self.cap_usd if self.cap_usd > 0 else 0.0
            if ratio >= 1.0 and not self._warned_100:
                self._warned_100 = True
                # Skipping 80% and going straight to 100% means we already
                # passed the lower threshold — mark it warned too so the
                # next call doesn't emit a stale 0.8 "all clear → warning".
                self._warned_80 = True
                return 1.0
            if ratio >= self.WARNING_THRESHOLD and not self._warned_80:
                self._warned_80 = True
                return self.WARNING_THRESHOLD
            return None

    def reset(self) -> None:
        """Admin helper — resets today's counters (used in tests)."""
        with self._lock:
            self._state = {
                "utc_date": self._utc_date_str(),
                "spent_usd": 0.0,
                "entries": [],
            }
            self._warned_80 = False
            self._warned_100 = False
            self._save()
