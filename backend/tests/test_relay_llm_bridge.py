"""TDD T3 — WI-R2 relay LLM bridge: the `persist_key` bypass on
POST /config/cloud.

Only the NEW `persist_key` behaviour is tested here — the hot-swap of
`local_llm` itself is already covered by test_config_cloud_endpoint.py.

Privacy contract: when `persist_key=False` (relay edition), the rotating
`tsk_xxx` device key is applied to the live provider but MUST NOT be
written to llm_runtime.json.
"""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient, ASGITransport

import main as main_module
from main import app, LLM_RUNTIME_PATH


@pytest.fixture(autouse=True)
def _snapshot_runtime_config():
    """Snapshot llm_runtime.json + local_llm; restore after each test."""
    saved = LLM_RUNTIME_PATH.read_text(encoding="utf-8") if LLM_RUNTIME_PATH.exists() else None
    saved_llm = main_module.local_llm
    try:
        yield
    finally:
        if saved is not None:
            LLM_RUNTIME_PATH.write_text(saved, encoding="utf-8")
        elif LLM_RUNTIME_PATH.exists():
            LLM_RUNTIME_PATH.unlink()
        main_module.local_llm = saved_llm


@pytest.fixture(autouse=True)
def _dev_mode(monkeypatch):
    """Bypass the shared-secret gate for these unit tests."""
    monkeypatch.setattr(main_module, "DEV_MODE", True)


async def _post(client, body: dict):
    return await client.post("/config/cloud", json=body)


def _read_runtime() -> dict:
    if not LLM_RUNTIME_PATH.exists():
        return {}
    return json.loads(LLM_RUNTIME_PATH.read_text(encoding="utf-8"))


_RELAY_KEY = "tsk_relaykey_SECRET_zzz"


@pytest.mark.asyncio
async def test_t3_1_persist_key_false_applies_key_live():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post(client, {
            "base_url": "https://your-llm-relay.example.com/v1",
            "model": "gpt-5.5",
            "api_key": _RELAY_KEY,
            "persist_key": False,
        })
    assert resp.status_code == 200
    # The in-memory provider must carry the relay key (live, usable).
    assert getattr(main_module.local_llm, "api_key", None) == _RELAY_KEY


@pytest.mark.asyncio
async def test_t3_2_persist_key_false_not_written_to_disk():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _post(client, {
            "base_url": "https://your-llm-relay.example.com/v1",
            "model": "gpt-5.5",
            "api_key": _RELAY_KEY,
            "persist_key": False,
        })
    runtime = _read_runtime()
    assert runtime.get("base_url") == "https://your-llm-relay.example.com/v1"
    assert runtime.get("model") == "gpt-5.5"
    assert "api_key" not in runtime
    # Hard guarantee: the relay key prefix appears nowhere in the file.
    raw = LLM_RUNTIME_PATH.read_text(encoding="utf-8") if LLM_RUNTIME_PATH.exists() else ""
    assert "tsk_" not in raw


@pytest.mark.asyncio
async def test_t3_3_persist_key_true_writes_key_regression():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _post(client, {
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4",
            "api_key": "sk-manual-key-123",
            "persist_key": True,
        })
    assert _read_runtime().get("api_key") == "sk-manual-key-123"


@pytest.mark.asyncio
async def test_t3_4_persist_key_defaults_true():
    """Field omitted → behaves as persist_key=True (manual edition / all
    existing callers unaffected)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _post(client, {
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4",
            "api_key": "sk-omitted-default",
        })
    assert _read_runtime().get("api_key") == "sk-omitted-default"


@pytest.mark.asyncio
async def test_t3_5_persist_key_false_idempotent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = {
            "base_url": "https://your-llm-relay.example.com/v1",
            "model": "gpt-5.5",
            "api_key": _RELAY_KEY,
            "persist_key": False,
        }
        r1 = await _post(client, body)
        r2 = await _post(client, body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert "api_key" not in _read_runtime()


@pytest.mark.asyncio
async def test_t3_6_request_model_accepts_persist_key():
    from main import CloudConfigRequest

    req = CloudConfigRequest(base_url="https://x/v1", model="m", api_key="k", persist_key=False)
    assert req.persist_key is False
    # Omitted → default True.
    req2 = CloudConfigRequest(base_url="https://x/v1", model="m")
    assert req2.persist_key is True
