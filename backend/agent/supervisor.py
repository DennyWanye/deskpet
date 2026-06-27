# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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
    "快照，并给出干预建议。\n"
    "\n"
    "**关键输出要求**：\n"
    "- 直接输出 JSON 对象，不要任何前置解释、思考链（<think>...</think>）、\n"
    "  markdown 代码块（```json ... ```）或额外文字。\n"
    "- 第一个字符就必须是 `{`，最后一个字符必须是 `}`。\n"
    "- 输出务必简洁：诊断 ≤80 字，hint ≤200 字，user_message ≤80 字。\n"
    "- 输出长度建议在 300 字以内，避免被 token 截断。\n"
    "\n"
    "保守原则：90% 的'看起来卡住'实际只是慢，默认应该 wait。仅当有明确证据（重复调用同\n"
    "工具/参数 3 次以上、或长时间无任何事件、或最近的事件是 error）才升级为 nudge / ask_user。\n"
    "\n"
    "输出 JSON schema：\n"
    "{\n"
    '  "action": "wait" | "nudge" | "ask_user" | "cancel",\n'
    '  "severity": "green" | "yellow" | "red",\n'
    '  "diagnosis": "<=80 chars 一句话诊断>",\n'
    '  "hint_for_main_agent": "<仅 nudge 时用，<=200 chars，将作为 system 消息注入主 agent>",\n'
    '  "user_message": "<给桌宠气泡的文字，<=80 chars，wait 时为空>",\n'
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
# Thinking-mode models (deepseek-v4-pro, GLM-4.5, etc.) sometimes prefix
# their JSON output with a ``<think>...</think>`` chain-of-thought block.
# We strip those before parsing — same defensive cleanup the P4-S25 plan
# extractor uses.
_THINK_BLOCK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_json_fences(text: str) -> str:
    r"""Defensive cleanup before json.loads:

    1. Strip ``<think>...</think>`` chain-of-thought blocks (deepseek-v4-pro etc.)
    2. Strip triple-backtick json fences (some models add them anyway)
    3. If neither matches but a JSON object lives inside the text, slice
       from the first ``{`` to the matching close brace.
    """
    if not text:
        return ""
    text = _THINK_BLOCK_PATTERN.sub("", text).strip()
    m = _JSON_FENCE_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    # Last-resort: find first '{' and slice. JSON object boundary detection
    # is approximate (no brace counting) — _parse_action's json.loads is
    # the final arbiter. If it fails, _parse_action falls back gracefully.
    start = text.find("{")
    if start >= 0:
        end = text.rfind("}")
        if end > start:
            return text[start : end + 1].strip()
    return text.strip()


def _safe_str(x: Any, max_len: int) -> str:
    s = "" if x is None else str(x)
    return s[:max_len]


