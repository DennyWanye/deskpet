# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Relay-managed provider WS operations (Phase B / WI-2).

Extracted from ``main.py``'s ``control_channel`` handler so the decision
logic (source gating, key-fingerprint redaction, KeyMissingError → error
payload, relay logout) is unit-testable WITHOUT importing the heavy
``main.py`` module. ``main.py`` imports these and only does the
``ws.send_json`` / broadcast plumbing around the returned payload.

Security: api_key plaintext is NEVER logged — only a short sha256
fingerprint (``key_fingerprint``).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from llm.provider_registry import KeyMissingError, LLMProviderRegistry

logger = logging.getLogger("deskpet.llm.relay_provider_ops")

RELAY_PROVIDER_ID = "relay-cloud"


def key_fingerprint(key: str | None) -> str | None:
    """Short, non-reversible fingerprint of an api_key for logs.

    Returns None for empty/None so we never log plaintext. 8 hex chars
    of sha256 — enough to correlate "which key" across logs without
    leaking the secret.
    """
    return None if not key else hashlib.sha256(key.encode()).hexdigest()[:8]


async def ensure_relay_provider(
    reg: LLMProviderRegistry, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Upsert a relay-managed provider into the registry.

    Returns an *error payload* dict (to send back over the ws) when the
    request is rejected or the local key is missing; returns ``None`` on
    success (caller then broadcasts ``providers_changed``).

    - Only ``source == "relay"`` is accepted on this channel (manual
      providers must use ``settings_providers_add``).
    - On success logs ``key_fp`` (fingerprint), never the plaintext key.
    - ``KeyMissingError`` → ``{reason: "key_missing", ...}`` so the
      frontend re-mints via ``?rotate=force``.
    """
    if payload.get("source") not in ("relay",):
        return {"reason": "ensure_only_managed", "detail": "source=relay only"}
    try:
        entry = await reg.ensure_provider(payload)
    except KeyMissingError as exc:
        return {
            "reason": "key_missing",
            "detail": str(exc),
            "provider_id": payload.get("id"),
        }
    logger.info(
        "relay_provider_ensured id=%s account_ref=%s base_url=%s key_fp=%s",
        entry.id,
        entry.account_ref,
        entry.base_url,
        key_fingerprint(reg.resolve_api_key(entry.id)),
    )
    return None


async def relay_logout(reg: LLMProviderRegistry) -> None:
    """Tear down the relay-managed provider on logout.

    Disables the row, clears its account fingerprint, and deletes the
    local keychain key so account A's long-lived key is never reused by
    a subsequently logged-in account B. Idempotent: no-op if the row was
    never created.
    """
    try:
        await reg.update_provider(RELAY_PROVIDER_ID, enabled=False, account_ref="")
    except KeyError:
        return
    reg._keychain_delete(RELAY_PROVIDER_ID)


__all__ = ["key_fingerprint", "ensure_relay_provider", "relay_logout", "RELAY_PROVIDER_ID"]
