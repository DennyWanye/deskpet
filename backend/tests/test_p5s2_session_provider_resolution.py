"""P5-S2 Phase 3.2: per-session provider resolution.

``resolve_provider_for_session(base_sid, *, is_code_session)`` chooses
which providers AgentLoop will walk for THIS session. The algorithm
(from openspec/changes/multi-provider-management/specs/code-session-provider-binding/spec.md):

  1. Companion sessions skip the SessionDB lookup → always use the
     global chain.
  2. For code sessions, read SessionDB.code_session_provider for the sid.
  3. If provider_id set AND provider exists + is enabled → single-element
     chain [provider]; if preferred_model is also set, override the
     provider's model field for THIS session only.
  4. If provider_id set but the provider was deleted/disabled → fall
     back to the global chain (auto-recovery; cleanup happens on
     provider removal but a race is possible).
  5. If provider_id is NULL → return registry.get_chain() unchanged
     (or with each provider's model overridden by preferred_model).
"""
from __future__ import annotations

from typing import Any

import pytest


# ────────────────────── stubs ──────────────────────


class _StubRegistry:
    """Stand-in for LLMProviderRegistry exposing only what resolution needs."""

    def __init__(self, providers: list[dict]) -> None:
        """`providers` is a list of dicts with keys
        id / base_url / model / api_key / enabled (defaults True)."""
        self._providers = providers

    def get_chain(self) -> list[dict]:
        """Return enabled providers in priority order — dict form."""
        return [p for p in self._providers if p.get("enabled", True)]

    def get_entry(self, provider_id: str):
        """Return the in-memory dataclass-style entry or None.

        Used by resolution to check 'still exists + enabled'. We mimic
        the real registry's behaviour: returns the dict (or a tiny
        namespace) when found, None otherwise.
        """
        for p in self._providers:
            if p["id"] == provider_id:
                return _EntryNS(**p)
        return None

    def resolve_api_key(self, provider_id: str) -> str | None:
        for p in self._providers:
            if p["id"] == provider_id:
                return p.get("api_key", "stub-key")
        return None


class _EntryNS:
    """Tiny namespace mirroring ProviderEntry dataclass field access."""

    def __init__(self, **fields) -> None:
        # Defaults match ProviderEntry
        self.id = fields["id"]
        self.name = fields.get("name", self.id)
        self.base_url = fields["base_url"]
        self.model = fields["model"]
        self.api_key_ref = fields.get("api_key_ref", f"keychain://{self.id}")
        self.priority = int(fields.get("priority", 1))
        self.enabled = bool(fields.get("enabled", True))


class _StubSessionDB:
    """Stand-in for SessionDB.get_code_session_provider_binding."""

    def __init__(self, bindings: dict[str, dict[str, str | None]]) -> None:
        """bindings: {sid: {provider_id, preferred_model}}."""
        self._bindings = bindings
        self.get_calls: list[str] = []

    async def get_code_session_provider_binding(
        self, base_session_id: str
    ) -> dict[str, Any]:
        self.get_calls.append(base_session_id)
        return self._bindings.get(
            base_session_id,
            {"provider_id": None, "preferred_model": None},
        )


# ────────────────────── tests ──────────────────────