def _parse_action(text: str, *, alert_id: str) -> SupervisorAction:
    """Parse LLM output into SupervisorAction. On parse failure, surface
    an ``ask_user`` alert so the user knows supervisor noticed but
    couldn't decide — beats silent wait when the user is staring at a
    visibly stuck task wondering if anyone is watching.

    Coerces ``cancel`` → ``ask_user`` per spec D3.
    """

    def _parse_fallback(reason: str) -> SupervisorAction:
        return SupervisorAction(
            action="ask_user",
            severity="yellow",
            diagnosis=f"supervisor_parse_failed: {reason}"[:200],
            hint_for_main_agent="",
            user_message="发现 session 卡住，supervisor 输出无法解析。要中断吗？",
            suggested_buttons=["中断", "再等等"],
            alert_id=alert_id,
        )

    raw = _strip_json_fences(text or "")
    if not raw:
        return _parse_fallback("empty supervisor output")
    try:
        obj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor_parse_failed error=%s raw=%s", exc, raw[:200])
        return _parse_fallback(f"invalid_json: {str(exc)[:60]}")
    if not isinstance(obj, dict):
        return _parse_fallback("non_object_json")

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
        auto_mode_check: Optional[Callable[[], bool]] = None,
        auto_followup: Optional[Callable[[str, str], Awaitable[None]]] = None,
        timeout_seconds: float = 120.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._provider = provider
        self._build_snapshot = snapshot_builder
        self._push_hint = nudge_queue_push
        self._broadcast = broadcast
        self._audit = audit
        # P6 bugfix 2026-05-14 (live-test): when auto-mode is on, the user
        # explicitly delegated decision-making to the supervisor — do NOT
        # block on UI buttons. Convert ask_user → nudge (auto-continue)
        # so long-running code tasks proceed without manual clicks.
        # ``auto_mode_check()`` returns True if permission_gate's
        # auto_mode is currently enabled.
        # ``auto_followup(sid, trigger_text)`` directly spawns a chat
        # follow-up task (e.g. "<<supervisor_followup>>") without
        # routing through the UI button-click path.
        self._auto_mode_check = auto_mode_check
        self._auto_followup = auto_followup
        self._timeout = float(timeout_seconds)
        self._clock = clock

    async def diagnose(self, sid: str, snapshot: Optional[dict[str, Any]] = None) -> SupervisorAction:
        """Build snapshot (if not provided), call LLM, dispatch outcome.

        Returns the SupervisorAction even when action=wait (caller may
        inspect for telemetry). Never raises.

        P5-S2 Phase 7: when the snapshot's reason is ``max_iterations``
        and the LLM returned a wishy-washy ``ask_user`` (or wait), we
        post-process to a concrete ``nudge`` with a "stop and commit"
        hint. This unblocks the auto-resume path — orchestrator only
        auto-spawns on nudge, so silent ask_user means user has to
        click. For max_iterations specifically, we KNOW what to do
        (tell the LLM to commit), so skip the popup.
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

        # P5-S2 Phase 7: max_iterations rescue — bias toward concrete
        # nudge instead of supervisor's default conservatism. The LLM
        # already burned 50 turns; what we KNOW it needs to do is commit
        # to a finish, not ask the user permission to keep grinding.
        _trigger_reason = (snap.get("reason") or "").strip() if isinstance(snap, dict) else ""
        if _trigger_reason == "max_iterations" and action.action in ("ask_user", "wait"):
            logger.info(
                "supervisor_max_iter_rescue sid=%s original_action=%s",
                sid, action.action,
            )
            action = SupervisorAction(
                action="nudge",
                severity="yellow",
                diagnosis="max_iterations 强制收尾"[:200],
                hint_for_main_agent=(
                    "你已经跑了所有可用迭代。立即停止任何新的 tool_call。\n"
                    "在下一轮回复中：\n"
                    "1. 用一段话总结你已完成的核心成果（具体说改了什么文件、做了什么测试）。\n"
                    "2. 把所有未完成的子任务写到 todo_write 留给下次。\n"
                    "3. 然后必须 stop_reason=end_turn 收尾，不要再调任何工具。"
                ),
                user_message="代码模型循环了，我让它强制收尾你之前的成果",
                suggested_buttons=[],  # auto-resume, no user click needed
                alert_id=alert_id,
                raw_action=action.raw_action or action.action,
            )
            # Re-dispatch with the new action so audit + broadcast +
            # nudge_queue all reflect the rescue decision.
            try:
                await self._dispatch(sid, action)
            except Exception as exc:  # noqa: BLE001
                logger.exception("supervisor_max_iter_rescue_dispatch_failed sid=%s err=%s", sid, exc)
            return action

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
            # P5-S1 D fix: bumped max_tokens 512 → 2048. thinking-mode
            # models (deepseek-v4-pro etc.) can use 800-1500 tokens just
            # for the <think>...</think> chain-of-thought block before
            # they emit the JSON. 512 led to stop_reason='length' and
            # truncated JSON every call. 2048 leaves comfortable budget
            # for thinking + the ~300-token JSON output spec.
            result = await asyncio.wait_for(
                self._provider.chat_with_tools(
                    messages,
                    tools=None,
                    max_tokens=2048,
                    temperature=0.1,
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("supervisor_llm_timeout after=%.1fs sid=%s", self._timeout, snapshot.get("session_id"))
            # P5-S1 D fix: do NOT silently fall back to wait. The user
            # sees a stuck session and supervisor also failed; tell the
            # user explicitly so they can decide rather than left
            # wondering. Yellow severity = noticed but uncertain.
            return SupervisorAction(
                action="ask_user",
                severity="yellow",
                diagnosis="supervisor_timeout",
                hint_for_main_agent="",
                user_message="发现 session 卡住，但 supervisor LLM 也超时了。要不要中断？",
                suggested_buttons=["中断", "再等等"],
                alert_id=alert_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "supervisor_llm_failed sid=%s error=%s", snapshot.get("session_id"), exc
            )
            # P5-S1 D fix: same as above — surface "supervisor saw it
            # but couldn't diagnose" instead of silent wait. Often the
            # main agent's failures are network/provider issues that
            # ALSO affect supervisor's LLM call; the user needs to know.
            err_brief = str(exc)[:80] if str(exc) else type(exc).__name__
            return SupervisorAction(
                action="ask_user",
                severity="yellow",
                diagnosis=f"supervisor_unavailable: {err_brief}"[:200],
                hint_for_main_agent="",
                user_message="发现 session 卡住，supervisor 自己也连不上 LLM。要中断吗？",
                suggested_buttons=["中断", "再等等"],
                alert_id=alert_id,
            )

        content = (result or {}).get("content") if isinstance(result, dict) else None
        return _parse_action(content or "", alert_id=alert_id)

    async def _dispatch(self, sid: str, action: SupervisorAction) -> None:
        """Side effects for non-wait actions: push hint, broadcast, audit."""
        if action.action == "wait":
            return

        # P6 bugfix 2026-05-14 (live-test): auto-mode bypass.
        # When permission_gate.auto_mode is ON the user explicitly chose
        # "decide for me, don't block on prompts." Convert ask_user into
        # a self-driven nudge + immediate follow-up so the agent keeps
        # working long-running tasks without manual clicks.
        _auto_bypassed = False
        # P6 bugfix 2026-05-14b (live-test 2): if supervisor itself
        # couldn't reach LLM (provider down / 403 / timeout), DO NOT
        # auto-followup — main agent uses the same provider chain and
        # will fail the same way, creating an infinite retry loop. Let
        # the session stop and show a clear error. Detect via diagnosis
        # prefix the supervisor sets when its own LLM call fails.
        _supervisor_itself_down = (
            action.diagnosis or ""
        ).startswith(("supervisor_unavailable", "supervisor_timeout"))
        if (
            action.action == "ask_user"
            and self._auto_mode_check is not None
            and not _supervisor_itself_down
        ):
            try:
                _is_auto = bool(self._auto_mode_check())
            except Exception:
                _is_auto = False
            if _is_auto:
                logger.info(
                    "supervisor_ask_user_auto_continued sid=%s alert=%s "
                    "(auto_mode on; bypassing UI buttons)",
                    sid, action.alert_id,
                )
                # Convert to nudge so hint flows into queue normally.
                action.action = "nudge"
                _auto_bypassed = True
                # If no hint was set (LLM only emitted user_message),
                # synthesize one from the diagnosis so the next turn has
                # context to recover.
                if not action.hint_for_main_agent:
                    action.hint_for_main_agent = (
                        action.user_message
                        or action.diagnosis
                        or "继续按你判断的下一步推进任务。"
                    )
                # Clear suggested_buttons + rewrite user_message so the
                # UI shows "已自动继续" notice instead of clickable buttons
                # the user shouldn't need to touch.
                _orig_msg = action.user_message
                action.suggested_buttons = []
                action.user_message = (
                    f"[auto-mode] 已自动继续：{_orig_msg or action.diagnosis or '推进下一步'}"
                )[:120]
        elif _supervisor_itself_down and action.action == "ask_user":
            # Provider-level outage — log clearly so the user can see
            # the chain has died and act (check API key / balance).
            logger.warning(
                "supervisor_provider_outage_no_auto_followup sid=%s diagnosis=%s "
                "(would have looped infinitely retrying same dead provider)",
                sid, action.diagnosis,
            )

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

        # 2b. P6 bugfix 2026-05-14 (live-test): if we converted ask_user
        # → nudge above (auto mode), proactively spawn a follow-up chat
        # task. Without this the hint sits in the queue waiting for the
        # next user message — defeating auto mode. ``auto_followup``
        # creates the same task ``supervisor_user_choice + _is_continue``
        # would have, just driven by code instead of a UI click.
        if (
            action.action == "nudge"
            and self._auto_followup is not None
            and self._auto_mode_check is not None
        ):
            try:
                _is_auto2 = bool(self._auto_mode_check())
            except Exception:
                _is_auto2 = False
            if _is_auto2:
                try:
                    await self._auto_followup(sid, "<<supervisor_followup>>")
                    logger.info(
                        "supervisor_auto_followup_scheduled sid=%s alert=%s",
                        sid, action.alert_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "supervisor_auto_followup_failed sid=%s error=%s",
                        sid, exc,
                    )

        # 3. Broadcast supervisor_alert
        # P6 bugfix 2026-05-14 (用户反馈): auto-mode + 真实 long-running 任务时
        # supervisor trigger (b) running>900s 每 12 分钟触发一次，每次都弹
        # "[auto-mode] 已自动继续：..." 气泡。用户已委托决策，根本没必要看。
        # 静默化：auto-mode bypass 后只 audit (前面已经做了)，不再 broadcast
        # 给 UI。失败 / 严重错误（severity=red）仍然 broadcast 保留可见性。
        _silent_for_auto_mode = (
            _auto_bypassed
            and action.severity != "red"
        )
        if self._broadcast is not None and not _silent_for_auto_mode:
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
        elif _silent_for_auto_mode:
            logger.info(
                "supervisor_alert_silenced_auto_mode sid=%s alert=%s severity=%s "
                "(auto-mode handled it; not broadcasting UI bubble)",
                sid, action.alert_id, action.severity,
            )


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
