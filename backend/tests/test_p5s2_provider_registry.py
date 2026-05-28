# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 1: LLMProviderRegistry tests (TDD).

Covers:
  - Registry CRUD: add / remove / reorder / set_enabled / list / get_chain
  - Validation: kebab-case id, uniqueness, redacted api_key
  - Migration: legacy [llm.local] → [[llm.endpoints]], idempotent, broken keychain
  - Persistence: toml round-trip via _persist_to_toml + atomic write
  - Keychain: api_key written to keyring, never returned in plaintext

Mocks `keyring` at module-import time via fixture so no host credential
store is touched. Uses tmp_path for the toml file.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest


# ───────────────────────── fixtures ─────────────────────────


class _FakeKeyring:
    """In-memory drop-in for the `keyring` module used by provider_registry."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.delete_log: list[tuple[str, str]] = []

    def set_password(self, service: str, account: str, password: str) -> None:
        self.store[(service, account)] = password

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.delete_log.append((service, account))
        self.store.pop((service, account), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    """Replace the keyring module inside provider_registry with a fake."""
    from llm import provider_registry

    fake = _FakeKeyring()
    monkeypatch.setattr(provider_registry, "keyring", fake, raising=False)
    monkeypatch.setattr(provider_registry, "_KEYRING_AVAILABLE", True, raising=False)
    return fake


@pytest.fixture
def empty_toml(tmp_path: Path) -> Path:
    """A config.toml with no llm section at all."""
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            schema_version = 1

            [backend]
            host = "127.0.0.1"
            port = 8100
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def legacy_toml(tmp_path: Path) -> Path:
    """A config.toml with legacy [llm.local] schema (single provider)."""
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            schema_version = 1

            [backend]
            host = "127.0.0.1"

            [llm.local]
            base_url = "https://api.the relay.example/v1"
            model = "the relay-deepseek-v3"
            api_key = "from-keychain"
            temperature = 0.7
            max_tokens = 2048
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def providers_toml(tmp_path: Path) -> Path:
    """A config.toml already on the new [[llm.endpoints]] schema."""
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            schema_version = 1

            [[llm.endpoints]]
            id = "the relay-deepseek"
            name = "Chinzy DeepSeek"
            base_url = "https://api.the relay.example/v1"
            model = "deepseek-v3"
            api_key_ref = "deskpet.provider.the relay-deepseek"
            priority = 1
            enabled = true
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return p


# ───────────────────────── 1.1: Registry CRUD ─────────────────────────


def _make_provider_kwargs(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "the relay-deepseek",
        "name": "Chinzy DeepSeek",
        "base_url": "https://api.the relay.example/v1",
        "model": "deepseek-v3",
        "api_key": "sk-real-secret",
        "priority": 1,
        "enabled": True,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_add_provider_persists(empty_toml: Path, fake_keyring):
    """1.1 — add() persists to toml + list_providers() returns the entry."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs())

    items = reg.list_providers()
    assert len(items) == 1
    assert items[0]["id"] == "the relay-deepseek"

    # toml on disk has the entry
    import tomli

    with empty_toml.open("rb") as fh:
        data = tomli.load(fh)
    providers = data["llm"]["endpoints"]
    assert len(providers) == 1
    assert providers[0]["id"] == "the relay-deepseek"
    # api_key plaintext NOT written to toml
    assert "api_key" not in providers[0]
    assert providers[0]["api_key_ref"] == "deskpet.provider.the relay-deepseek"

    # keychain has it
    assert fake_keyring.get_password("deskpet", "provider.the relay-deepseek") == "sk-real-secret"


@pytest.mark.asyncio
async def test_remove_provider(empty_toml: Path, fake_keyring):
    """1.2 — remove() drops from list + deletes keychain entry."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a"))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2))

    await reg.remove_provider("a")

    items = reg.list_providers()
    assert len(items) == 1
    assert items[0]["id"] == "b"

    # keychain entry for 'a' was deleted
    assert ("deskpet", "provider.a") in fake_keyring.delete_log
    assert fake_keyring.get_password("deskpet", "provider.a") is None


@pytest.mark.asyncio
async def test_reorder_changes_chain(empty_toml: Path, fake_keyring):
    """1.3 — reorder() updates priorities + get_chain() returns new order."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=1))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2))
    await reg.add_provider(_make_provider_kwargs(id="c", priority=3))

    await reg.reorder(["c", "a", "b"])

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_set_enabled_false_removes_from_chain(empty_toml: Path, fake_keyring):
    """1.4 — disabling provider keeps it in list but removes from chain."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=1))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2))

    await reg.set_enabled("b", False)

    assert len(reg.list_providers()) == 2
    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["a"]


@pytest.mark.asyncio
async def test_get_chain_empty_raises_no_provider_configured(empty_toml: Path, fake_keyring):
    """1.5 — empty registry: get_chain() raises NoProviderConfiguredError."""
    from llm.provider_registry import LLMProviderRegistry, NoProviderConfiguredError

    reg = LLMProviderRegistry(empty_toml)
    with pytest.raises(NoProviderConfiguredError):
        reg.get_chain()


@pytest.mark.asyncio
async def test_get_chain_filters_disabled(empty_toml: Path, fake_keyring):
    """1.6 — get_chain() excludes disabled providers."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=1, enabled=True))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2, enabled=False))
    await reg.add_provider(_make_provider_kwargs(id="c", priority=3, enabled=True))

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["a", "c"]


