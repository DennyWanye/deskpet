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
        return [{"id": "p0", "base_url": "https://your-llm-relay.example.com/v1",
                 "model": "deepseek-v4-pro", "temperature": 0.7}]

    def get_entry(self, pid):
        return None


class _SDB:
    def __init__(self, binding):
        self._b = binding

    async def get_code_session_provider_binding(self, sid):
        return self._b


def _resolve(**kw):
    return asyncio.run(resolve_provider_for_session(**kw))


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


def test_bound_anthropic_model_strips_reasoning_effort() -> None:
    # Model-aware: an Anthropic-family model (opus-*) does NOT expose
    # reasoning_effort, so even though the mapper derives one from
    # thinking/effort, _attach_code_params strips it for this entry.
    # context_window / fast are NOT effort and still apply.
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
    assert "reasoning_effort" not in cp  # stripped — Anthropic has no effort
    assert cp["extra_body"] == {"context_window": 1_000_000, "fast": True}


def test_bound_openai_model_keeps_reasoning_effort() -> None:
    # gpt-* IS a reasoning_effort family → the key is preserved (max→high).
    sdb = _SDB({
        "provider_id": None,
        "preferred_model": "gpt-5.5",
        "model_params": {"thinking": True, "effort": "max",
                         "context": "1m", "fast": True},
    })
    entries = _resolve(
        base_sid="code:proj", is_code_session=True, registry=_Reg(),
        session_db=sdb, code_default_model="gpt-5.5",
    )
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
