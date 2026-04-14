"""Sensitive-information filter for memory writes.

V5 §6 threat model: "记忆写入前经过敏感信息过滤器（正则+分类器）".

MVP strategy — regex-based redaction. When a message looks like it contains
secrets (API keys, passwords, credit cards, emails, phone numbers), replace
the secret span with a ``[REDACTED:KIND]`` marker before the message reaches
the persistent store. A future slice can layer a classifier on top for
semantic detection; the contract (string → string) stays the same.

Design notes:
- The filter is applied only on writes, never on reads — so historical
  unredacted data (imported from elsewhere) is still returned verbatim.
- Patterns err on the side of caution: it is better to over-redact a public
  string than to leak a real secret.
- Order matters: we redact long, specific patterns first (JWT, API keys)
  before shorter generic ones (hex tokens) so we don't bite off a prefix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Pattern:
    kind: str
    regex: re.Pattern[str]


# Order matters — more specific first.
_PATTERNS: tuple[_Pattern, ...] = (
    # Anthropic keys (specific prefix `sk-ant-`) MUST come before the generic
    # `sk-` OpenAI-style key pattern, otherwise the latter consumes the span
    # first and masks the `ant-` infix.
    _Pattern("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    # OpenAI / Stripe-style API keys — distinctive prefix + length.
    _Pattern("API_KEY", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}\b")),
    # GitHub personal access tokens.
    _Pattern("GITHUB_TOKEN", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    # JWT — three base64 segments separated by dots.
    _Pattern("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    # AWS access key IDs.
    _Pattern("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    # Credit-card-like digit runs (13-19 digits, spaces/dashes allowed).
    _Pattern("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Chinese mainland phone numbers.
    _Pattern("PHONE_CN", re.compile(r"\b1[3-9]\d{9}\b")),
    # Email addresses.
    _Pattern("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # "password: <value>" / "passwd=<value>" / "secret: <value>"
    _Pattern(
        "CREDENTIAL",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S+",
        ),
    ),
)


def redact(text: str) -> str:
    """Return `text` with sensitive spans replaced by ``[REDACTED:KIND]``.

    Always returns a string — never raises. Empty / non-matching input is
    returned unchanged so this is cheap to wrap around every write.
    """
    if not text:
        return text
    redacted = text
    for pat in _PATTERNS:
        redacted = pat.regex.sub(f"[REDACTED:{pat.kind}]", redacted)
    return redacted


class RedactingMemoryStore:
    """MemoryStore decorator that redacts content before calling ``append``.

    Duck-typed against ``memory.base.MemoryStore`` — wraps any concrete
    implementation. Reads pass through unchanged.
    """

    def __init__(self, inner) -> None:  # noqa: ANN001 — duck-typed Protocol
        self._inner = inner

    async def get_recent(self, session_id: str, limit: int = 10):
        return await self._inner.get_recent(session_id, limit)

    async def append(self, session_id: str, role: str, content: str) -> None:
        await self._inner.append(session_id, role, redact(content))

    async def clear(self, session_id: str) -> None:
        await self._inner.clear(session_id)

    # ---- S14 management passthrough (if the inner store provides them) ----
    # Inner content was already redacted on write, so reads don't need any
    # further processing. These pass through unconditionally so the UI works
    # regardless of which decorator layer the service holds.

    async def list_turns(self, session_id=None, limit=None):
        return await self._inner.list_turns(session_id, limit)

    async def delete_turn(self, turn_id: int) -> bool:
        return await self._inner.delete_turn(turn_id)

    async def list_sessions(self):
        return await self._inner.list_sessions()

    async def clear_all(self) -> int:
        return await self._inner.clear_all()