@pytest.mark.asyncio
async def test_get_chain_stable_on_equal_priority(empty_toml: Path, fake_keyring):
    """1.7 — equal priorities: get_chain() falls back to insertion order."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="x", priority=2))
    await reg.add_provider(_make_provider_kwargs(id="y", priority=2))
    await reg.add_provider(_make_provider_kwargs(id="z", priority=2))

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["x", "y", "z"]


@pytest.mark.asyncio
async def test_list_providers_redacts_api_key(empty_toml: Path, fake_keyring):
    """1.8 — list_providers() returns api_key='********', not plaintext."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a"))

    items = reg.list_providers()
    assert items[0]["api_key"] == "********"
    # Internal ref preserved
    assert items[0]["api_key_ref"] == "deskpet.provider.a"
    # Real key is in fake keyring, never returned
    assert "sk-real-secret" not in str(items)


@pytest.mark.asyncio
async def test_add_provider_unique_id_validation(empty_toml: Path, fake_keyring):
    """1.9 — duplicate id raises ValueError."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="dup"))

    with pytest.raises(ValueError, match="already exists|duplicate|unique"):
        await reg.add_provider(_make_provider_kwargs(id="dup"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        "UpperCase",
        "snake_case",
        "with spaces",
        "trailing-",
        "-leading",
        "double--dash",
        "",
        "x" * 33,  # > 32 chars
        "with.dot",
        "with/slash",
    ],
)
async def test_add_provider_kebab_case_validation(empty_toml: Path, fake_keyring, bad_id: str):
    """1.10 — non-kebab-case id raises ValueError."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    with pytest.raises(ValueError, match="kebab-case|invalid id|invalid provider id"):
        await reg.add_provider(_make_provider_kwargs(id=bad_id))


# ───────────────────────── 1.3: Migration ─────────────────────────


def test_migrate_legacy_llm_local_to_providers(legacy_toml: Path, fake_keyring):
    """1.14 — legacy [llm.local] is converted to [[llm.endpoints]] with one entry."""
    from llm.provider_registry import _migrate_legacy_provider_config

    # Pre-populate keychain with the existing cloud key so the migration can
    # reference it (matches design.md: api_key_ref = "deskpet.cloud_api_key").
    fake_keyring.set_password("deskpet", "cloud_api_key", "existing-cloud-key")

    _migrate_legacy_provider_config(legacy_toml)

    import tomli

    with legacy_toml.open("rb") as fh:
        data = tomli.load(fh)
    providers = data["llm"]["endpoints"]
    assert len(providers) == 1
    entry = providers[0]
    assert entry["id"] == "legacy-default"
    assert entry["base_url"] == "https://api.the relay.example/v1"
    # v2 schema: models is the canonical array
    assert entry["models"] == ["the relay-deepseek-v3"]
    assert entry.get("default_model") == "the relay-deepseek-v3"
    assert entry["api_key_ref"] == "deskpet.cloud_api_key"
    assert entry["priority"] == 1
    assert entry["enabled"] is True
    assert "api_key" not in entry  # never plaintext


def test_migration_idempotent(providers_toml: Path, fake_keyring):
    """1.15 — already migrated toml stays unchanged on second migration call."""
    from llm.provider_registry import _migrate_legacy_provider_config

    before = providers_toml.read_text(encoding="utf-8")
    _migrate_legacy_provider_config(providers_toml)
    after = providers_toml.read_text(encoding="utf-8")

    # Content semantically unchanged: parse both and compare.
    import tomli

    before_data = tomli.loads(before)
    after_data = tomli.loads(after)
    assert before_data["llm"]["endpoints"] == after_data["llm"]["endpoints"]
    # And specifically: still exactly 1 entry, not 2 (no duplicate legacy-default).
    assert len(after_data["llm"]["endpoints"]) == 1
    assert after_data["llm"]["endpoints"][0]["id"] == "the relay-deepseek"


def test_migration_handles_missing_keychain_key(legacy_toml: Path, fake_keyring, caplog):
    """1.16 — migration creates entry + warning even when keychain key missing."""
    import logging

    from llm.provider_registry import _migrate_legacy_provider_config

    # keychain intentionally NOT populated → resolve will return None
    with caplog.at_level(logging.WARNING, logger="deskpet.llm.provider_registry"):
        _migrate_legacy_provider_config(legacy_toml)

    import tomli

    with legacy_toml.open("rb") as fh:
        data = tomli.load(fh)
    providers = data["llm"]["endpoints"]
    assert len(providers) == 1
    assert providers[0]["api_key_ref"] == "deskpet.cloud_api_key"

    # warning was emitted
    msgs = [r.getMessage() for r in caplog.records]
    assert any("not found" in m or "missing" in m or "re-enter" in m for m in msgs), msgs


def test_migration_no_op_on_fresh_install(empty_toml: Path, fake_keyring):
    """Fresh install (no llm section at all): migration is no-op, no crash."""
    from llm.provider_registry import _migrate_legacy_provider_config

    before = empty_toml.read_text(encoding="utf-8")
    _migrate_legacy_provider_config(empty_toml)
    after = empty_toml.read_text(encoding="utf-8")

    import tomli

    after_data = tomli.loads(after)
    assert "providers" not in after_data.get("llm", {})
