# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P6 agent-loop-refactor — feature flag tests.

Phase 0 of the p6-agent-loop-refactor change introduced a process-wide
feature flag (``P6_ENABLE_GATE``) that lets us ship the new agent-loop
machinery dark and flip it on per-environment without a restart.

Phase 6 (this file as currently shaped) flipped the default to **on**.
The flag is essentially dead code now — it's kept around for one
release as an opt-OUT switch. Tests verify:

  * ``is_p6_gate_enabled() -> bool`` reads ``os.environ`` each call, so
    tests / runtime can flip the flag without re-importing the module.
  * Default (env unset) → True.
  * Explicitly falsy strings (``"0"``, ``"false"``, ``"no"``, ``""``)
    → False (case-insensitive, whitespace-stripped).
  * Truthy strings (``"1"``, ``"true"``, ``"yes"``) → True.
  * Anything else also returns True (the flag is "on unless explicitly
    disabled").
"""
from __future__ import annotations

import os

import pytest

from config import is_p6_gate_enabled


@pytest.fixture(autouse=True)
def _clean_env():
    """Ensure P6_ENABLE_GATE is unset before/after each test."""
    prev = os.environ.pop("P6_ENABLE_GATE", None)
    try:
        yield
    finally:
        os.environ.pop("P6_ENABLE_GATE", None)
        if prev is not None:
            os.environ["P6_ENABLE_GATE"] = prev


def test_flag_default_on():
    """P6 Phase 6 — Unset env var → True (was False before Phase 6)."""
    assert "P6_ENABLE_GATE" not in os.environ
    assert is_p6_gate_enabled() is True


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "True", "yes", "YES", "Yes"])
def test_flag_on_when_env_set(truthy: str):
    """Truthy strings (case-insensitive) → True (explicit opt-in)."""
    os.environ["P6_ENABLE_GATE"] = truthy
    assert is_p6_gate_enabled() is True


@pytest.mark.parametrize("falsy", ["0", "false", "FALSE", "no", "NO", ""])
def test_flag_off_when_env_falsy(falsy: str):
    """Explicit falsy strings → False (opt-out switch)."""
    os.environ["P6_ENABLE_GATE"] = falsy
    assert is_p6_gate_enabled() is False


def test_flag_off_only_when_explicitly_falsy():
    """P6 Phase 6 invariant: unset is on, "0" is off."""
    # Unset → on
    assert "P6_ENABLE_GATE" not in os.environ
    assert is_p6_gate_enabled() is True
    # Explicit "0" → off
    os.environ["P6_ENABLE_GATE"] = "0"
    assert is_p6_gate_enabled() is False
    # Unset again → on
    del os.environ["P6_ENABLE_GATE"]
    assert is_p6_gate_enabled() is True


def test_flag_reads_env_each_call():
    """Flipping the env between calls must reflect immediately (no caching).

    Phase 6: order is on → off → on → on (unset matches on now).
    """
    assert is_p6_gate_enabled() is True  # unset → on
    os.environ["P6_ENABLE_GATE"] = "0"
    assert is_p6_gate_enabled() is False
    os.environ["P6_ENABLE_GATE"] = "1"
    assert is_p6_gate_enabled() is True
    del os.environ["P6_ENABLE_GATE"]
    assert is_p6_gate_enabled() is True  # unset → on


def test_flag_strips_whitespace():
    """Common .env / shell artefacts (trailing space) should still parse."""
    os.environ["P6_ENABLE_GATE"] = "  true  "
    assert is_p6_gate_enabled() is True
    os.environ["P6_ENABLE_GATE"] = " 0 "
    assert is_p6_gate_enabled() is False
