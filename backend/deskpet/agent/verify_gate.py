"""WI-T2.3/T2.4 stub — VerifyGate + ClaimExtractor (PRD §3 D6)。

**Stub-only**：提供接口签名 + dataclass schema 让 TG-0 smoke 通过；
真正的 regex/LLM cascade extractor + ephemeral subagent 救援链 + 4 个
outcome verifier 在 WI-T2.3 / T2.4 / T2.4b / T2.5 后续 commit 中实现。

字段与 PRD §3 D6 / TDD §C.4 同源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, Protocol


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
    verifier: str  # "file_exists" | "git_diff" | "build" | "test"
    status: str    # "failed" | "skipped" | "timeout"
    reason: str
    log_tail: Optional[str] = None
    error_class: Optional[str] = None


@dataclass
class VerifyOutcome:
    """VerifyGate.check 返回 — 详见 PRD D6 末段 + TDD §C.4。"""
    passed: bool
    claims_extracted: int = 0
    unmatched_claims: list[UnmatchedClaim] = field(default_factory=list)
    verifier_failures: list[VerifierFailure] = field(default_factory=list)
    elapsed_ms: int = 0
    extractor_used: str = "regex"   # "regex" | "regex+llm_fallback" | "ephemeral_subagent"
    failure_count: int = 0          # PRD D6 计数语义：== 3 时强退


@dataclass
class ClaimPattern:
    """从 verify/claim_patterns.yaml 加载。"""
    id: str
    regex: str
    artifact_kind: str
    tool_hint: list[str] = field(default_factory=list)


@dataclass
class Claim:
    """ClaimExtractor 输出。"""
    pattern_id: str
    raw_text: str
    title: Optional[str] = None
    path: Optional[str] = None
    kind: str = "file"


# ─── ClaimExtractor strategy (PRD §3 D6) ─────────────────────

class ClaimExtractor(Protocol):
    """Pluggable interface — regex / LLM / NLI 三实现共用。"""
    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]: ...


class RegexExtractor:
    """re2-compiled patterns (stub — 真正实现 WI-T2.4)。"""
    def __init__(self, patterns: list[ClaimPattern]) -> None:
        self.patterns = patterns

    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]:
        return []  # stub


class SmallLLMExtractor:
    """二级 LLM fallback (stub — 真正实现 WI-T2.4b)。"""
    def __init__(self, llm_call: Callable[[str], str]) -> None:
        self.llm_call = llm_call

    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]:
        return []  # stub


class CascadeExtractor:
    """regex 白盒 + LLM 兜底 (PRD D6 默认)。"""
    def __init__(
        self,
        primary: ClaimExtractor,
        fallback: Optional[ClaimExtractor] = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def extract(self, assistant_text: str, hints: dict[str, Any]) -> list[Claim]:
        return self.primary.extract(assistant_text, hints)


# ─── VerifyGate (PRD D6) ─────────────────────────────────────

class VerifyGate:
    """Check assistant claims against ReceiptLedger.

    Stub-only — full check() + ephemeral_verifier_subagent 救援链 在
    WI-T2.4 + T2.4b 实现。本 stub 仅 mode 切换 + 空 outcome 返回。
    """

    def __init__(
        self,
        *,
        extractor: ClaimExtractor,
        mode: str = "off",
    ) -> None:
        if mode not in ("off", "shadow", "strict"):
            raise ValueError(f"invalid mode: {mode}")
        self.extractor = extractor
        self.mode = mode

    def check(
        self,
        *,
        assistant_text: str,
        ledger: Any,  # ReceiptLedger
    ) -> VerifyOutcome:
        # stub: off mode 总是 pass；shadow/strict 实际 check 在 T2.4
        if self.mode == "off":
            return VerifyOutcome(passed=True)
        return VerifyOutcome(passed=True, extractor_used="regex")


__all__ = [
    "UnmatchedClaim",
    "VerifierFailure",
    "VerifyOutcome",
    "ClaimPattern",
    "Claim",
    "ClaimExtractor",
    "RegexExtractor",
    "SmallLLMExtractor",
    "CascadeExtractor",
    "VerifyGate",
]
