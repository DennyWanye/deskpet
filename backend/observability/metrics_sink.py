# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-12 — minimal local observability sink (beta-100 readiness).

Writes anonymous, append-only usage counters to
``%AppData%\\deskpet\\metrics.jsonl`` so that — when a beta user sends
a feedback bundle (WI-02) — we can answer questions like "what's the
app-start success rate" / "what's the LLM call failure rate".

**Hard privacy guarantees** (enforced by code + tests):

* Only a fixed whitelist of event *names* is accepted; anything else
  is dropped silently.
* The ``detail`` payload is **key-whitelisted** — only keys in
  :data:`_ALLOWED_DETAIL_KEYS` survive. A caller literally cannot
  write a ``user_message`` / ``prompt`` / ``api_key`` field because
  those key names are not on the list. This is the real privacy wall:
  a length cap alone is useless ("我的密码是123456" is only 10 chars).
* Surviving values must additionally be short scalars (str / int /
  float / bool); strings over :data:`_MAX_DETAIL_STR` are dropped as
  a defence-in-depth second layer.
* No event ever carries conversation content, prompts, or API keys.
  There is simply no code path — and no permitted key — that could
  put them there.

**Rotation**: when the file exceeds :data:`_MAX_BYTES` we keep only the
tail (most recent lines). The sink never grows unbounded.

The sink is intentionally tiny and synchronous — a metrics write must
never be able to slow down or break the chat path, so every public
method swallows its own I/O errors (logged at debug).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


# Whitelisted event names. Anything not in here is dropped — this keeps
# the schema closed so a future careless caller can't invent an event
# that smuggles free text.
VALID_EVENTS = frozenset({
    "app_start",
    "app_start_failed",
    "llm_call_ok",
    "llm_call_failed",
    "skill_invoked",
    "crash",
    # WI-T1.7 last-mile artifact button clicks (PRD §5 G1 度量).
    "artifact_action",
    # WI-T2.4 verify metrics (PRD §5 健康区间).
    "verify_extractor.fallback_used",
    "verify.ephemeral_rescued",
    "verify.sig_invalid_filtered",
    "verifier.skipped_due_to_missing_toolchain",
    # WI-T2.1 v3 build_agent 工厂接电 — chat 启动时 emit；MR-T-1 硬证据
    # （用户 goal："必须有 boot smoke + metrics.jsonl 真出现 verify_* event"）.
    "verify_gate_init",
    # WI-T2.6 agent_loop 触发 nudge（fake-completion 拦截事件）.
    "verify_gate_nudge_injected",
    # WI-B3 Companion+Code v1 /goal end_turn rebound checker invocations.
    "goal_checker_invoked",
    # WI-C1 Companion+Code v1 Stage C — agent_parallel subagent lifecycle
    # (starting / completed / failed)；脱敏：task_id 由 caller 自管。
    "subagent_progress",
    # WI-G1 Companion+Code v2 — Multi-Agent Team workflow task lifecycle
    # (team_task_created / team_task_claimed / team_task_done)；脱敏：
    # task_id + team_id 都是 caller 自管的 uuid，无敏感内容。
    "team_task_created",
    "team_task_claimed",
    "team_task_done",
})

# Whitelisted ``detail`` keys. A caller can ONLY write these fields —
# every other key is dropped. This is the primary privacy wall: it is
# structurally impossible to log a ``user_message`` / ``prompt`` /
# ``api_key`` because those key names are not here. Keep this list
# tiny and operational — every entry must be an enum / id / number,
# never something that could carry user text.
_ALLOWED_DETAIL_KEYS = frozenset({
    "error_class",   # llm_call_failed: timeout / auth / rate_limit / ...
    "skill_name",    # skill_invoked: which builtin skill
    "latency_ms",    # llm_call_ok: round-trip time
    "duration_ms",   # generic timing
    "count",         # generic counter
    "file",          # crash: associated crash_reports filename
    "reason",        # app_start_failed: short reason code
    "provider",      # llm route: 'local' / 'cloud'
    "model",         # model id (not sensitive)
    "code",          # http status / exit code
    "attempt",       # retry attempt number
    "ratio",         # generic fraction
    "ok",            # generic bool
    # WI-T1.7 last-mile artifact_action detail keys (脱敏:无 path / 无 url).
    "action_id",     # open / show_in_folder / copy_path / save_as / preview
    "tool_name",     # ppt_create / excel_create / doc_create / ...
    # WI-T2.4/T2.5 verify metric details (无敏感内容).
    "verifier",      # file_exists / git_diff / build / test
    "missing",       # missing toolchain name (npm / pytest / git)
    # WI-T2.1 v3 verify_gate_init detail (脱敏: mode 是枚举, count 是数字).
    "mode",            # verify_gate_init: off / shadow / strict
    "patterns_loaded", # verify_gate_init: 加载的 ClaimPattern 数
    "session_id",      # verify_gate_nudge_injected: hash 后 sid（caller 自管脱敏）
    "nudge_count",     # verify_gate_nudge_injected: 第几次 nudge
    # WI-C1 Companion+Code v1 Stage C — agent_parallel subagent_progress
    # detail keys.
    "task_id",         # subagent_progress: caller-supplied short id
    "status",          # subagent_progress: starting / completed / failed
    # WI-G1 Companion+Code v2 Multi-Agent Team — team_task_* detail keys.
    "team_id",         # team_task_*: caller-supplied short id (uuid hex)
    "teammate_id",     # team_task_claimed/done: who did the work
})

