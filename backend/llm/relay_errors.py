# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Relay (中转站) error classification — WI-R5.

When a chat LLM call goes through the the relay relay, two failure modes
need *structured* surfacing instead of a raw HTTP error:

  * **余额不足** — the account is out of credit. The UI should show a
    friendly "余额不足" message + a 去充值 link, not a 402 stack trace.
  * **key 失效** — the rotating ``tsk_xxx`` device key was rotated and
    the old one is now dead. The frontend drives a cross-layer retry:
    fetch a fresh key → re-push via relayProviderBridge → resend.

``TODO(M6)``: the exact HTTP status + response-body shape the relay
returns for these cases is **not yet finalised with the relay side**.
This module matches on a *deliberately loose* set of signals (status
code OR keyword in the body). Once the relay 402/401 contract is
confirmed, tighten :func:`classify_relay_error` — the call sites and the
error-code strings stay the same, so nothing downstream changes.
"""
from __future__ import annotations

# Structured error codes surfaced to the frontend chat layer.
INSUFFICIENT_BALANCE = "insufficient_balance"
RELAY_KEY_INVALID = "relay_key_invalid"

# TODO(M6): replace these heuristic keyword sets with the confirmed
# relay contract once the relay side documents its 402/401 body shape.
_BALANCE_HINTS = (
    "insufficient", "balance", "quota", "out of credit",
    "余额", "额度",
)
_KEY_HINTS = (
    "invalid_token", "invalid api key", "invalid key", "expired",
    "unauthorized", "token_invalid", "key_invalid", "device key",
)


def classify_relay_error(
    status_code: int | None,
    body_text: str = "",
    *,
    body: dict | None = None,
) -> str | None:
    """Classify a relay HTTP failure into a structured error code.

    Returns :data:`INSUFFICIENT_BALANCE`, :data:`RELAY_KEY_INVALID`, or
    ``None`` when the failure is neither (caller surfaces it generically).

    Structured relay codes are authoritative. A 401 always means the
    device key should be re-signed; 402/403 balance surfaces map to a
    recharge prompt.
    """
    top = body.get("code") if isinstance(body, dict) else None
    nested_error = body.get("error") if isinstance(body, dict) else None
    nested = nested_error.get("code") if isinstance(nested_error, dict) else None

    if (
        top == "INSUFFICIENT_BALANCE"
        or nested == "insufficient_balance"
        or (status_code == 403 and top == "FORBIDDEN")
    ):
        return INSUFFICIENT_BALANCE

    if top in {"INVALID_TOKEN", "EXPIRED_TOKEN"} or status_code == 401:
        return RELAY_KEY_INVALID

    ignored_codes = {
        "RATE_LIMITED",
        "UPSTREAM_ERROR",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "DEVICE_KEY_MISSING",
    }
    if top in ignored_codes or nested in ignored_codes:
        return None

    text = (body_text or "").lower()

    if status_code == 402 or any(h in text for h in _BALANCE_HINTS):
        return INSUFFICIENT_BALANCE

    if any(h in text for h in _KEY_HINTS):
        return RELAY_KEY_INVALID

    return None


__all__ = ["INSUFFICIENT_BALANCE", "RELAY_KEY_INVALID", "classify_relay_error"]
