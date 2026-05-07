"""Tests for POST /config/cloud endpoint."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import main as main_module
from main import app, SHARED_SECRET, LLM_RUNTIME_PATH


# Autouse: snapshot llm_runtime.json before each test, restore after.
# Without this, /config/cloud writes pollute %APPDATA%/deskpet/llm_runtime.json
# and the next backend boot uses the test's bogus base_url/model. Bit me
# during P4-S20-D E2E (api.example.com/gpt-4 leaked to runtime).
@pytest.fixture(autouse=True)
def _snapshot_runtime_config():
    saved = LLM_RUNTIME_PATH.read_text(encoding="utf-8") if LLM_RUNTIME_PATH.exists() else None
    try:
        yield
    finally:
        if saved is not None:
            LLM_RUNTIME_PATH.write_text(saved, encoding="utf-8")
        elif LLM_RUNTIME_PATH.exists():
            LLM_RUNTIME_PATH.unlink()


@pytest.mark.asyncio
async def test_update_cloud_config_rejects_bad_secret():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/config/cloud",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4",
                "api_key": "sk-test1234567890",
            },
            headers={"x-shared-secret": "wrong-secret"},
        )
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


@pytest.mark.asyncio
async def test_update_cloud_config_dev_mode_bypasses_auth(monkeypatch):
    monkeypatch.setattr(main_module, "DEV_MODE", True)
    # Provide an api_key so we don't hit the "no api_key configured" 400.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/config/cloud",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4",
                "api_key": "sk-test1234567890",
            },
            # No x-shared-secret header
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_cloud_config_validates_base_url():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/config/cloud",
            json={
                "base_url": "file:///etc/passwd",
                "model": "gpt-4",
                "api_key": "sk-test1234567890",
            },
            headers={"x-shared-secret": SHARED_SECRET},
        )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_update_cloud_config_validates_model_empty():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/config/cloud",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "",
                "api_key": "sk-test1234567890",
            },
            headers={"x-shared-secret": SHARED_SECRET},
        )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_update_cloud_config_success():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/config/cloud",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4",
                "api_key": "sk-test1234567890",
            },
            headers={"x-shared-secret": SHARED_SECRET},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["cloud_configured"] is True
    assert data["base_url"] == "https://api.example.com/v1"
    assert data["model"] == "gpt-4"
    assert data["has_api_key"] is True
    assert "strategy" in data


@pytest.mark.asyncio
async def test_update_cloud_config_keeps_old_key_when_omitted():
    """Second call without api_key should reuse the key from the first call."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First call: provide api_key
        await client.post(
            "/config/cloud",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4",
                "api_key": "sk-keepthistoken123",
            },
            headers={"x-shared-secret": SHARED_SECRET},
        )
        # Second call: omit api_key
        resp = await client.post(
            "/config/cloud",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4-turbo",
            },
            headers={"x-shared-secret": SHARED_SECRET},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_api_key"] is True


@pytest.mark.asyncio
async def test_update_cloud_config_with_strategy():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/config/cloud",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4",
                "api_key": "sk-test1234567890",
                "strategy": "local_first",
            },
            headers={"x-shared-secret": SHARED_SECRET},
        )
    assert resp.status_code == 200
    data = resp.json()
    # P4-S20-LLM-Unified: strategy field accepted but ignored under unified
    # single-endpoint schema (no local/cloud routing decision). Response
    # echoes the HybridRouter's residual strategy, not the requested one.
    assert "strategy" in data
