# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B2 — GoalChecker（PRD Stage B § B2）.

Lightweight LLM-backed判定: 给定 ``goal_text`` 和最近 working_messages，
判断 goal 是否达成。返 ``(done: bool, hint: str)``，``hint`` 是 done=False
时的"还差什么"提示，done=True 时为空串。

设计纪律:
  - **JSON 解析容错**: LLM 输出可能被 ```json ... ``` 包裹 / 含 markdown
    / 含前后解释文本 — 用尽 3 级 fallback 提取首个合法 JSON 对象。
  - **Safe-fail / 降级（R-T3 §15.4）**: LLM 调用异常 / 畸形 JSON →
    返 ``(False, "goal_check=skipped")``，意为"无法正向确认完成"。
    AgentLoop 看到 ``hint == "goal_check=skipped"`` 时**不调用 mark_done**
    （与旧 ``checker_error`` done=True 不同）——避免 checker 故障时静默
    标目标完成。降级事实同步 emit ``goal_check_skipped`` metric 供 shadow
    量化。这与 ``context_compressor.compress`` 失败不抛同源范式。
  - **prompt 简短**: 不喂全 working_messages（容易爆 token），只取最近
    5 轮 assistant message 摘要。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def _emit_goal_check_skipped(reason: str) -> None:
    """Best-effort metric emit for goal_check=skipped降级事实（R-T3）。

    失败不抛 — metric 系统不可用时不阻断 checker 降级逻辑。
    """
    try:
        from observability.metrics_sink import record as _metric  # noqa: PLC0415
        _metric("goal_check_skipped", {"reason": reason})
    except Exception:  # noqa: BLE001
        pass


# 最多喂给 checker 的最近 assistant 轮数
_RECENT_TURNS = 5
# 单条消息截断长度（防 prompt 爆 token）
_MSG_TRUNC = 400


_PROMPT_TEMPLATE = (
    "Goal: {goal_text}\n\n"
    "Recent assistant work (last {n} turns):\n"
    "{summary}\n\n"
    "Is the goal achieved? Output ONLY a JSON object on a single line, "
    "no markdown, no explanation:\n"
    '{{"done": true|false, "hint": "<what\'s missing if not done; empty if done>"}}'
)


def _recent_msgs_summary(working_msgs: list[dict[str, Any]]) -> str:
    """提取最近 _RECENT_TURNS 条 assistant 消息内容，每条截断。

    跳过 system / tool / user 消息（goal-checker 只看 assistant 产出）。
    返回换行分隔的 bullet list。
    """
    out: list[str] = []
    for m in reversed(working_msgs):
        if len(out) >= _RECENT_TURNS:
            break
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if content is None:
            continue
        if isinstance(content, list):
            # OpenAI content blocks → 拼接 text 块
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict) and blk.get("type") in ("text", "output_text")
            ]
            text = " ".join(p for p in parts if p)
        else:
            text = str(content)
        text = text.strip()
        if not text:
            continue
        if len(text) > _MSG_TRUNC:
            text = text[:_MSG_TRUNC] + "…"
        out.append(f"  - {text}")
    if not out:
        return "  (no assistant turns yet)"
    # 倒序 → 让最新一轮在末尾（更符合 LLM "what just happened" 直觉）
    return "\n".join(reversed(out))


_FENCED_JSON_RX = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
_BARE_JSON_RX = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """3 级 fallback 提 JSON 对象:

    1. 直 ``json.loads(raw.strip())`` — 最理想
    2. 找 ``` ```json ... ``` ``` fenced block
    3. 找首个 ``{...}`` substring 尝试 parse

    返 ``None`` 表示无法 parse。
    """
    s = raw.strip()
    # 1. 直 parse
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. fenced block
    m = _FENCED_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. 首个 {...}
    m = _BARE_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    return None