@pytest.mark.asyncio
async def test_pinned_session_returns_single_chain() -> None:
    """3.8: binding provider_id='chinzy' → chain = [chinzy] only."""
    from llm.resolution import resolve_provider_for_session

    registry = _StubRegistry([
        {"id": "chinzy", "base_url": "https://chinzy.example/v1",
         "model": "deepseek-v4-pro", "api_key": "k1", "enabled": True},
        {"id": "openrouter", "base_url": "https://openrouter.example/v1",
         "model": "claude-4.7-sonnet", "api_key": "k2", "enabled": True},
        {"id": "ollama", "base_url": "http://localhost:11434/v1",
         "model": "gemma", "api_key": "ollama", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "vpn-tunnel": {"provider_id": "chinzy", "preferred_model": None},
    })

    chain = await resolve_provider_for_session(
        "vpn-tunnel",
        is_code_session=True,
        registry=registry,
        session_db=sdb,
    )

    assert len(chain) == 1
    assert chain[0].id == "chinzy"
    # model unchanged because preferred_model is None
    assert chain[0].model == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_unbound_session_returns_global_chain() -> None:
    """3.9: binding all None → registry.get_chain()."""
    from llm.resolution import resolve_provider_for_session

    registry = _StubRegistry([
        {"id": "chinzy", "base_url": "https://a.example/v1",
         "model": "m1", "api_key": "k1", "enabled": True},
        {"id": "openrouter", "base_url": "https://b.example/v1",
         "model": "m2", "api_key": "k2", "enabled": True},
    ])
    sdb = _StubSessionDB({})  # no binding for any sid

    chain = await resolve_provider_for_session(
        "fresh-code-session",
        is_code_session=True,
        registry=registry,
        session_db=sdb,
    )

    assert [p.id for p in chain] == ["chinzy", "openrouter"]


@pytest.mark.asyncio
async def test_preferred_model_only_overrides_model_field() -> None:
    """3.10: preferred_model set but no provider_id → global chain with
    every provider's model overridden to preferred_model."""
    from llm.resolution import resolve_provider_for_session

    registry = _StubRegistry([
        {"id": "chinzy", "base_url": "https://a.example/v1",
         "model": "deepseek-v4-pro", "api_key": "k1", "enabled": True},
        {"id": "openrouter", "base_url": "https://b.example/v1",
         "model": "claude-4.7-sonnet", "api_key": "k2", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "research": {
            "provider_id": None,
            "preferred_model": "claude-4.7-opus",
        },
    })

    chain = await resolve_provider_for_session(
        "research",
        is_code_session=True,
        registry=registry,
        session_db=sdb,
    )

    # Same two providers but model overridden on each.
    assert len(chain) == 2
    assert chain[0].id == "chinzy"
    assert chain[0].model == "claude-4.7-opus"
    assert chain[1].id == "openrouter"
    assert chain[1].model == "claude-4.7-opus"


@pytest.mark.asyncio
async def test_pinned_to_deleted_provider_falls_back_to_chain() -> None:
    """3.11: provider_id points to non-existent provider → fall back to
    global chain (auto-recovery)."""
    from llm.resolution import resolve_provider_for_session

    registry = _StubRegistry([
        # Only 'chinzy' exists. Binding points at 'gone-provider'.
        {"id": "chinzy", "base_url": "https://a.example/v1",
         "model": "m1", "api_key": "k1", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "stale-binding": {
            "provider_id": "gone-provider",
            "preferred_model": None,
        },
    })

    chain = await resolve_provider_for_session(
        "stale-binding",
        is_code_session=True,
        registry=registry,
        session_db=sdb,
    )

    # Resolution falls through to the global chain (the only enabled
    # provider, 'chinzy').
    assert [p.id for p in chain] == ["chinzy"]


@pytest.mark.asyncio
async def test_companion_session_honors_binding_2026_05_19() -> None:
    """Contract change 2026-05-19: companion ("default") sessions NOW
    read the per-session binding too (user wants the slim message panel
    to switch model like Code mode). A stale provider_id still falls
    back to the global chain — same recovery as code sessions."""
    from llm.resolution import resolve_provider_for_session

    registry = _StubRegistry([
        {"id": "chinzy", "base_url": "https://a.example/v1",
         "model": "m1", "api_key": "k1", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "default": {"provider_id": "imaginary", "preferred_model": None},
    })

    chain = await resolve_provider_for_session(
        "default",
        is_code_session=False,  # companion mode
        registry=registry,
        session_db=sdb,
    )

    # DB WAS queried (new behavior); imaginary provider → global fallback.
    assert [p.id for p in chain] == ["chinzy"]
    assert sdb.get_calls == ["default"]


@pytest.mark.asyncio
async def test_companion_session_preferred_model_applies() -> None:
    """Companion with a preferred_model binding overrides the model on
    the global chain (the message-panel model switcher path)."""
    from llm.resolution import resolve_provider_for_session

    registry = _StubRegistry([
        {"id": "chinzy", "base_url": "https://a.example/v1",
         "model": "deepseek-v4-pro", "api_key": "k1", "enabled": True},
    ])
    sdb = _StubSessionDB({
        "default": {"provider_id": None, "preferred_model": "gpt-5.5",
                    "model_params": {"effort": "high"}},
    })

    chain = await resolve_provider_for_session(
        "default",
        is_code_session=False,
        registry=registry,
        session_db=sdb,
    )

    assert [p.id for p in chain] == ["chinzy"]
    assert chain[0].model == "gpt-5.5"  # binding overrides companion model
    assert chain[0].code_params.get("reasoning_effort") == "high"
