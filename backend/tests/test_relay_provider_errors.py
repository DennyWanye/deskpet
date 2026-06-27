# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from llm.errors import LLMAuthError
from llm.openai_adapter import OpenAIAdapter
from llm.relay_errors import (
    INSUFFICIENT_BALANCE,
    RELAY_KEY_INVALID,
    classify_relay_error,
)
from providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (403, {"code": "INSUFFICIENT_BALANCE", "balance_minor": 0}, INSUFFICIENT_BALANCE),
        (403, {"code": "FORBIDDEN", "balance_minor": 0}, INSUFFICIENT_BALANCE),
        (402, {"error": {"code": "insufficient_balance"}}, INSUFFICIENT_BALANCE),
        (401, {"code": "INVALID_TOKEN"}, RELAY_KEY_INVALID),
        (401, {"code": "EXPIRED_TOKEN"}, RELAY_KEY_INVALID),
    ],
)
def test_classify_relay_error_structured_contract(
    status: int, body: dict, expected: str
) -> None:
    assert classify_relay_error(status, body=body) == expected


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (429, {"code": "RATE_LIMITED"}),
        (502, {"code": "UPSTREAM_ERROR"}),
        (503, {"code": "UPSTREAM_TIMEOUT"}),
        (404, {"code": "DEVICE_KEY_MISSING"}),
    ],
)
def test_classify_relay_error_ignores_non_key_balance_codes(
    status: int, body: dict
) -> None:
    assert classify_relay_error(status, body=body) is None


def test_llm_auth_error_preserves_status_code_and_error_class() -> None:
    exc = LLMAuthError(
        "balance",
        provider="relay",
        status_code=403,
        error_class=INSUFFICIENT_BALANCE,
    )

    assert exc.status_code == 403
    assert exc.error_class == INSUFFICIENT_BALANCE
    assert exc.provider == "relay"


def _api_status_error(status: int, body: dict) -> Exception:
    return SimpleNamespace(
        status_code=status,
        body=body,
        response=SimpleNamespace(text="body text", headers={}),
    )


def test_openai_adapter_maps_relay_auth_errors_with_error_class() -> None:
    provider = OpenAIAdapter(api_key="sk-test", base_url="https://relay.example/v1", is_relay=True)

    mapped = provider._map_error(
        _api_status_error(403, {"code": "INSUFFICIENT_BALANCE"})
    )

    assert isinstance(mapped, LLMAuthError)
    assert mapped.status_code == 403
    assert mapped.error_class == INSUFFICIENT_BALANCE


def test_openai_adapter_does_not_classify_non_relay_auth_errors() -> None:
    provider = OpenAIAdapter(api_key="sk-test", base_url="https://api.example/v1")

    mapped = provider._map_error(_api_status_error(401, {"code": "INVALID_TOKEN"}))

    assert isinstance(mapped, LLMAuthError)
    assert mapped.status_code == 401
    assert mapped.error_class is None


@pytest.mark.asyncio
async def test_openai_compatible_provider_classifies_relay_http_body_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"code": "insufficient_balance"}})

    provider = OpenAICompatibleProvider(
        base_url="https://relay.example/v1",
        api_key="tsk_x",
        model="gpt-5.5",
        is_relay=True,
    )
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(Exception) as ei:
        await provider.chat_with_tools([{"role": "user", "content": "hi"}])

    assert getattr(ei.value, "status_code", None) == 402
    assert getattr(ei.value, "error_class", None) == INSUFFICIENT_BALANCE


@pytest.mark.asyncio
async def test_openai_compatible_provider_does_not_classify_non_relay() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "INVALID_TOKEN"})

    provider = OpenAICompatibleProvider(
        base_url="https://api.example/v1",
        api_key="sk_x",
        model="gpt-5.5",
    )
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(Exception) as ei:
        await provider.chat_with_tools([{"role": "user", "content": "hi"}])

    assert getattr(ei.value, "status_code", None) == 401
    assert getattr(ei.value, "error_class", None) is None
