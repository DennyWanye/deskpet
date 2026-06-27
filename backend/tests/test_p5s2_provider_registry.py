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
            base_url = "https://api.your-llm-relay.example.com/v1"
            model = "relay-deepseek-v3"
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
            id = "relay-deepseek"
            name = "Relay DeepSeek"
            base_url = "https://api.your-llm-relay.example.com/v1"
            model = "deepseek-v3"
            api_key_ref = "deskpet.provider.relay-deepseek"
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
        "id": "relay-deepseek",
        "name": "Relay DeepSeek",
        "base_url": "https://api.your-llm-relay.example.com/v1",
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
    assert items[0]["id"] == "relay-deepseek"

    # toml on disk has the entry
    import tomli

    with empty_toml.open("rb") as fh:
        data = tomli.load(fh)
    providers = data["llm"]["endpoints"]
    assert len(providers) == 1
    assert providers[0]["id"] == "relay-deepseek"
    # api_key plaintext NOT written to toml
    assert "api_key" not in providers[0]
    assert providers[0]["api_key_ref"] == "deskpet.provider.relay-deepseek"

    # keychain has it
    assert fake_keyring.get_password("deskpet", "provider.relay-deepseek") == "sk-real-secret"


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
    assert entry["base_url"] == "https://api.your-llm-relay.example.com/v1"
    # v2 schema: models is the canonical array
    assert entry["models"] == ["relay-deepseek-v3"]
    assert entry.get("default_model") == "relay-deepseek-v3"
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
    assert after_data["llm"]["endpoints"][0]["id"] == "relay-deepseek"


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


# ───────────────────────── relay-managed provider upsert ─────────────────────────


@pytest.mark.asyncio
async def test_source_and_account_ref_roundtrip_toml(empty_toml: Path, fake_keyring):
    """Relay-managed metadata persists to toml and reloads into public dicts."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            source="relay",
            account_ref="acct-123",
        )
    )

    reloaded = LLMProviderRegistry(empty_toml)
    items = reloaded.list_providers()
    assert items[0]["source"] == "relay"
    assert items[0]["account_ref"] == "acct-123"

    import tomli

    with empty_toml.open("rb") as fh:
        data = tomli.load(fh)
    provider = data["llm"]["endpoints"][0]
    assert provider["source"] == "relay"
    assert provider["account_ref"] == "acct-123"


@pytest.mark.asyncio
async def test_user_provider_omits_source_account_lines(empty_toml: Path, fake_keyring):
    """Manual providers keep byte-level TOML shape: no default metadata lines."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="manual-one"))

    text = empty_toml.read_text(encoding="utf-8")
    assert "\nsource = " not in text
    assert "\naccount_ref = " not in text


@pytest.mark.asyncio
async def test_ensure_provider_idempotent(empty_toml: Path, fake_keyring):
    """ensure_provider inserts once, then updates the same relay row."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    first = await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            source="relay",
            account_ref="acct-a",
        )
    )
    second = await reg.ensure_provider(
        {
            "id": "relay-cloud",
            "name": "Relay Cloud Updated",
            "base_url": "https://relay.example.com/v2",
            "models": ["gpt-4o-mini"],
            "source": "relay",
            "account_ref": "acct-b",
        }
    )

    assert first.id == second.id == "relay-cloud"
    items = reg.list_providers()
    assert [p["id"] for p in items] == ["relay-cloud"]
    assert items[0]["name"] == "Relay Cloud Updated"
    assert items[0]["account_ref"] == "acct-b"


@pytest.mark.asyncio
async def test_ensure_preserves_user_reorder(empty_toml: Path, fake_keyring):
    """Relay priority steal keeps existing manual relative order intact."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="manual-a", priority=1))
    await reg.add_provider(_make_provider_kwargs(id="manual-b", priority=2))
    await reg.reorder(["manual-b", "manual-a"])

    relay_payload = _make_provider_kwargs(
        id="relay-cloud",
        source="relay",
        account_ref="acct",
    )
    relay_payload.pop("priority")
    await reg.ensure_provider(relay_payload)

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["relay-cloud", "manual-b", "manual-a"]


