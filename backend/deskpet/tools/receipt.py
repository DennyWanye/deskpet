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
    """12 字段 dataclass — 与 PRD §3 D5 + TDD §C.2 JSON Schema 同源。

    error_class enum 见 PRD D5 末段（与 UnmatchedClaim.reason 是两套）。
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


def hmac_sign(receipt: ToolReceipt, secret: bytes = _TEST_HMAC_KEY) -> str:
    """HMAC-SHA256(secret, canonical_json(receipt without 'sig'))。"""
    d = receipt.to_dict()
    d.pop("sig", None)
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
