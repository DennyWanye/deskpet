# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.1 stub — ToolReceipt + HMAC (PRD §3 D5 + D11)。

**Stub-only**：本文件先提供完整接口签名 + dataclass schema，让 TG-0
smoke 通过（防接口腐烂）；HMAC 计算 + DPAPI/Keychain 包装的真正实现
留在 WI-T2.1 / WI-T2.2 的后续 commit 中。

字段与 PRD §3 D5 / TDD §C.2 同源（12 项 required + sig HMAC）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Sentinel：测试 fixture 用确定性 key；真实生产从 DPAPI/Keychain 读
_TEST_HMAC_KEY: bytes = b"\x00" * 32


@dataclass
class ToolReceipt:
    """12 コア字段 + 4 可选 shadow 字段 — 与 PRD §3 D5 + TDD §C.2 JSON Schema 同源。

    error_class enum 见 PRD D5 末段（与 UnmatchedClaim.reason 是两套）。

    R-T6 §15.5 Shadow 可观测字段（全部 Optional，default 保持 BC）：
      shadow_verdict     — strict 模式「会拦/会放」的预测（"would_block" | "would_pass" | None）
      actual_outcome     — 用户后续行为弱信号占位（"user_accepted" | "user_complained" | None）
      verify_latency_ms  — 本轮 verify 耗时（用于 monitor 聚合 p95）
      degradation_flags  — 本轮哪些 LLM 依赖点降级（list，供降级率统计）

    **BC 保证**：所有新字段均有 default；旧格式 ToolReceipt(**d) 不含这些 key
    也能正常反序列化（需调方用 ``ToolReceipt(**{k: v for k, v in d.items() if k in ...}}``
    或直接 ``ToolReceipt(**d)`` — dataclass default 兜底）。
    """
    receipt_id: str
    tool_name: str
    args_hash: str             # sha256(canonical_json(args))
    started_at: str            # ISO 8601 UTC
    ended_at: str
    duration_ms: int
    ok: bool
    error_class: Optional[str] = None
    artifacts: list[str] = field(default_factory=list)  # sha256 列表
    session_id: str = ""
    iteration: int = 0
    sig: str = ""              # HMAC-SHA256 base64
    # ── R-T6 Shadow 可观测字段（全部可选，BC-safe） ──────────────────────────
    shadow_verdict: Optional[str] = None          # "would_block" | "would_pass" | None
    actual_outcome: Optional[str] = None          # 用户行为弱信号占位
    verify_latency_ms: Optional[int] = None       # verify 耗时（ms），用于 p95 聚合
    degradation_flags: list[str] = field(default_factory=list)  # 降级事实列表

    def to_dict(self) -> dict[str, Any]:
        """字段顺序稳定 (按 dataclass 定义)。"""
        return asdict(self)


# ─── HMAC helpers ────────────────────────────────────────────

def canonical_json(obj: Any) -> str:
    """跨语言可重现的 canonical JSON（用于 HMAC + args_hash）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def args_hash(args: dict[str, Any]) -> str:
    """sha256(canonical_json(args)) — receipt.args_hash 字段。"""
    return hashlib.sha256(canonical_json(args).encode("utf-8")).hexdigest()


# R-T6 BC: shadow 可观测字段不计入 HMAC 载荷，使旧收据（无这些 key）仍能通过验签。
# 这些字段是事后补注的元数据（shadow 预测 / 用户弱信号 / 运维观测），
# 不属于工具调用的原始不可篡改事实。
_HMAC_EXCLUDED_FIELDS: frozenset[str] = frozenset({
    "sig",
    "shadow_verdict",
    "actual_outcome",
    "verify_latency_ms",
    "degradation_flags",
})


def hmac_sign(receipt: ToolReceipt, secret: bytes = _TEST_HMAC_KEY) -> str:
    """HMAC-SHA256(secret, canonical_json(core receipt fields))。

    Shadow 字段（shadow_verdict / actual_outcome / verify_latency_ms /
    degradation_flags）被排除在 HMAC 载荷之外，保证旧格式收据（无这些 key）
    与新格式收据（含这些 key）的 HMAC 计算结果一致（R-T6 BC 保证）。
    """
    d = receipt.to_dict()
    for f in _HMAC_EXCLUDED_FIELDS:
        d.pop(f, None)
    payload = canonical_json(d).encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def hmac_verify(receipt: ToolReceipt, secret: bytes = _TEST_HMAC_KEY) -> bool:
    """验证 receipt.sig 字段。"""
    expected = hmac_sign(receipt, secret)
    return hmac.compare_digest(expected, receipt.sig)


def make_receipt(
    *,
    tool_name: str,
    args: dict[str, Any],
    started_at: datetime,
    ended_at: datetime,
    ok: bool,
    session_id: str = "",
    iteration: int = 0,
    error_class: Optional[str] = None,
    artifact_shas: Optional[list[str]] = None,
    secret: bytes = _TEST_HMAC_KEY,
) -> ToolReceipt:
    """Convenience factory — 构造 receipt + 自动签名。"""
    r = ToolReceipt(
        receipt_id=str(uuid.uuid4()),
        tool_name=tool_name,
        args_hash=args_hash(args),
        started_at=started_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ended_at=ended_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_ms=int((ended_at - started_at).total_seconds() * 1000),
        ok=ok,
        error_class=error_class,
        artifacts=list(artifact_shas or []),
        session_id=session_id,
        iteration=iteration,
    )
    r.sig = hmac_sign(r, secret)
    return r


__all__ = [
    "ToolReceipt",
    "canonical_json",
    "args_hash",
    "hmac_sign",
    "hmac_verify",
    "make_receipt",
]