@pytest.mark.asyncio
async def test_ensure_updates_key(empty_toml: Path, fake_keyring):
    """ensure_provider rewrites keychain when api_key is supplied."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            api_key="sk-old",
            source="relay",
            account_ref="acct",
        )
    )
    await reg.ensure_provider(
        {
            "id": "relay-cloud",
            "api_key": "sk-new",
            "source": "relay",
            "account_ref": "acct",
        }
    )

    assert reg.resolve_api_key("relay-cloud") == "sk-new"


@pytest.mark.asyncio
async def test_ensure_first_login_steals_default_priority(empty_toml: Path, fake_keyring):
    """First relay login becomes the default provider ahead of manual rows."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="manual-one", priority=1))

    relay_payload = _make_provider_kwargs(
        id="relay-cloud",
        source="relay",
        account_ref="acct",
    )
    relay_payload.pop("priority")
    await reg.ensure_provider(relay_payload)

    public = reg.list_providers()
    assert {p["id"]: p["priority"] for p in public} == {
        "manual-one": 2,
        "relay-cloud": 1,
    }
    assert [p["id"] for p in reg.get_chain()] == ["relay-cloud", "manual-one"]


@pytest.mark.asyncio
async def test_ensure_raises_key_missing_when_keychain_empty(empty_toml: Path, fake_keyring):
    """Existing managed provider without a stored key asks caller to recover."""
    from llm.provider_registry import KeyMissingError, LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            source="relay",
            account_ref="acct",
        )
    )
    fake_keyring.delete_password("deskpet", "provider.relay-cloud")

    with pytest.raises(KeyMissingError) as excinfo:
        await reg.ensure_provider(
            {
                "id": "relay-cloud",
                "source": "relay",
                "account_ref": "acct",
            }
        )

    assert excinfo.value.provider_id == "relay-cloud"


@pytest.mark.asyncio
async def test_ensure_updates_base_url_models(empty_toml: Path, fake_keyring):
    """ensure_provider updates mutable endpoint metadata on existing rows."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            base_url="https://relay.example.com/v1",
            model="old-model",
            source="relay",
            account_ref="acct",
        )
    )
    await reg.ensure_provider(
        {
            "id": "relay-cloud",
            "base_url": "https://relay.example.com/v2",
            "models": ["new-a", "new-b"],
            "default_model": "new-b",
            "source": "relay",
            "account_ref": "acct",
        }
    )

    item = reg.list_providers()[0]
    assert item["base_url"] == "https://relay.example.com/v2"
    assert item["models"] == ["new-a", "new-b"]
    assert item["default_model"] == "new-b"
    assert item["model"] == "new-b"


@pytest.mark.asyncio
async def test_normalize_priorities_unique(empty_toml: Path, fake_keyring):
    """_normalize_priorities rewrites stable priority order to 1..N."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=5))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=5))
    await reg.add_provider(_make_provider_kwargs(id="c", priority=3))

    reg._normalize_priorities()

    items = sorted(reg.list_providers(), key=lambda p: p["priority"])
    assert [(p["id"], p["priority"]) for p in items] == [
        ("c", 1),
        ("a", 2),
        ("b", 3),
    ]


def test_ws_settings_provider_relay_messages_are_wired():
    """WI-2 guard: relay provider WS messages enter the registry branch and
    delegate to llm.relay_provider_ops (behaviour tested below)."""
    main_py = Path(__file__).resolve().parents[1] / "main.py"
    text = main_py.read_text(encoding="utf-8")

    # msg types enter the settings_providers branch
    assert '"settings_providers_ensure"' in text
    assert '"settings_providers_relay_logout"' in text
    # handler delegates to the testable ops module
    assert "ensure_relay_provider" in text
    assert "relay_logout" in text

    ops_py = main_py.parent / "llm" / "relay_provider_ops.py"
    ops_text = ops_py.read_text(encoding="utf-8")
    assert "relay_provider_ensured" in ops_text
    assert "key_fingerprint" in ops_text


# ───────────── Phase B / WI-2: relay_provider_ops behaviour ─────────────
# Behaviour-level coverage of the WS-handler decision logic, extracted to
# llm.relay_provider_ops so it is testable without importing main.py.

