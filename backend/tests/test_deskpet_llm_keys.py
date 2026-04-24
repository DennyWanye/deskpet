"""Unit tests for llm.keys: env precedence, mask_key shape."""
from __future__ import annotations

import llm.keys as keys_mod
from llm.keys import get_api_key, mask_key


def test_env_var_takes_priority(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-anthropic-12345")
    assert get_api_key("anthropic") == "sk-env-anthropic-12345"


def test_env_var_missing_falls_through_to_keyring(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(keys_mod, "_KEYRING_AVAILABLE", True)

    class FakeKeyring:
        @staticmethod
        def get_password(svc, name):
            assert svc == "deskpet"
            assert name == "openai_api_key"
            return "sk-from-keyring-abcdef"

    monkeypatch.setattr(keys_mod, "keyring", FakeKeyring())
    assert get_api_key("openai") == "sk-from-keyring-abcdef"


def test_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(keys_mod, "_KEYRING_AVAILABLE", False)
    monkeypatch.setattr(keys_mod, "keyring", None)
    assert get_api_key("gemini") is None


def test_keyring_exception_doesnt_crash(monkeypatch):
    """keyring backend can raise on locked session — resolution MUST return None,
    not propagate the exception."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keys_mod, "_KEYRING_AVAILABLE", True)

    class CrankyKeyring:
        @staticmethod
        def get_password(svc, name):
            raise RuntimeError("D-Bus is asleep")

    monkeypatch.setattr(keys_mod, "keyring", CrankyKeyring())
    assert get_api_key("anthropic") is None


def test_unknown_provider_returns_none(monkeypatch):
    monkeypatch.setattr(keys_mod, "_KEYRING_AVAILABLE", False)
    assert get_api_key("nonexistent-provider") is None


def test_mask_key_sk_prefix():
    assert mask_key("sk-abcdefghij") == "sk-****ghij"


def test_mask_key_aiza_prefix():
    assert mask_key("AIzaSyABC123XYZ456") == "AIza****Z456"


def test_mask_key_generic_prefix():
    assert mask_key("randomkey-abcd1234") == "****1234"


def test_mask_key_empty_or_none_returns_placeholder():
    assert mask_key(None) == "****"
    assert mask_key("") == "****"
    assert mask_key("short") == "****"  # too short to safely show last4
