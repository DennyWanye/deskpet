# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-2.4 — ExternalEvaluator: 外部 / 多 persona 交叉验证（仅高后果目标触发）。

设计纪律：
  - **成本护栏**：`is_high_consequence_goal` 保守判定，仅命中时走一次 LLM 调用；
    普通只读目标完全不调 evaluator（plan §3 lock）。
  - **Safe-fail**：llm_call=None / 调用异常 → 默认返回 pass + 记 evaluator_skipped。
    高后果目标场景：传 ``conservative_on_error=True`` → 超时/错误改返 revise（保守拦截）
    + 提示手动确认，防 checker 故障时静默放行不可逆操作（R-T3 §15.4）。
  - **Persona 隔离**：evaluator prompt 不含人格/情感/偏好字段（与 2.3 同隔离策略）。
  - **防漂移**：evaluator 用不同 system persona，抵消单 agent 自我强化偏置。
  - **每 goal 最多 1 次**：由 agent_loop 调用方保证（FinalEvent 前仅调一次）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("deskpet.agent.external_evaluator")


# ──────────────────────────────────────────────────────────────────────────────
# 高后果关键词 & 工具集
# ──────────────────────────────────────────────────────────────────────────────

#: 目标文字中出现任一词 → high consequence
_HIGH_CONSEQUENCE_KEYWORDS: frozenset[str] = frozenset({
    # 财务 / 支付
    "支付", "付款", "购买", "买", "转账", "充值",
    # 不可逆删改
    "删除", "清空", "格式化", "销毁", "清除", "覆盖",
    # 发送 / 外发
    "发送", "发邮件", "发微信", "发短信", "推送",
    # 系统设置
    "系统设置", "设置密码", "修改密码", "重置", "卸载",
    # 英文变体
    "pay", "payment", "delete", "send", "purchase", "buy",
    "format", "destroy", "wipe", "reset",
})

#: 工具名中出现任一 → high consequence（写盘 / 产物 / 外发类）
_HIGH_CONSEQUENCE_TOOLS: frozenset[str] = frozenset({
    "file_write",
    "patch",
    "ppt_create",
    "excel_create",
    "doc_create",
    "pdf_export",
    "send_email",
    "send_message",
    "execute_shell",
    "run_code",
    "delete_file",
    "write_file",
    "save_file",
    "create_file",
})

#: 「纯只读」工具名集合 — 这些不构成高后果触发（即使 ≥5 条需另判）
_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "search",
    "read",
    "retrieve",
    "read_file",
    "fetch_url",
    "list_files",
    "get",
})

#: ≥5 工具调用视为「高投入」，对齐 hermes 阈值
_HIGH_TOOL_CALL_THRESHOLD: int = 5

#: reflection confidence 低于此值 → 升级到 evaluator
_LOW_CONFIDENCE_THRESHOLD: float = 0.3


# ──────────────────────────────────────────────────────────────────────────────
# is_high_consequence_goal
# ──────────────────────────────────────────────────────────────────────────────

def is_high_consequence_goal(
    goal_text: str,
    ledger: list[Any],
    changed_files: list[str],
    *,
    confidence: Optional[float] = None,
) -> bool:
    """判断目标是否为「高后果」，命中任一条件即返回 True。

    保守策略：误判为高后果只多一次 LLM 调用（可接受）；
    误判为低后果会漏 evaluator（更危险）→ 宁可宽。

    纯只读工具（web_search/read/retrieve）且无其他触发条件 → False（成本护栏）。

    Args:
        goal_text:    用户原始目标文字
        ledger:       本 session 的 ToolReceipt 列表（已 sig-filtered）
        changed_files: 本轮修改的文件路径列表
        confidence:   WI-2.1 structured reflection confidence（None = 不检查）

    Returns:
        True iff any condition fires.
    """
    # ① 写盘 / 产物 / 外发工具出现在 ledger
    for receipt in ledger:
        tool_name = getattr(receipt, "tool_name", "") or ""
        if tool_name in _HIGH_CONSEQUENCE_TOOLS:
            logger.debug("hc: write-tool=%s in ledger", tool_name)
            return True

    # ② goal_text 或工具名命中关键词
    text_lower = (goal_text or "").lower()
    for kw in _HIGH_CONSEQUENCE_KEYWORDS:
        if kw.lower() in text_lower:
            logger.debug("hc: keyword='%s' in goal_text", kw)
            return True

    # ③ ≥5 工具调用（高投入）— 只计 non-read-only 也算，保守取总数
    if len(ledger) >= _HIGH_TOOL_CALL_THRESHOLD:
        # BUT: if ALL tools are pure read-only → not high consequence (cost guard)
        non_read = [r for r in ledger
                    if getattr(r, "tool_name", "") not in _READ_ONLY_TOOLS]
        if non_read:
            logger.debug("hc: ≥5 tool calls with non-read tools (%d non-read)", len(non_read))
            return True
        # All read-only but ≥5 → still high consequence (multi-step high-investment)
        logger.debug("hc: ≥5 all-read-only tool calls")
        return True

    # ④ reflection confidence < 0.3 (agent 自己都没把握)
    if confidence is not None and confidence < _LOW_CONFIDENCE_THRESHOLD:
        logger.debug("hc: low confidence=%.2f < %.2f", confidence, _LOW_CONFIDENCE_THRESHOLD)
        return True

    # 纯只读 + 无关键词 + <5 tools + 正常 confidence → False
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Evaluator prompt
# ──────────────────────────────────────────────────────────────────────────────

