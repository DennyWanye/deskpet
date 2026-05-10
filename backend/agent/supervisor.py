"""P5-S2: SupervisorAgent — LLM-based diagnosis of stuck Code-mode sessions.

The watchdog calls ``SupervisorAgent.diagnose(sid, snapshot)`` when a
session crosses a stuck threshold. The supervisor:

1. Builds a Chinese system prompt + structured JSON request
2. Calls the configured LLM provider with a 30s hard timeout
3. Parses output into a ``SupervisorAction``; on any failure → ``wait/green``
4. Coerces ``cancel`` to ``ask_user`` (P5 doesn't ship cancel yet)
5. Persists to ``supervisor_hints`` (audit trail)
6. Pushes hint to ``nudge_queue`` for ``action=nudge``
7. Broadcasts ``supervisor_alert`` ws event for non-wait actions

The supervisor is **strictly best-effort**: any failure path falls back
to ``wait/green`` and logs. It must never throw out to the watchdog.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("deskpet.agent.supervisor")


# ─────────────── action protocol ─────────────────────────────────────


VALID_ACTIONS = ("wait", "nudge", "ask_user", "cancel")
VALID_SEVERITIES = ("green", "yellow", "red")


@dataclass
class SupervisorAction:
    """Output of a supervisor LLM call."""

    action: str = "wait"           # wait | nudge | ask_user | cancel (coerced)
    severity: str = "green"        # green | yellow | red
    diagnosis: str = ""            # ≤200 chars
    hint_for_main_agent: str = ""  # ≤500 chars; only used when action=nudge
    user_message: str = ""         # ≤120 chars; bubble text
    suggested_buttons: list[str] = field(default_factory=list)  # ≤2 items

    # Audit: where did this come from
    alert_id: str = ""
    raw_action: str = ""           # original LLM action before any coercion

    def is_actionable(self) -> bool:
        return self.action in ("nudge", "ask_user")


# ─────────────── prompt + parsing ────────────────────────────────────


_SYSTEM_PROMPT = (
    "你是 DeskPet 的 supervisor agent，负责监督 Code 模式下主 agent 的执行情况。\n"
    "用户委派主 agent 跑一个开发任务，但有时它会卡住（死循环调同一工具、长时间无活动、\n"
    "permission 弹窗没人理、报错、或者错认为已完成）。你的工作是审视一个结构化的状态\n"
    "快照，并给出干预建议。你只输出 JSON，不要任何额外文字、解释或 markdown 代码块包裹。\n"
    "\n"
    "保守原则：90% 的'看起来卡住'实际只是慢，默认应该 wait。仅当有明确证据（重复调用同\n"
    "工具/参数 3 次以上、或长时间无任何事件、或最近的事件是 error）才升级为 nudge / ask_user。\n"
    "\n"
    "输出 JSON schema：\n"
    "{\n"
    '  "action": "wait" | "nudge" | "ask_user" | "cancel",\n'
    '  "severity": "green" | "yellow" | "red",\n'
    '  "diagnosis": "<=200 chars 一句话诊断>",\n'
    '  "hint_for_main_agent": "<仅 nudge 时使用，<=500 chars，将作为 system 消息注入主 agent 下一轮>",\n'
    '  "user_message": "<给桌宠气泡的文字，<=120 chars，wait 时为空>",\n'
    '  "suggested_buttons": ["<最多 2 个按钮文本>"]\n'
    "}\n"
    "\n"
    "action 含义：\n"
    "- wait: 还在正常工作（长编译/大下载等）；不打扰用户。\n"
    "- nudge: 主 agent 在打转/走偏；注入 hint 让它换思路。\n"
    "- ask_user: 需要用户决策（permission 没人理、错误不可恢复需要用户确认中断等）。\n"
    "- cancel: 死循环不可恢复（请保留协议字段；初版会被强制改为 ask_user）。\n"
)


_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE
)


def _strip_json_fences(text: str) -> str:
    """Some models emit ```json ... ``` even when asked not to. Strip."""
    if not text:
        return ""
    m = _JSON_FENCE_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _safe_str(x: Any, max_len: int) -> str:
    s = "" if x is None else str(x)
    return s[:max_len]


def _parse_action(text: str, *, alert_id: str) -> SupervisorAction:
    """Parse LLM output into SupervisorAction. Falls back to wait on any error.

    Coerces ``cancel`` → ``ask_user`` per spec D3.
    """
    raw = _strip_json_fences(text or "")
    if not raw:
        return SupervisorAction(action="wait", severity="green", diagnosis="empty supervisor output", alert_id=alert_id)
    try:
        obj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("supervisor_parse_failed error=%s raw=%s", exc, raw[:200])
        return SupervisorAction(action="wait", severity="green", diagnosis="invalid json", alert_id=alert_id)
    if not isinstance(obj, dict):
        return SupervisorAction(action="wait", severity="green", diagnosis="non-object json", alert_id=alert_id)

    action = str(obj.get("action") or "wait").strip().lower()
    severity = str(obj.get("severity") or "green").strip().lower()
    if action not in VALID_ACTIONS:
        action = "wait"
    if severity not in VALID_SEVERITIES:
        severity = "green"

    raw_action = action
    # Coerce cancel → ask_user (D3): we keep the user_message but rewrite action
    coerced = False
    if action == "cancel":
        action = "ask_user"
        coerced = True

    diagnosis = _safe_str(obj.get("diagnosis"), 200)
    hint = _safe_str(obj.get("hint_for_main_agent"), 500)
    user_message = _safe_str(obj.get("user_message"), 120)
    if coerced and not user_message:
        user_message = "任务可能已无法恢复，是否要中断？"

    suggested = obj.get("suggested_buttons") or []
    if not isinstance(suggested, list):
        suggested = []
    suggested = [str(b)[:24] for b in suggested[:2] if b is not None]
    if coerced and not suggested:
        suggested = ["中断", "让它继续试"]

    return SupervisorAction(
        action=action,
        severity=severity,
        diagnosis=diagnosis,
        hint_for_main_agent=hint if action == "nudge" else "",
        user_message="" if action == "wait" else user_message,
        suggested_buttons=[] if action == "wait" else suggested,
        alert_id=alert_id,
        raw_action=raw_action,
    )


# ─────────────── agent ───────────────────────────────────────────────


HintAuditFn = Callable[[SupervisorAction, str], Awaitable[None]]
NudgeQueuePush = Callable[[str, Any], Awaitable[None]]
AlertBroadcast = Callable[[str, dict[str, Any]], Awaitable[None]]
SnapshotBuilder = Callable[[str], Awaitable[dict[str, Any]]]


class SupervisorAgent:
    """Wraps an LLM provider with a strict timeout + JSON parsing path."""

    def __init__(
        self,
        *,
        provider: Any,                          # OpenAI-compatible provider
        snapshot_builder: SnapshotBuilder,      # async (sid) -> snapshot dict
        nudge_queue_push: Optional[NudgeQueuePush] = None,
        broadcast: Optional[AlertBroadcast] = None,
        audit: Optional[HintAuditFn] = None,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._provider = provider
        self._build_snapshot = snapshot_builder
        self._push_hint = nudge_queue_push
        self._broadcast = broadcast
        self._audit = audit
        self._timeout = float(timeout_seconds)
        self._clock = clock

    async def diagnose(self, sid: str, snapshot: Optional[dict[str, Any]] = None) -> SupervisorAction:
        """Build snapshot (if not provided), call LLM, dispatch outcome.

        Returns the SupervisorAction even when action=wait (caller may
        inspect for telemetry). Never raises.
        """
        alert_id = uuid.uuid4().hex
        try:
            snap = snapshot if snapshot is not None else await self._build_snapshot(sid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("supervisor_snapshot_failed sid=%s error=%s", sid, exc)
            return SupervisorAction(action="wait", severity="green", diagnosis="snapshot_failed", alert_id=alert_id)

        if not snap:
            # No activity → nothing to diagnose
            return SupervisorAction(action="wait", severity="green", diagnosis="empty_snapshot", alert_id=alert_id)

        # Call LLM
        action = await self._call_llm(snap, alert_id=alert_id)

        # Dispatch
        try:
            await self._dispatch(sid, action)
        except Exception as exc:  # noqa: BLE001
            logger.exception("supervisor_dispatch_failed sid=%s action=%s error=%s", sid, action.action, exc)
        return action

    async def _call_llm(self, snapshot: dict[str, Any], *, alert_id: str) -> SupervisorAction:
        """One LLM round-trip with hard timeout. Errors → wait/green."""
        try:
            user_payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
        except Exception:
            user_payload = str(snapshot)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": "请审视以下 session 状态快照并按 schema 输出 JSON：\n\n" + user_payload},
        ]
        try:
            result = await asyncio.wait_for(
                self._provider.chat_with_tools(
                    messages,
                    tools=None,
                    max_tokens=512,
                    temperature=0.1,
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("supervisor_llm_timeout after=%.1fs sid=%s", self._timeout, snapshot.get("session_id"))
            return SupervisorAction(action="wait", severity="green", diagnosis="supervisor_timeout", alert_id=alert_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "supervisor_llm_failed sid=%s error=%s", snapshot.get("session_id"), exc
            )
            return SupervisorAction(action="wait", severity="green", diagnosis="supervisor_unavailable", alert_id=alert_id)

        content = (result or {}).get("content") if isinstance(result, dict) else None
        return _parse_action(content or "", alert_id=alert_id)

    async def _dispatch(self, sid: str, action: SupervisorAction) -> None:
        """Side effects for non-wait actions: push hint, broadcast, audit."""
        if action.action == "wait":
            return

        # 1. Audit BEFORE side effects so even a failed broadcast leaves a row
        if self._audit is not None:
            try:
                await self._audit(action, sid)
            except Exception as exc:  # noqa: BLE001
                logger.debug("supervisor_audit_failed sid=%s error=%s", sid, exc)

        # 2. nudge → push hint to queue
        if action.action == "nudge" and self._push_hint is not None and action.hint_for_main_agent:
            try:
                # ``nudge_queue.push`` takes (sid, hint_obj); we pass the
                # action itself so the queue caller can read alert_id.
                await self._push_hint(sid, action)
            except Exception as exc:  # noqa: BLE001
                logger.warning("supervisor_push_hint_failed sid=%s error=%s", sid, exc)

        # 3. Broadcast supervisor_alert
        if self._broadcast is not None:
            payload = {
                "session_id": sid,
                "alert_id": action.alert_id,
                "severity": action.severity,
                "action": action.action,
                "diagnosis": action.diagnosis,
                "user_message": action.user_message,
                "suggested_buttons": action.suggested_buttons,
            }
            try:
                await self._broadcast("supervisor_alert", payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("supervisor_broadcast_failed sid=%s error=%s", sid, exc)


# Convenience factory used by main.py wiring (S2.2).
def build_supervisor_hook(agent: SupervisorAgent) -> Callable[[str, dict[str, Any]], Awaitable[None]]:
    """Wrap a SupervisorAgent in the WatchdogLoop hook signature.

    The watchdog passes ``(sid, snapshot)`` per-tick; we forward to
    ``agent.diagnose`` with the prebuilt snapshot. Watchdog already
    isolates exceptions, but we add a try/except for defence in depth.
    """

    async def _hook(sid: str, snapshot: dict[str, Any]) -> None:
        try:
            await agent.diagnose(sid, snapshot=snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.exception("supervisor_hook_unhandled sid=%s error=%s", sid, exc)

    return _hook