class GoalChecker:
    """LLM-backed goal-达成判定.

    Parameters
    ----------
    llm_call:
        ``async (prompt: str) -> str`` callable. 通常由 main.py lifespan
        注入，绑定 LLM registry 的 ephemeral subagent endpoint（参考
        ``verify_gate.consult_ephemeral_subagent`` 模式）。
    """

    def __init__(self, llm_call: Callable[[str], Awaitable[str]]) -> None:
        self.llm_call = llm_call

    async def check(
        self,
        goal_text: str,
        working_msgs: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        """判定 goal 是否达成。

        Returns
        -------
        (done, hint):
            done=True / hint="" → 达成；AgentLoop ``mark_done``。
            done=False / hint=<text> → 未达成；AgentLoop 回灌 hint
                给下一轮 LLM。
            done=True / hint="checker_error" → LLM 异常 safe-fail；
                AgentLoop ``mark_done`` 避免死循环。
        """
        summary = _recent_msgs_summary(working_msgs)
        prompt = _PROMPT_TEMPLATE.format(
            goal_text=goal_text,
            n=_RECENT_TURNS,
            summary=summary,
        )
        try:
            raw = await self.llm_call(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("goal_checker llm_call failed: %s", exc)
            _emit_goal_check_skipped("llm_error")
            return (False, "goal_check=skipped")

        if not isinstance(raw, str):
            logger.warning("goal_checker llm returned non-str: %r", type(raw))
            _emit_goal_check_skipped("non_str_response")
            return (False, "goal_check=skipped")

        obj = _extract_json(raw)
        if obj is None:
            logger.warning(
                "goal_checker could not parse JSON from llm output: %r",
                raw[:200],
            )
            # 无法 parse → 降级到"仅客观证据判定"信号（不默认放行）
            _emit_goal_check_skipped("parse_failed")
            return (False, "goal_check=skipped")

        done_val = obj.get("done")
        # 容错: "true" / "yes" / 1 也接受
        if isinstance(done_val, bool):
            done = done_val
        elif isinstance(done_val, str):
            done = done_val.strip().lower() in ("true", "yes", "1", "done")
        elif isinstance(done_val, (int, float)):
            done = bool(done_val)
        else:
            logger.warning(
                "goal_checker missing 'done' field; degrading to skipped",
            )
            _emit_goal_check_skipped("missing_done_field")
            return (False, "goal_check=skipped")

        hint_val = obj.get("hint", "")
        hint = str(hint_val) if hint_val is not None else ""
        if done:
            return (True, "")
        return (False, hint)


# ─── WI-2.3: build_alignment_prompt (pure, exported for tests) ───────────────

# 固定反谄媚前缀（硬编码，不可被 persona 覆盖）
_ANTI_SYCOPHANCY_PREFIX = (
    "你是冷静的验收员，只依据客观证据判定，不考虑用户情绪，"
    "宁可判未完成也不假装完成。\n\n"
)

_ALIGNMENT_PROMPT_TEMPLATE = (
    "{anti_sycophancy}"
    "原始目标: {goal_text}\n\n"
    "客观证据（来自工具 receipt + outcome_verifier，不含 persona/情感信息）:\n"
    "{evidence_lines}\n\n"
    "声明（来自 assistant，仅作对比）:\n"
    "{claim_lines}\n\n"
    "请仅依据以上客观证据判断原始目标是否已真正满足。\n"
    "输出 ONLY 单行 JSON（无 markdown）:\n"
    '{{"aligned": true|false, "gap": "<未满足点；若满足则空串>"}}'
)


def build_alignment_prompt(
    goal_text: str,
    artifacts: list[str],
    claims: list[str],
) -> str:
    """WI-2.3: 构建目标对照 prompt（纯函数，供 VerifyGate / tests 复用）。

    **HARD input whitelist**:
      - prompt 只含 goal_text + objective_evidence (artifacts/sha/diff/test) + claims
      - 不含 persona/人格 Component、用户情绪、偏好画像
      - 固定反谄媚前缀保证判定客观性

    Parameters
    ----------
    goal_text:
        原始用户目标文本（§1 锚）。
    artifacts:
        客观证据列表：receipt OK 记录、文件 sha、diff、test pass 等。
        由 VerifyGate._build_goal_alignment 或 outcome_verifier 提供。
    claims:
        assistant 声明列表（从 assistant_text 提取，仅作对比参考）。
    """
    evidence_lines = (
        "\n".join(f"  - {e}" for e in artifacts)
        if artifacts else "  (无客观证据)"
    )
    claim_lines = (
        "\n".join(f"  - {c}" for c in claims)
        if claims else "  (无声明)"
    )
    return _ALIGNMENT_PROMPT_TEMPLATE.format(
        anti_sycophancy=_ANTI_SYCOPHANCY_PREFIX,
        goal_text=goal_text,
        evidence_lines=evidence_lines,
        claim_lines=claim_lines,
    )


__all__ = ["GoalChecker", "build_alignment_prompt"]