_EVALUATOR_SYSTEM_PERSONA = (
    "你是一名严格、客观的质量评审员（QA Judge）。"
    "你的职责：从独立视角评判 AI 助手是否真正完成了用户目标。"
    "你不受助手的解释影响，只看客观证据（产物路径、执行收据）。"
    "你倾向于发现遗漏和不足，而非轻易放行。"
    "注意：本提示不包含任何角色扮演、情感偏好或人格设定，你只评估任务完成质量。"
)

_EVALUATOR_USER_TEMPLATE = """\
## 原始用户目标
{original_goal}

## 产物清单（路径 + 摘要）
{produced_artifacts}

## 客观执行证据（工具收据）
{objective_evidence}

## 对话摘要
{conversation_summary}

---
请评审上述任务完成质量，输出严格 JSON（不含其他文字）：
{{
  "quality_score": <0-10 整数>,
  "issues": ["具体问题1", "具体问题2", ...],
  "verdict": "pass" 或 "revise",
  "reason": "一句话总结"
}}

评分标准：
- 8-10 分：完全满足目标，产物完整可用
- 6-7 分：基本满足，有小瑕疵但不影响使用 → verdict=pass
- 4-5 分：部分满足，有明显缺陷需修复 → verdict=revise
- 0-3 分：严重不足，产物为空或完全偏离目标 → verdict=revise

`verdict="revise"` 的条件：quality_score < 6 **且** 有可操作的 issues。
"""


# ──────────────────────────────────────────────────────────────────────────────
# ExternalEvaluator
# ──────────────────────────────────────────────────────────────────────────────

