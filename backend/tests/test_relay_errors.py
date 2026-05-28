# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TDD T6-1 / T6-2 — relay error classification (WI-R5).

TODO(M6): assertions track the heuristic contract in
`llm/relay_errors.py`. Re-tighten once the relay 402/401 body shape is
confirmed with the relay side.
"""
from __future__ import annotations

from llm.relay_errors import (
    INSUFFICIENT_BALANCE,
    RELAY_KEY_INVALID,
    classify_relay_error,
)


def test_t6_1_http_402_is_insufficient_balance():
    assert classify_relay_error(402, "") == INSUFFICIENT_BALANCE


def test_t6_1_balance_keyword_in_body():
    assert classify_relay_error(400, '{"error":"insufficient balance"}') == INSUFFICIENT_BALANCE
    assert classify_relay_error(None, "账户余额不足") == INSUFFICIENT_BALANCE


def test_t6_2_http_401_is_key_invalid():
    assert classify_relay_error(401, "") == RELAY_KEY_INVALID


def test_t6_2_key_keyword_in_body():
    assert classify_relay_error(400, '{"code":"invalid_token"}') == RELAY_KEY_INVALID
    assert classify_relay_error(403, "device key expired") == RELAY_KEY_INVALID


def test_balance_wins_over_key_when_both_signals():
    # A 401 whose body says "insufficient balance" is a balance problem.
    assert classify_relay_error(401, "insufficient balance") == INSUFFICIENT_BALANCE


def test_unrelated_error_returns_none():
    assert classify_relay_error(500, "internal server error") is None
    assert classify_relay_error(None, "") is None
