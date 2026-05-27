"""P5-S2 Phase 2 — settings_providers_* + code_session_set_* IPC tests.

Covers `multi-provider-management` change, spec
`frontend-ipc-surface`. The handlers live inline in `backend/main.py`
ws control loop; tests drive them through `TestClient.websocket_connect`
with the shared secret, monkey-patching the module-level
``service_context`` to inject a per-test fresh ``LLMProviderRegistry``
and ``SessionDB`` so we don't poison the real backend state.

Wire format (recap):

  in:  settings_providers_list_request          → list_response { providers }
  in:  settings_providers_add { ...fields }     → added + providers_changed broadcast
  in:  settings_providers_update { id, patch }  → updated + providers_changed broadcast
  in:  settings_providers_remove { id }         → removed + providers_changed broadcast
  in:  settings_providers_reorder { ordered_ids } → reordered + providers_changed broadcast
  in:  code_session_set_provider { session_id, provider_id }     → code_session_provider_set
  in:  code_session_set_model    { session_id, model }           → code_session_model_set

Error path: any validation/registry error → ``settings_providers_error
{ reason, detail }``, registry unchanged, NO ``providers_changed``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from main import app, SHARED_SECRET, service_context, _control_connections
from llm.provider_registry import LLMProviderRegistry
from deskpet.memory.session_db import SessionDB


# ---------- helpers --------------------------------------------------------


def _drain_startup(ws) -> None:
    """Drain the `startup_status` frame the control ws sends first."""
    msg = ws.receive_json()
    assert msg["type"] == "startup_status", msg


def _drain_until(ws, target_type: str, max_frames: int = 8) -> dict[str, Any]:
    """Read frames until one of `target_type` shows up. Other interim
    frames (e.g. ``providers_changed`` arriving before ``settings_providers_added``)
    are returned in a list via a side-effect attribute on the ws object —
    here we just return the matched frame; broadcast tests use a separate
    helper that records every frame."""
    for _ in range(max_frames):
        msg = ws.receive_json()
        if msg.get("type") == target_type:
            return msg
    raise AssertionError(f"never received {target_type!r} after {max_frames} frames")


def _collect_frames(ws, count: int) -> list[dict[str, Any]]:
    """Read exactly `count` frames (used when we want to assert both
    response + broadcast in one shot)."""
    return [ws.receive_json() for _ in range(count)]


@pytest.fixture
def fresh_registry(tmp_path: Path, monkeypatch):
    """Per-test isolated registry under tmp_path/config.toml. Restores
    the original on teardown so other tests don't see leakage. Also
    monkey-patches keychain to a dict (no real OS keychain writes)."""
    fake_keychain: dict[tuple[str, str], str] = {}

    from llm import provider_registry as pr_mod

    class _FakeKeyring:
        @staticmethod
        def set_password(service: str, account: str, value: str) -> None:
            fake_keychain[(service, account)] = value

        @staticmethod
        def get_password(service: str, account: str) -> str | None:
            return fake_keychain.get((service, account))

        @staticmethod
        def delete_password(service: str, account: str) -> None:
            fake_keychain.pop((service, account), None)

    monkeypatch.setattr(pr_mod, "keyring", _FakeKeyring)
    monkeypatch.setattr(pr_mod, "_KEYRING_AVAILABLE", True)

    cfg = tmp_path / "config.toml"
    reg = LLMProviderRegistry(cfg)

    old_reg = service_context.provider_registry
    service_context.register("provider_registry", reg)
    try:
        yield reg, fake_keychain, cfg
    finally:
        service_context.provider_registry = old_reg


@pytest_asyncio.fixture
async def fresh_session_db(tmp_path: Path):
    """Per-test isolated SessionDB. Replaces service_context.session_db
    so the IPC handlers under test write/read against this one."""
    sdb = SessionDB(tmp_path / "state.db")
    await sdb.initialize()

    old_sdb = service_context.session_db
    service_context.register("session_db", sdb)
    try:
        yield sdb
    finally:
        service_context.session_db = old_sdb
        await sdb.close()


def _ws_open(client: TestClient, sid: str = "default"):
    """Open a control ws with shared secret + drain startup frame."""
    cm = client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}&session_id={sid}"
    )
    ws = cm.__enter__()
    _drain_startup(ws)
    return cm, ws


def _seed_provider_args(
    *,
    pid: str,
    name: str = "",
    base_url: str = "http://x/v1",
    model: str = "m",
    api_key: str = "sk-x",
    priority: int = 1,
    enabled: bool = True,
) -> dict:
    return {
        "id": pid,
        "name": name or pid,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "priority": priority,
        "enabled": enabled,
    }


def _seed_provider(reg: LLMProviderRegistry, **kw) -> None:
    """Sync helper for sync tests: run add_provider on a fresh loop.

    Async tests should not call this — they should use ``await
    reg.add_provider(_seed_provider_args(...))`` directly so the call
    runs on the active pytest-asyncio event loop. ``add_provider`` is
    async-named-only (no actual I/O await inside) — Phase 1 intentionally
    kept the signature async-shaped for Phase 2 broadcast hooks.
    """
    import asyncio

    asyncio.new_event_loop().run_until_complete(
        reg.add_provider(_seed_provider_args(**kw))
    )


# ---------- 2.1 settings_providers_list_request ----------------------------


def test_list_request_returns_sanitized(fresh_registry):
    """2.1: list_response carries api_key=******** for every entry."""
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="the relay", api_key="sk-real-1")
    _seed_provider(reg, pid="openrouter", api_key="sk-real-2", priority=2)

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json({"type": "settings_providers_list_request"})
        resp = _drain_until(ws, "settings_providers_list_response")
    finally:
        cm.__exit__(None, None, None)

    providers = resp["payload"]["providers"]
    assert len(providers) == 2
    ids = {p["id"] for p in providers}
    assert ids == {"the relay", "openrouter"}
    for p in providers:
        assert p["api_key"] == "********", f"api_key leaked: {p}"
        # Full metadata present.
        for field in ("id", "name", "base_url", "model", "priority", "enabled"):
            assert field in p, f"missing {field} in {p}"


# ---------- 2.2 add — uniqueness ------------------------------------------


def test_add_validates_uniqueness(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="the relay", api_key="sk-1")

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_add",
                "payload": {
                    "id": "the relay",  # duplicate!
                    "base_url": "http://y/v1",
                    "model": "m",
                    "api_key": "sk-2",
                },
            }
        )
        # Should ONLY get the error frame — no providers_changed broadcast.
        err = _drain_until(ws, "settings_providers_error")
    finally:
        cm.__exit__(None, None, None)

    assert err["payload"]["reason"] == "duplicate_id"
    # Registry unchanged.
    assert len(reg.list_providers()) == 1


# ---------- 2.3 add — missing field ---------------------------------------


def test_add_validates_required_fields(fresh_registry):
    reg, _kc, _cfg = fresh_registry

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_add",
                "payload": {
                    "id": "incomplete",
                    # NO base_url!
                    "model": "m",
                    "api_key": "sk-1",
                },
            }
        )
        err = _drain_until(ws, "settings_providers_error")
    finally:
        cm.__exit__(None, None, None)

    assert err["payload"]["reason"] == "missing_field"
    assert "base_url" in err["payload"]["detail"]
    assert len(reg.list_providers()) == 0


# ---------- 2.4 update — partial patch -------------------------------------


def test_update_partial_patch(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(
        reg,
        pid="the relay",
        name="Chinzy",
        base_url="http://x/v1",
        model="deepseek",
        api_key="sk-original",
        priority=1,
    )

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_update",
                "payload": {"id": "the relay", "patch": {"priority": 5}},
            }
        )
        # Expect: settings_providers_updated + providers_changed
        frames = _collect_frames(ws, 2)
    finally:
        cm.__exit__(None, None, None)

    types = sorted(f["type"] for f in frames)
    assert types == ["providers_changed", "settings_providers_updated"]

    entry = reg.get_entry("the relay")
    assert entry is not None
    assert entry.priority == 5
    # All other fields unchanged.
    assert entry.name == "Chinzy"
    assert entry.base_url == "http://x/v1"
    assert entry.model == "deepseek"
    assert entry.enabled is True


# ---------- 2.5 update — api_key writes keychain --------------------------


def test_update_api_key_writes_keychain(fresh_registry):
    reg, kc, _cfg = fresh_registry
    _seed_provider(reg, pid="the relay", api_key="sk-original")
    # confirm baseline
    assert kc[("deskpet", "provider.the relay")] == "sk-original"

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        # 1) Update WITHOUT api_key — keychain must not change.
        ws.send_json(
            {
                "type": "settings_providers_update",
                "payload": {"id": "the relay", "patch": {"name": "Renamed"}},
            }
        )
        _collect_frames(ws, 2)  # updated + providers_changed
        assert kc[("deskpet", "provider.the relay")] == "sk-original"

        # 2) Update WITH api_key — keychain updated.
        ws.send_json(
            {
                "type": "settings_providers_update",
                "payload": {"id": "the relay", "patch": {"api_key": "sk-new"}},
            }
        )
        _collect_frames(ws, 2)
    finally:
        cm.__exit__(None, None, None)

    assert kc[("deskpet", "provider.the relay")] == "sk-new"


# ---------- 2.6 remove — cleanup ------------------------------------------


@pytest.mark.asyncio
async def test_remove_cleanup(fresh_registry, fresh_session_db):
    """remove provider → keychain entry + code_session_provider rows clear."""
    reg, kc, _cfg = fresh_registry
    sdb = fresh_session_db

    await reg.add_provider(_seed_provider_args(pid="the relay", api_key="sk-1"))
    await reg.add_provider(_seed_provider_args(pid="openrouter", api_key="sk-2", priority=2))
    # Two sessions bound to "the relay", one to "openrouter".
    await sdb.set_code_session_provider_binding("sid-a", "the relay", None)
    await sdb.set_code_session_provider_binding("sid-b", "the relay", "alt-model")
    await sdb.set_code_session_provider_binding("sid-c", "openrouter", None)

    assert kc[("deskpet", "provider.the relay")] == "sk-1"

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_remove",
                "payload": {"id": "the relay"},
            }
        )
        # removed + providers_changed
        frames = _collect_frames(ws, 2)
    finally:
        cm.__exit__(None, None, None)

    types = sorted(f["type"] for f in frames)
    assert types == ["providers_changed", "settings_providers_removed"]

    # 1) Registry: only openrouter left.
    remaining = {p["id"] for p in reg.list_providers()}
    assert remaining == {"openrouter"}

    # 2) Keychain entry for the relay gone.
    assert ("deskpet", "provider.the relay") not in kc

    # 3) SessionDB: sid-a / sid-b cleared; sid-c untouched.
    assert (await sdb.get_code_session_provider_binding("sid-a"))["provider_id"] is None
    assert (await sdb.get_code_session_provider_binding("sid-b"))["provider_id"] is None
    binding_c = await sdb.get_code_session_provider_binding("sid-c")
    assert binding_c["provider_id"] == "openrouter"


# ---------- 2.7 reorder — incomplete set ----------------------------------


def test_reorder_validates_complete_set(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="a", api_key="k")
    _seed_provider(reg, pid="b", api_key="k", priority=2)
    _seed_provider(reg, pid="c", api_key="k", priority=3)

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "settings_providers_reorder",
                "payload": {"ordered_ids": ["b", "a"]},  # 'c' missing!
            }
        )
        err = _drain_until(ws, "settings_providers_error")
    finally:
        cm.__exit__(None, None, None)

    assert err["payload"]["reason"] == "incomplete_order"
    # Order unchanged.
    ids = [p["id"] for p in reg.list_providers()]
    assert ids == ["a", "b", "c"]


# ---------- 2.8 broadcast — both ws receive providers_changed --------------


def test_providers_changed_broadcasts_to_all_conns(fresh_registry):
    reg, _kc, _cfg = fresh_registry
    _seed_provider(reg, pid="seed", api_key="sk")

    client = TestClient(app)
    # Two simultaneous control conns: pet panel + code panel window.
    cm1, ws1 = _ws_open(client, sid="default")
    cm2, ws2 = _ws_open(client, sid="code-panel")
    try:
        # Trigger mutation from ws1.
        ws1.send_json(
            {
                "type": "settings_providers_update",
                "payload": {"id": "seed", "patch": {"priority": 9}},
            }
        )
        # ws1 should see {updated, providers_changed}; ws2 sees only
        # providers_changed.
        ws1_frames = _collect_frames(ws1, 2)
        ws2_frame = ws2.receive_json()
    finally:
        cm1.__exit__(None, None, None)
        cm2.__exit__(None, None, None)

    ws1_types = sorted(f["type"] for f in ws1_frames)
    assert ws1_types == ["providers_changed", "settings_providers_updated"]
    assert ws2_frame["type"] == "providers_changed"
    # Payload carries the new provider list (sanitized).
    assert ws2_frame["payload"]["providers"][0]["api_key"] == "********"


# ---------- 2.10 / 2.11 code_session_set_provider --------------------------


@pytest.mark.asyncio
async def test_set_provider_binding_persists(fresh_registry, fresh_session_db):
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="the relay", api_key="sk"))

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "code_session_set_provider",
                "payload": {"session_id": "vpn-tunnel", "provider_id": "the relay"},
            }
        )
        resp = _drain_until(ws, "code_session_provider_set")
    finally:
        cm.__exit__(None, None, None)

    assert resp["payload"] == {
        "session_id": "vpn-tunnel",
        "provider_id": "the relay",
        "preferred_model": None,
        "model_params": None,
    }
    # DB row written.
    binding = await sdb.get_code_session_provider_binding("vpn-tunnel")
    assert binding == {
        "provider_id": "the relay",
        "preferred_model": None,
        "model_params": None,
    }


@pytest.mark.asyncio
async def test_set_provider_null_clears_binding(fresh_registry, fresh_session_db):
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="the relay", api_key="sk"))
    await sdb.set_code_session_provider_binding("vpn-tunnel", "the relay", None)

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "code_session_set_provider",
                "payload": {"session_id": "vpn-tunnel", "provider_id": None},
            }
        )
        resp = _drain_until(ws, "code_session_provider_set")
    finally:
        cm.__exit__(None, None, None)

    assert resp["payload"] == {
        "session_id": "vpn-tunnel",
        "provider_id": None,
        "preferred_model": None,
        "model_params": None,
    }
    binding = await sdb.get_code_session_provider_binding("vpn-tunnel")
    assert binding == {
        "provider_id": None,
        "preferred_model": None,
        "model_params": None,
    }


# ---------- 2.12 code_session_set_model alone keeps chain global ------------


@pytest.mark.asyncio
async def test_set_model_alone_keeps_chain_global(fresh_registry, fresh_session_db):
    """User sets preferred_model on an unbound session: provider_id stays
    None (still global chain), but preferred_model is recorded."""
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="the relay", api_key="sk"))

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "code_session_set_model",
                "payload": {"session_id": "vpn-tunnel", "model": "gpt-4o-mini"},
            }
        )
        resp = _drain_until(ws, "code_session_model_set")
    finally:
        cm.__exit__(None, None, None)

    assert resp["payload"] == {
        "session_id": "vpn-tunnel",
        "provider_id": None,
        "preferred_model": "gpt-4o-mini",
        "model_params": None,  # legacy {session_id,model} → provider defaults
    }
    binding = await sdb.get_code_session_provider_binding("vpn-tunnel")
    assert binding == {
        "provider_id": None,
        "preferred_model": "gpt-4o-mini",
        "model_params": None,
    }


# ---------- 2.13 code-session-model-params: params round-trip via IPC -------


@pytest.mark.asyncio
async def test_set_model_with_params_round_trip(fresh_registry, fresh_session_db):
    """Cursor picker sends {session_id, model, params}; backend persists
    and echoes model_params (code-session-model-params T4.1)."""
    reg, _kc, _cfg = fresh_registry
    sdb = fresh_session_db
    await reg.add_provider(_seed_provider_args(pid="the relay", api_key="sk"))
    params = {
        "thinking": True,
        "fast": False,
        "context": "1m",
        "effort": "high",
    }

    client = TestClient(app)
    cm, ws = _ws_open(client)
    try:
        ws.send_json(
            {
                "type": "code_session_set_model",
                "payload": {
                    "session_id": "code:proj-x",
                    "model": "gpt-5.5",
                    "params": params,
                },
            }
        )
        resp = _drain_until(ws, "code_session_model_set")
    finally:
        cm.__exit__(None, None, None)

    assert resp["payload"] == {
        "session_id": "code:proj-x",
        "provider_id": None,
        "preferred_model": "gpt-5.5",
        "model_params": params,
    }
    binding = await sdb.get_code_session_provider_binding("code:proj-x")
    assert binding == {
        "provider_id": None,
        "preferred_model": "gpt-5.5",
        "model_params": params,
    }