class ExternalEvaluator:
    """外部 / 多 persona 交叉验证器（仅高后果目标触发）。

    使用独立的 evaluator persona（与对话 LLM、与 2.3 判定 LLM 均不同）——
    抵消单 agent 自我强化偏置（best-practices §3.1）。

    输入 WHITELIST（不含人格/情感/偏好，同 2.3 §5 隔离）：
      - original_goal: 用户原始目标
      - produced_artifacts: 产物路径列表
      - objective_evidence: 客观执行证据（工具收据等）
      - conversation_summary: 对话摘要

    输出：
      {"quality_score": 0-10, "issues": [...], "verdict": "pass|revise", "reason": "..."}

    provider=None → safe-fail，返回 pass + 记 evaluator_skipped。
    """

    _DEFAULT_PASS_THRESHOLD: int = 6  # quality_score < 此值 + verdict=revise → replan

    def __init__(
        self,
        llm_call: Optional[Callable[[str], Awaitable[str]]] = None,
        *,
        pass_threshold: int = _DEFAULT_PASS_THRESHOLD,
        conservative_on_error: bool = False,
    ) -> None:
        self._llm_call = llm_call
        self._pass_threshold = pass_threshold
        # R-T3 §15.4: True → 超时/错误时返回 revise（保守拦截），适用于高后果目标。
        # False（默认）→ 保持原 safe-fail pass 行为（BC）。
        self._conservative_on_error = conservative_on_error

    async def evaluate(
        self,
        original_goal: str,
        produced_artifacts: list[str],
        objective_evidence: list[str],
        conversation_summary: str,
    ) -> dict:
        """Evaluate task completion quality.

        Returns:
            dict with keys: quality_score (int), issues (list[str]),
            verdict ("pass" | "revise"), reason (str).

        Safe-fail: on any error or provider=None → {"verdict": "pass", ...}.
        """
        if self._llm_call is None:
            logger.info("evaluator_skipped: llm_call=None (provider not configured)")
            if self._conservative_on_error:
                # R-T3 §15.4: 高后果目标 + no provider → 保守拦截
                self._record_metric("evaluator_skipped", {"reason": "no_provider_conservative"})
                self._record_metric("evaluator_conservative_block", {"reason": "no_provider"})
                return {
                    "quality_score": 0,
                    "issues": ["evaluator unavailable — manual confirmation required"],
                    "verdict": "revise",
                    "reason": "evaluator_skipped (no provider) — conservative block; please verify manually",
                }
            self._record_metric("evaluator_skipped", {"reason": "no_provider"})
            return {
                "quality_score": 10,
                "issues": [],
                "verdict": "pass",
                "reason": "evaluator_skipped (no provider)",
            }

        prompt = self._build_prompt(
            original_goal=original_goal,
            produced_artifacts=produced_artifacts,
            objective_evidence=objective_evidence,
            conversation_summary=conversation_summary,
        )

        try:
            raw = await self._llm_call(prompt)
        except Exception as exc:  # noqa: BLE001 — safe-fail
            logger.warning("external_evaluator LLM call failed: %s", exc)
            if self._conservative_on_error:
                # R-T3 §15.4: 高后果目标超时/失败 → 保守拦 + 提示手动确认
                logger.warning("external_evaluator conservative block — manual confirmation required")
                self._record_metric("evaluator_skipped", {"reason": "llm_error_conservative"})
                self._record_metric("evaluator_conservative_block", {"reason": "llm_error"})
                return {
                    "quality_score": 0,
                    "issues": [f"evaluator failed ({exc}) — manual confirmation required"],
                    "verdict": "revise",
                    "reason": f"evaluator_skipped (error: {exc}) — conservative block",
                }
            self._record_metric("evaluator_skipped", {"reason": "llm_error", "error": str(exc)})
            return {
                "quality_score": 10,
                "issues": [],
                "verdict": "pass",
                "reason": f"evaluator_skipped (error: {exc})",
            }

        result = self._parse_result(raw)

        # Apply threshold: only trigger replan if BOTH conditions hold
        if result["verdict"] == "revise" and result["quality_score"] >= self._pass_threshold:
            logger.info(
                "evaluator: verdict=revise but quality_score=%d >= threshold=%d "
                "→ upgrading to pass",
                result["quality_score"], self._pass_threshold,
            )
            result = dict(result)
            result["verdict"] = "pass"

        logger.info(
            "external_evaluator verdict=%s quality_score=%d issues=%d",
            result["verdict"], result["quality_score"], len(result.get("issues", [])),
        )
        self._record_metric("evaluator_completed", {
            "verdict": result["verdict"],
            "quality_score": result["quality_score"],
        })
        return result

    def _build_prompt(
        self,
        original_goal: str,
        produced_artifacts: list[str],
        objective_evidence: list[str],
        conversation_summary: str,
    ) -> str:
        """Build the evaluator prompt (persona + user template).

        Strict input whitelist: NO persona/emotion/preference fields injected.
        """
        artifacts_text = "\n".join(f"- {a}" for a in produced_artifacts) or "（无）"
        evidence_text = "\n".join(f"- {e}" for e in objective_evidence) or "（无）"
        user_content = _EVALUATOR_USER_TEMPLATE.format(
            original_goal=original_goal,
            produced_artifacts=artifacts_text,
            objective_evidence=evidence_text,
            conversation_summary=conversation_summary or "（无摘要）",
        )
        # Combine system persona + user content into a single prompt
        # (since llm_call is a simple str→str callable)
        return f"[系统角色]\n{_EVALUATOR_SYSTEM_PERSONA}\n\n[任务]\n{user_content}"

    def _parse_result(self, raw: str) -> dict:
        """Parse LLM JSON output with 3-level fallback. Returns safe defaults on failure."""
        # Level 1: direct parse
        try:
            data = json.loads(raw)
            return self._normalize(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Level 2: fenced code block
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                return self._normalize(data)
            except (json.JSONDecodeError, ValueError):
                pass

        # Level 3: first {...} block
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                return self._normalize(data)
            except (json.JSONDecodeError, ValueError):
                pass

        logger.warning("external_evaluator: failed to parse LLM output → safe-fail pass")
        return {
            "quality_score": 10,
            "issues": [],
            "verdict": "pass",
            "reason": "parse_error (safe-fail)",
        }

    @staticmethod
    def _normalize(data: dict) -> dict:
        """Ensure required fields with safe defaults."""
        return {
            "quality_score": int(data.get("quality_score", 10)),
            "issues": list(data.get("issues", [])),
            "verdict": str(data.get("verdict", "pass")),
            "reason": str(data.get("reason", "")),
        }

    @staticmethod
    def _record_metric(event_type: str, payload: dict) -> None:
        """Best-effort metric recording (same pattern as other WI modules)."""
        try:
            from observability.metrics_sink import record as _metric  # noqa: PLC0415
            _metric(event_type, payload)
        except Exception:  # noqa: BLE001 — metric 失败不阻
            pass
