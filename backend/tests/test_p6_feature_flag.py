"""P6 agent-loop-refactor — feature flag tests.

Phase 0 of the p6-agent-loop-refactor change introduces a process-wide
feature flag (``P6_ENABLE_GATE``) that lets us ship the new agent-loop
machinery dark and flip it on per-environment without a restart.

Contract:
  * ``is_p6_gate_enabled() -> bool`` reads ``os.environ`` each call, so
    tests / runtime can flip the flag without re-importing the module.
  * Default (env unset) → False.
  * Truthy strings (``"1"``, ``"true"``, ``"yes"``, case-insensitive) → True.
  * Falsy / empty strings (``"0"``, ``"false"``, ``""``, ``"no"``) → False.
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


def test_flag_default_off():
    """Unset env var → False."""
    assert "P6_ENABLE_GATE" not in os.environ
    assert is_p6_gate_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "True", "yes", "YES", "Yes"])
def test_flag_on_when_env_set(truthy: str):
    """Truthy strings (case-insensitive) → True."""
    os.environ["P6_ENABLE_GATE"] = truthy
    assert is_p6_gate_enabled() is True


@pytest.mark.parametrize("falsy", ["0", "false", "FALSE", "no", "NO", ""])
def test_flag_off_when_env_falsy(falsy: str):
    """Falsy strings → False."""
    os.environ["P6_ENABLE_GATE"] = falsy
    assert is_p6_gate_enabled() is False


def test_flag_reads_env_each_call():
    """Flipping the env between calls must reflect immediately (no caching)."""
    assert is_p6_gate_enabled() is False
    os.environ["P6_ENABLE_GATE"] = "1"
    assert is_p6_gate_enabled() is True
    os.environ["P6_ENABLE_GATE"] = "0"
    assert is_p6_gate_enabled() is False
    del os.environ["P6_ENABLE_GATE"]
    assert is_p6_gate_enabled() is False


def test_flag_strips_whitespace():
    """Common .env / shell artefacts (trailing space) should still parse."""
    os.environ["P6_ENABLE_GATE"] = "  true  "
    assert is_p6_gate_enabled() is True
    os.environ["P6_ENABLE_GATE"] = " 0 "
    assert is_p6_gate_enabled() is False