# Max length of any *surviving* string value — defence-in-depth second
# layer behind the key whitelist. An ``error_class`` over 120 chars is
# already a caller bug.
_MAX_DETAIL_STR = 120

# File-size cap. Past this we truncate to the tail on next write.
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

# When rotating, keep roughly this many most-recent lines.
_ROTATE_KEEP_LINES = 2000


def sanitize_detail(detail: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return a privacy-safe copy of ``detail``.

    Rules (in order):
      1. ``None`` / empty → ``{}``.
      2. **Key whitelist** — keys not in :data:`_ALLOWED_DETAIL_KEYS`
         are dropped. This is the primary wall.
      3. Values must be str / int / float / bool. Containers (dict /
         list / object) are dropped.
      4. String values longer than :data:`_MAX_DETAIL_STR` are dropped
         entirely (defence-in-depth — a long ``error_class`` is a bug).

    Pure + deterministic → trivially testable.
    """
    if not detail:
        return {}
    out: dict[str, Any] = {}
    for k, v in detail.items():
        key = str(k)
        if key not in _ALLOWED_DETAIL_KEYS:
            continue  # primary privacy wall — unknown key, dropped
        if isinstance(v, bool):
            out[key] = v
        elif isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, str):
            if len(v) <= _MAX_DETAIL_STR:
                out[key] = v
            # else: dropped — too long to be a safe metric value
        # dicts / lists / objects: dropped
    return out


class MetricsSink:
    """Append-only JSONL metrics writer.

    Parameters
    ----------
    path:
        Absolute path to ``metrics.jsonl``. Parent dir is created on
        first write if missing.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        event: str,
        detail: Optional[dict[str, Any]] = None,
        *,
        now: Optional[float] = None,
    ) -> bool:
        """Append one event. Returns True if written, False if dropped.

        Dropped (returns False) when:
          * ``event`` not in :data:`VALID_EVENTS`
          * an I/O error occurs (logged at debug — never raised)

        ``now`` overrides the timestamp for deterministic tests.
        """
        if event not in VALID_EVENTS:
            log.debug("metrics_sink: dropped unknown event %r", event)
            return False
        ts = now if now is not None else time.time()
        row = {
            "ts": round(float(ts), 3),
            "event": event,
            "detail": sanitize_detail(detail),
        }
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._maybe_rotate()
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return True
        except OSError as exc:
            log.debug("metrics_sink: write failed: %s", exc)
            return False

    def _maybe_rotate(self) -> None:
        """Truncate the file to its tail when it exceeds the size cap.

        Called before every append. Cheap: only stats the file; the
        actual rewrite happens just on the rare crossing event.
        """
        try:
            if not self._path.exists():
                return
            if self._path.stat().st_size <= _MAX_BYTES:
                return
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = lines[-_ROTATE_KEEP_LINES:]
            self._path.write_text("\n".join(tail) + "\n", encoding="utf-8")
            log.debug(
                "metrics_sink: rotated %s (kept %d lines)",
                self._path, len(tail),
            )
        except OSError as exc:
            log.debug("metrics_sink: rotation failed: %s", exc)

    def read_all(self) -> list[dict[str, Any]]:
        """Read every event back as a list of dicts. Bad lines skipped.

        Used by the diagnostic-bundle collector (WI-02) and by tests.
        """
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for line in self._path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            log.debug("metrics_sink: read failed: %s", exc)
        return out

    def summary(self) -> dict[str, int]:
        """Aggregate counts per event name — quick health snapshot.

        Returns e.g. ``{"app_start": 12, "llm_call_ok": 340, ...}``.
        Derived stats (success rates) are left to the caller.
        """
        counts: dict[str, int] = {}
        for row in self.read_all():
            ev = row.get("event")
            if isinstance(ev, str):
                counts[ev] = counts.get(ev, 0) + 1
        return counts


# ---------------------------------------------------------------------
# Process-wide default sink — resolved lazily so import is side-effect free
# ---------------------------------------------------------------------

_default_sink: Optional[MetricsSink] = None


def get_default_sink() -> MetricsSink:
    """Return the process-wide :class:`MetricsSink` at
    ``<user_data>/metrics.jsonl``. Constructed on first call.

    Falls back to the system temp dir when ``paths.user_data_dir`` is
    unavailable (e.g. in stripped test environments).
    """
    global _default_sink
    if _default_sink is not None:
        return _default_sink
    try:
        from paths import user_data_dir  # type: ignore
        base = user_data_dir()
    except Exception:  # noqa: BLE001
        import tempfile
        base = Path(tempfile.gettempdir()) / "deskpet"
    _default_sink = MetricsSink(Path(base) / "metrics.jsonl")
    return _default_sink


def record(event: str, detail: Optional[dict[str, Any]] = None) -> bool:
    """Module-level convenience: record onto the default sink."""
    return get_default_sink().record(event, detail)