_RELAY_PAYLOAD = {
    "id": "relay-cloud",
    "source": "relay",
    "account_ref": "acct-a",
    "name": "中转站 · relay",
    "base_url": "https://relay.example.com/v1",
    "models": ["gpt-5.5"],
    "default_model": "gpt-5.5",
    "api_key": "tsk_live_SUPERSECRET123",
}


@pytest.mark.asyncio
async def test_ws_ensure_rejects_non_relay_source(empty_toml: Path, fake_keyring):
    from llm.provider_registry import LLMProviderRegistry
    from llm.relay_provider_ops import ensure_relay_provider

    reg = LLMProviderRegistry(empty_toml)
    err = await ensure_relay_provider(reg, {**_RELAY_PAYLOAD, "id": "x", "source": "user"})
    assert err is not None and err["reason"] == "ensure_only_managed"
    assert reg.get_entry("x") is None  # rejected, never added


@pytest.mark.asyncio
async def test_ws_ensure_relay_provider_succeeds_and_lists(empty_toml: Path, fake_keyring):
    from llm.provider_registry import LLMProviderRegistry
    from llm.relay_provider_ops import ensure_relay_provider

    reg = LLMProviderRegistry(empty_toml)
    err = await ensure_relay_provider(reg, dict(_RELAY_PAYLOAD))
    assert err is None
    pub = [p for p in reg.list_providers() if p["id"] == "relay-cloud"]
    assert pub, "relay-cloud should appear in list_providers()"
    assert pub[0]["source"] == "relay"
    assert pub[0]["account_ref"] == "acct-a"
    assert pub[0]["api_key"] == "********"  # redacted in public view
    assert reg.resolve_api_key("relay-cloud") == "tsk_live_SUPERSECRET123"  # real key stored


@pytest.mark.asyncio
async def test_ws_ensure_key_missing_returns_error(empty_toml: Path, fake_keyring):
    from llm.provider_registry import LLMProviderRegistry
    from llm.relay_provider_ops import ensure_relay_provider

    reg = LLMProviderRegistry(empty_toml)
    await ensure_relay_provider(reg, dict(_RELAY_PAYLOAD))
    fake_keyring.delete_password("deskpet", "provider.relay-cloud")  # simulate local key loss
    # ensure again WITHOUT api_key → must signal key_missing for re-mint
    err = await ensure_relay_provider(
        reg, {k: v for k, v in _RELAY_PAYLOAD.items() if k != "api_key"}
    )
    assert err is not None
    assert err["reason"] == "key_missing"
    assert err["provider_id"] == "relay-cloud"


@pytest.mark.asyncio
async def test_ws_ensure_never_logs_plaintext_key(empty_toml: Path, fake_keyring, caplog):
    import logging

    from llm.provider_registry import LLMProviderRegistry
    from llm.relay_provider_ops import ensure_relay_provider, key_fingerprint

    reg = LLMProviderRegistry(empty_toml)
    secret = _RELAY_PAYLOAD["api_key"]
    with caplog.at_level(logging.INFO):
        await ensure_relay_provider(reg, dict(_RELAY_PAYLOAD))
    assert secret not in caplog.text  # plaintext key NEVER logged
    assert key_fingerprint(secret) in caplog.text  # only the fingerprint


@pytest.mark.asyncio
async def test_ws_relay_logout_disables_and_deletes_key(empty_toml: Path, fake_keyring):
    from llm.provider_registry import LLMProviderRegistry
    from llm.relay_provider_ops import ensure_relay_provider, relay_logout

    reg = LLMProviderRegistry(empty_toml)
    await ensure_relay_provider(reg, dict(_RELAY_PAYLOAD))
    await relay_logout(reg)
    entry = reg.get_entry("relay-cloud")
    assert entry is not None
    assert entry.enabled is False  # disabled, not deleted (preserves ordering)
    assert entry.account_ref == ""  # account fingerprint cleared
    assert reg.resolve_api_key("relay-cloud") is None  # A's key deleted — never reused by B


@pytest.mark.asyncio
async def test_ws_relay_logout_noop_when_absent(empty_toml: Path, fake_keyring):
    from llm.provider_registry import LLMProviderRegistry
    from llm.relay_provider_ops import relay_logout

    reg = LLMProviderRegistry(empty_toml)
    await relay_logout(reg)  # no relay-cloud row → must not raise
