"""code-session-model-params — resolution wiring.

Spec: "Bound model+params apply to the code session's next agent call",
"Code-mode default model is configurable and distinct".
"""
from __future__ import annotations

import asyncio
import types

from llm.resolution import resolve_provider_for_session


class _Reg:
    """Minimal registry stub: one global chain entry."""

    def get_chain(self):
        return [{"id": "p0", "base_url": "https://chinzy.com/v1",
                 "model": "deepseek-v4-pro", "temperature": 0.7}]

    def get_entry(self, pid):
        return None


class _SDB:
    def __init__(self, binding):
        self._b = binding

    async def get_code_session_provider_binding(self, sid):
        return self._b


def _resolve(**kw):
    return asyncio.get_event_loop().run_until_complete(
        resolve_provider_for_session(**kw)
    )


def test_companion_session_untouched_no_binding_no_params() -> None:
    sdb = _SDB({"provider_id": None, "preferred_model": None,
                "model_params": None})
    entries = _resolve(
        base_sid="default", is_code_session=False, registry=_Reg(),
        session_db=sdb, code_default_model="gpt-5.5",
    )
    assert entries[0].model == "deepseek-v4-pro"  # pet model unchanged
    assert not getattr(entries[0], "code_params", {})  # no params attached


def test_unbound_code_session_uses_code_default_model() -> None:
    sdb = _SDB({"provider_id": None, "preferred_model": None,
                "model_params": None})
    entries = _resolve(
        base_sid="code:proj", is_code_session=True, registry=_Reg(),
        session_db=sdb, code_default_model="gpt-5.5",
    )
    assert entries[0].model == "gpt-5.5"
    assert entries[0].code_params == {}  # no params → provider defaults


def test_bound_model_and_params_applied() -> None:
    sdb = _SDB({
        "provider_id": None,
        "preferred_model": "opus-4.7",
        "model_params": {"thinking": True, "effort": "max",
                         "context": "1m", "fast": True},
    })
    entries = _resolve(
        base_sid="code:proj", is_code_session=True, registry=_Reg(),
        session_db=sdb, code_default_model="gpt-5.5",
    )
    assert entries[0].model == "opus-4.7"  # explicit binding wins over default
    cp = entries[0].code_params
    assert cp["reasoning_effort"] == "high"  # max clamps to high
    assert cp["extra_body"] == {"context_window": 1_000_000, "fast": True}


def test_flag_off_no_default_no_params_legacy() -> None:
    sdb = _SDB({"provider_id": None, "preferred_model": None,
                "model_params": None})
    entries = _resolve(
        base_sid="code:proj", is_code_session=True, registry=_Reg(),
        session_db=sdb, code_default_model=None,  # knob empty → legacy
    )
    assert entries[0].model == "deepseek-v4-pro"  # unchanged
    assert entries[0].code_params == {}
