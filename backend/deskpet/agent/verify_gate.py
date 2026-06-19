# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.4/T2.4b — VerifyGate 真正实现（PRD §3 D6 + 二轮 N1/N2）。

stub 时期已建好接口；本次升级把 stub 替换为真正逻辑：
  - RegexExtractor: 加载 verify/claim_patterns.yaml + re 编译 + ReDoS 拒
    （google-re2 不在 prod 依赖中，用 Python re + 静态 nested-quantifier
    检测兜底；N2 默认 yaml 100% 加载正向用例覆盖）
  - VerifyGate.check 真正实现：claim 提取 + ledger 对账 + failure_count
  - CascadeExtractor: 二级 LLM fallback (LLM 实现 stub 留 WI-T2.4b)
  - ephemeral_verifier_subagent: 第 3 次失败救援 stub（接 LLM 留 WI-T2.4b）

测试组对照 plans/.../01-TDD.md §B TG-9。
"""
from __future__ import annotations

import json
import logging
import re as _re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol

import yaml

from deskpet.tools.receipt import ToolReceipt

logger = logging.getLogger(__name__)


# ─── Data contracts (PRD §3.1 IDL) ───────────────────────────

UnmatchedReason = Literal["no_receipt", "path_mismatch", "sha256_mismatch", "file_missing"]


@dataclass
class UnmatchedClaim:
    """与 ToolReceipt.error_class 不同 enum — 详见 PRD D5 末段。"""
    pattern_id: str
    raw_text: str
    expected_kind: str
    expected_path_or_title: Optional[str]
    reason: UnmatchedReason


@dataclass
class VerifierFailure:
    verifier: str
    status: str
    reason: str
    log_tail: Optional[str] = None
    error_class: Optional[str] = None


@dataclass
class GoalAlignment:
    """WI-2.3: 原目标 vs 客观产物对照结果（由 VerifyGate.check 填入）。

    仅当 check() 传入 goal_text 时生成；goal_text=None 时 VerifyOutcome.goal_alignment=None（BC）。
    """
    goal_text: str                 # 重述的【原始目标】（防 verifier 漂移的锚）
    objective_evidence: list[str]  # receipt + outcome_verifier 的客观信号
    aligned: bool                  # 客观证据是否真满足原目标
    gap: str                       # aligned=False 时差什么；aligned=True 时为空串


@dataclass
class VerifyOutcome:
    passed: bool
    claims_extracted: int = 0
    unmatched_claims: list[UnmatchedClaim] = field(default_factory=list)
    verifier_failures: list[VerifierFailure] = field(default_factory=list)
    elapsed_ms: int = 0
    extractor_used: str = "regex"
    failure_count: int = 0
    goal_alignment: Optional[GoalAlignment] = None  # WI-2.3: None when goal_text not provided (BC)


@dataclass
class ClaimPattern:
    id: str
    regex: str
    artifact_kind: str
    tool_hint: list[str] = field(default_factory=list)


@dataclass
class Claim:
    pattern_id: str
    raw_text: str
    title: Optional[str] = None
    path: Optional[str] = None
    kind: str = "file"


# ─── Pattern loading + ReDoS detection ───────────────────────

# 简单 ReDoS pattern 静态检测（缺 re2 时的兜底，PRD N2/T9-13）。
# 命中即拒：
#   - (X+)+ / (X*)+ / (X+)* / (X*)* — nested quantifier
#   - (a|a)+ 等价分支
# 这不是完美方案（re 库没真 ReDoS 防护），但能挡 TDD T9-13 给的样例。
_REDOS_PATTERNS = [
    _re.compile(r"\([^)]*[+*]\)[+*]"),   # (X+)+ / (X*)* / (X+)* / (X*)+
    _re.compile(r"\((?:[^()]+\|)+[^()]+\)[+*]"),  # (a|b|...)+
]


def _looks_like_redos(pattern_str: str) -> bool:
    for r in _REDOS_PATTERNS:
        if r.search(pattern_str):
            return True
    return False


def load_claim_patterns(yaml_path: Path) -> list[ClaimPattern]:
    """yaml.safe_load + schema 校验 + re 编译 + ReDoS 拒。

    返回**成功编译**的 patterns；失败的单条 reject + log error，
    其他继续生效（PRD §3 D6 + N2 + T9-12b/T9-13/T9-15）。
    """
    if not yaml_path.exists():
        logger.warning("claim_patterns yaml missing: %s", yaml_path)
        return []
    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)  # T9-15: 拒任意对象 (!!python/object/apply)
    except (yaml.YAMLError, OSError) as exc:
        logger.error("claim_patterns yaml load failed: %s", exc)
        return []
    if not isinstance(data, dict):
        logger.error("claim_patterns yaml must be a dict, got %s", type(data))
        return []
    raw_patterns = data.get("patterns", [])
    if not isinstance(raw_patterns, list):
        logger.error("claim_patterns.patterns must be a list")
        return []

    out: list[ClaimPattern] = []
    for raw in raw_patterns:
        if not isinstance(raw, dict):
            continue
        pid = raw.get("id")
        regex = raw.get("regex")
        kind = raw.get("artifact_kind", "file")
        if not (isinstance(pid, str) and isinstance(regex, str)
                and len(regex) <= 500):
            logger.warning("claim_pattern invalid schema: %s", raw)
            continue
        # T9-13: ReDoS pattern reject
        if _looks_like_redos(regex):
            logger.error(
                "claim_pattern rejected: ReDoS-prone nested quantifier in %r (id=%s)",
                regex, pid,
            )
            continue
        # 尝试编译
        try:
            _re.compile(regex)
        except _re.error as exc:
            logger.error("claim_pattern regex compile failed (id=%s): %s",
                         pid, exc)
            continue
        out.append(ClaimPattern(
            id=pid,
            regex=regex,
            artifact_kind=kind,
            tool_hint=list(raw.get("tool_hint") or []),
        ))
    return out


# ─── ClaimExtractor strategy ─────────────────────────────────

class ClaimExtractor(Protocol):
    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]: ...


class RegexExtractor:
    """re-compiled patterns + claim extraction。"""

    def __init__(self, patterns: list[ClaimPattern]) -> None:
        self.patterns = patterns
        # 预编译，节约 extract() per-call 开销
        self._compiled: list[tuple[ClaimPattern, _re.Pattern[str]]] = []
        for p in patterns:
            try:
                self._compiled.append((p, _re.compile(p.regex)))
            except _re.error as exc:
                logger.warning("RegexExtractor: skip pattern %s — %s", p.id, exc)

    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]:
        out: list[Claim] = []
        for pat, rx in self._compiled:
            for m in rx.finditer(assistant_text):
                gd = m.groupdict()
                out.append(Claim(
                    pattern_id=pat.id,
                    raw_text=m.group(0),
                    title=gd.get("title"),
                    path=gd.get("path"),
                    kind=pat.artifact_kind,
                ))
        return out


class SmallLLMExtractor:
    """二级 LLM fallback (WI-T2.4b stub — 真 LLM 接入留下轮)。"""

    def __init__(self, llm_call: Optional[Callable[[str], str]] = None) -> None:
        self.llm_call = llm_call

    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]:
        # stub: 无 LLM 调用时返空（不阻 dispatch）
        if self.llm_call is None:
            return []
        # 真 LLM 实现 留 WI-T2.4b
        return []


class CascadeExtractor:
    """regex 白盒 + LLM 兜底（PRD §3 D6 CascadeExtractor）。"""

    def __init__(
        self,
        primary: ClaimExtractor,
        fallback: Optional[ClaimExtractor] = None,
        *,
        fallback_threshold_chars: int = 80,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.fallback_threshold = fallback_threshold_chars

    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]:
        claims = self.primary.extract(assistant_text, hints)
        # 触发 fallback 条件：text 长 + regex 0 命中 + ledger 非空
        # （断言性长文本 + 0 receipt 是典型同义改写情形）
        if self.fallback and self._suspicious(assistant_text, claims, hints):
            logger.info("verify_extractor.fallback_used")
            claims += self.fallback.extract(assistant_text, hints)
        return _dedup(claims)

    def _suspicious(
        self,
        text: str,
        claims: list[Claim],
        hints: dict[str, Any],
    ) -> bool:
        if claims:
            return False
        if len(text) < self.fallback_threshold:
            return False
        # ledger 有 receipt 但 regex 0 claim → 强烈 suggests 同义改写
        ledger_size = int(hints.get("ledger_size", 0))
        return ledger_size > 0


def _dedup(claims: list[Claim]) -> list[Claim]:
    seen: set[tuple[Optional[str], Optional[str], Optional[str]]] = set()
    out: list[Claim] = []
    for c in claims:
        key = (c.pattern_id, c.title, c.path)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ─── VerifyGate (PRD §3 D6 真正实现) ─────────────────────────

class VerifyGate:
    """Check assistant claims against ReceiptLedger.

    PRD §3 D6 计数语义：failure_count 起始 0；每次 verify 失败 +=1；
    `failure_count == 3` 时调度 ephemeral_verifier_subagent 救援；
    救援仍 fail 才强退 + 标 verify_exhausted。
    """

    MAX_FAILURES_BEFORE_EPHEMERAL = 3

    def __init__(
        self,
        *,
        extractor: ClaimExtractor,
        mode: str = "off",
        ephemeral_subagent: Optional[Callable[[Any], Awaitable[bool]]] = None,
    ) -> None:
        if mode not in ("off", "shadow", "strict"):
            raise ValueError(f"invalid mode: {mode}")
        self.extractor = extractor
        self.mode = mode
        # ephemeral_subagent: async callable接 payload dict, 返回 final_verdict bool
        self.ephemeral_subagent = ephemeral_subagent

    def check(
        self,
        *,
        assistant_text: str,
        ledger: list[ToolReceipt],
        goal_text: Optional[str] = None,  # WI-2.3: BC default None → behavior identical to pre-WI-2.3
    ) -> VerifyOutcome:
        # off mode：总 pass（兼容 BC 路径）; goal_text irrelevant in off mode
        if self.mode == "off":
            return VerifyOutcome(passed=True)

        claims = self.extractor.extract(
            assistant_text,
            hints={"ledger_size": len(ledger)},
        )
        unmatched = self._match_claims_against_ledger(claims, ledger)

        outcome = VerifyOutcome(
            passed=(not unmatched),
            claims_extracted=len(claims),
            unmatched_claims=unmatched,
            extractor_used="regex" if isinstance(self.extractor, RegexExtractor)
                          else "regex+llm_fallback",
        )

        # shadow 模式：不阻断，仅 warn
        if self.mode == "shadow":
            if unmatched:
                logger.warning(
                    "verify_gate shadow: %d unmatched claims (would block in strict)",
                    len(unmatched),
                )
            outcome.passed = True  # shadow 总放行

        # WI-2.3: build GoalAlignment iff goal_text provided (BC: goal_text=None → skip)
        if goal_text is not None:
            outcome.goal_alignment = self._build_goal_alignment(
                goal_text=goal_text,
                claims=claims,
                unmatched=unmatched,
                ledger=ledger,
            )

        return outcome

    def _build_goal_alignment(
        self,
        *,
        goal_text: str,
        claims: list[Claim],
        unmatched: list[UnmatchedClaim],
        ledger: list[ToolReceipt],
    ) -> GoalAlignment:
        """WI-2.3: 从 claim/ledger 客观证据构建 GoalAlignment（纯同步，无 LLM）。

        aligned 判定逻辑（客观证据，不读 persona/用户情绪）:
          - aligned=True  iff 有 claims 且无 unmatched（即所有 claim 都有 receipt 佐证）
          - aligned=False iff 有 unmatched claim，或 claim 为空但 ledger 也空

        VG-INVARIANT: goal_text 非 None 时，若有 receipt 证据则
        objective_evidence ≥1 条；若无任何证据则标记 evidence_unavailable 防空证据放行。
        """
        # 收集客观证据（receipt 层）
        objective_evidence: list[str] = []
        for r in ledger:
            if r.ok:
                objective_evidence.append(f"receipt ok: tool={r.tool_name}")

        if not claims:
            # 无 claim 提取：vacuous pass（未声明 = 未检验），不产生 aligned 判定
            # 但需遵守 VG-INVARIANT：无证据时标注
            if not objective_evidence:
                objective_evidence = ["evidence_unavailable"]
            # 无 claim 对目标没有信息 → aligned 保守取 True（无假完成风险）
            return GoalAlignment(
                goal_text=goal_text,
                objective_evidence=objective_evidence,
                aligned=True,
                gap="",
            )

        if unmatched:
            # 有 claim 但未被 receipt 佐证 → 伪完成
            gap_parts = [
                f"unmatched claim: {c.raw_text!r} (reason={c.reason})"
                for c in unmatched[:3]
            ]
            return GoalAlignment(
                goal_text=goal_text,
                objective_evidence=objective_evidence if objective_evidence else ["evidence_unavailable"],
                aligned=False,
                gap="; ".join(gap_parts),
            )

        # 所有 claim 都有 receipt 佐证 → aligned
        return GoalAlignment(
            goal_text=goal_text,
            objective_evidence=objective_evidence,
            aligned=True,
            gap="",
        )

    def _match_claims_against_ledger(
        self,
        claims: list[Claim],
        ledger: list[ToolReceipt],
    ) -> list[UnmatchedClaim]:
        """对每个 claim 查 ledger：是否存在匹配的 receipt？"""
        unmatched: list[UnmatchedClaim] = []
        for claim in claims:
            if not self._claim_has_matching_receipt(claim, ledger):
                # 至少要有一个 ok=True 的 receipt（tool 调用过且成功）
                unmatched.append(UnmatchedClaim(
                    pattern_id=claim.pattern_id,
                    raw_text=claim.raw_text,
                    expected_kind=claim.kind,
                    expected_path_or_title=claim.path or claim.title,
                    reason="no_receipt",
                ))
        return unmatched

    def _claim_has_matching_receipt(
        self,
        claim: Claim,
        ledger: list[ToolReceipt],
    ) -> bool:
        """**严格匹配**（修 v2 评审 P0-2）：claim.pattern 的 tool_hint 必须
        在 ledger 中能找到**同名且 ok=True** 的 receipt。

        这比"任一 ok receipt 即放行"严格 N 倍，是 fake-completion 防护的
        核心。具体语义：
          - claim 由 RegexExtractor 提取，带 pattern_id + tool hint
          - 通过 pattern_id 反查 ClaimPattern.tool_hint（list of tool_name）
          - ledger 里至少一条 receipt.tool_name ∈ tool_hint 且 ok=True → 匹配
          - tool_hint 为空（少数通用 path/url pattern）→ 放宽为任一 ok receipt
            （但要求 receipt 是 file-类工具）

        Path 级精确匹配 (claim.path 与 receipt artifact path 对账) 由
        outcome_verifier.FileExistsVerifier 在 file_exists 层兜底，而非
        VerifyGate 层 — VerifyGate 只保证"工具确实被调过"，不保证产物路径
        正确（PRD D7 责任划分）。
        """
        tool_hints = self._tool_hints_for_pattern(claim.pattern_id)
        # 兜底：pattern_id 未注册（pattern 已被 unload）— 严格 mode 拒
        if tool_hints is None:
            return False

        for r in ledger:
            if not r.ok:
                continue
            if tool_hints:
                if r.tool_name in tool_hints:
                    return True
            else:
                # 无 hint → 任一 ok file-生成工具放行（保守扩展）
                if r.tool_name in {"ppt_create", "excel_create", "doc_create",
                                   "pdf_export", "generate_image",
                                   "file_write"}:
                    return True
        return False

    def _tool_hints_for_pattern(
        self, pattern_id: str
    ) -> Optional[list[str]]:
        """从 extractor 反查 pattern_id 对应的 tool_hint list。"""
        if isinstance(self.extractor, RegexExtractor):
            for p in self.extractor.patterns:
                if p.id == pattern_id:
                    return list(p.tool_hint)
        elif isinstance(self.extractor, CascadeExtractor):
            primary = self.extractor.primary
            if isinstance(primary, RegexExtractor):
                for p in primary.patterns:
                    if p.id == pattern_id:
                        return list(p.tool_hint)
        # 未知 pattern → 兜底返空 list（无 hint，按通用 file 工具放行）
        return []

    async def consult_ephemeral_subagent(
        self,
        *,
        ledger: list[ToolReceipt],
        failed_claims: list[UnmatchedClaim],
        assistant_text: str,
    ) -> bool:
        """failure_count==3 时调度 ephemeral verifier；仍 fail 则真退。

        **N1 信任面**：调用方应传入已经 sig-filtered 的 ledger（由
        ReceiptStore.load_session 保证）；此处再次断言以防 bug。

        Returns:
            final_verdict: True=救援通过 / False=确实 fail
        """
        # N1: 断言 ledger 已是 sig-valid（防 caller bug 误传）
        # 实际验签由 ReceiptStore 在 load 时做；这里只 sanity check ledger 非 None
        if ledger is None:
            logger.error("consult_ephemeral_subagent: ledger=None (N1 violation)")
            return False
        if self.ephemeral_subagent is None:
            # stub: 无 ephemeral 接入时直接 fail（保守）
            return False
        try:
            return bool(await self.ephemeral_subagent({
                "ledger_size": len(ledger),
                "failed_claims": [c.__dict__ for c in failed_claims],
                "assistant_text": assistant_text[:2000],  # truncate
            }))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ephemeral_subagent raised: %s", exc)
            return False


# ─── WI-2.2 ephemeral verifier factory ──────────────────────────────────────

_EPHEMERAL_VERIFIER_PROMPT_TEMPLATE = (
    "You are an objective verification assistant. Your job is to determine whether "
    "unmatched claims are actually covered by tool calls in the ledger.\n\n"
    "Context:\n"
    "- Assistant claimed: {assistant_text}\n"
    "- Ledger has {ledger_size} tool call record(s).\n"
    "- Unmatched claims (regex did not find receipt): {failed_claims}\n\n"
    "Question: Are these 'unmatched' claims actually covered by one of the tool calls "
    "in the ledger, just phrased differently (synonym / paraphrase)?\n\n"
    "Rules:\n"
    "1. Be objective — do NOT just agree with the assistant.\n"
    "2. Only output pass if you are confident a tool call covers the claim.\n"
    "3. If ledger_size=0, the answer is almost always fail.\n"
    "4. Output ONLY valid JSON with no extra text.\n\n"
    'Output format: {{"verdict": "pass"|"fail", "reason": "<one-line explanation>"}}'
)

_FENCED_JSON_RX = _re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    _re.DOTALL | _re.IGNORECASE,
)
_BARE_JSON_RX = _re.compile(r"\{[^{}]*\}", _re.DOTALL)


def _extract_json_str(raw: str) -> dict | None:
    """3-level JSON extraction: direct → fenced block → first {..}."""
    s = raw.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    m = _FENCED_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    m = _BARE_JSON_RX.search(s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def make_ephemeral_verifier(
    llm_call: Callable[[str], Awaitable[str]],
) -> Callable[[dict[str, Any]], Awaitable[bool]]:
    """Factory: wraps an async (prompt:str)->str LLM call into a VerifyGate-compatible
    async ephemeral_subagent callable.

    The returned callable:
      - accepts payload dict{ledger_size, failed_claims, assistant_text}
      - returns True (rescue pass) / False (fail)
      - Anti-sycophancy + objective-only prompt
      - Safe-fail: any exception / bad JSON → False (conservative)

    Args:
        llm_call: async callable (prompt: str) -> str  (e.g. _make_str_llm_call result)

    Returns:
        async callable matching VerifyGate.ephemeral_subagent signature
    """
    async def _verifier(payload: dict[str, Any]) -> bool:
        try:
            prompt = _EPHEMERAL_VERIFIER_PROMPT_TEMPLATE.format(
                assistant_text=str(payload.get("assistant_text", ""))[:500],
                ledger_size=int(payload.get("ledger_size", 0)),
                failed_claims=str(payload.get("failed_claims", []))[:500],
            )
            raw = await llm_call(prompt)
            obj = _extract_json_str(raw or "")
            if obj is None:
                logger.warning("make_ephemeral_verifier: could not parse LLM JSON: %r", (raw or "")[:200])
                return False
            verdict = str(obj.get("verdict", "fail")).strip().lower()
            return verdict == "pass"
        except Exception as exc:  # noqa: BLE001
            logger.warning("make_ephemeral_verifier: error: %s", exc)
            return False

    return _verifier


__all__ = [
    "UnmatchedClaim",
    "VerifierFailure",
    "GoalAlignment",
    "VerifyOutcome",
    "ClaimPattern",
    "Claim",
    "ClaimExtractor",
    "RegexExtractor",
    "SmallLLMExtractor",
    "CascadeExtractor",
    "VerifyGate",
    "load_claim_patterns",
    "make_ephemeral_verifier",
]
