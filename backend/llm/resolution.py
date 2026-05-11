"""P5-S2 Phase 3.2 — per-session provider resolution.

``resolve_provider_for_session(base_sid, *, is_code_session, registry,
session_db)`` decides which LLM provider entries to walk for THIS chat
turn.

Algorithm (from
openspec/changes/multi-provider-management/specs/code-session-provider-binding/spec.md
"Resolution algorithm"):

    1. Companion sessions skip the SessionDB lookup — always use the
       global chain (companion sessions don't carry per-session bindings).
    2. For code sessions, read ``code_session_provider`` for ``base_sid``.
    3. If ``provider_id`` is set AND the provider still exists in
       registry AND it is enabled → return single-element chain
       ``[provider]``; if ``preferred_model`` is also set, override the
       returned entry's ``model`` field for THIS session only (in-memory
       copy — never mutates the registry).
    4. If ``provider_id`` is set but the provider was deleted or
       disabled → fall through to the global chain (auto-recovery; the
       registry's remove flow already cleans bindings but a race window
       is possible during long-running sessions).
    5. If ``provider_id`` is NULL → return ``registry.get_chain()``
       (enabled providers, priority-sorted). If ``preferred_model`` is
       set, override the model on EVERY chain entry.

Return type: ``list[ProviderEntry]`` (the registry's dataclass).
Returned entries are SAFE TO MUTATE — callers receive shallow copies
so adjusting ``.model`` for the preferred_model case can't leak back
into the registry.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger("deskpet.llm.resolution")


async def resolve_provider_for_session(
    base_sid: str,
    *,
    is_code_session: bool,
    registry: Any,
    session_db: Any,
) -> list[Any]:
    """Return the list of ProviderEntry-like objects this session should walk.

    :param base_sid: session id (sid base, no role suffix).
    :param is_code_session: True for code panel sessions (have bindings);
        False for companion sessions (no binding lookup, ever).
    :param registry: an ``LLMProviderRegistry`` instance (or stub with
        the same surface — ``get_chain()``, ``get_entry(id)``).
    :param session_db: a ``SessionDB`` instance (or stub with
        ``async get_code_session_provider_binding(sid)``). Unused when
        ``is_code_session`` is False.

    :return: list of provider entries (shallow copies of the registry's
        ProviderEntry dataclasses). May have their ``model`` field
        overridden if the session has a ``preferred_model`` binding.
        Empty chain is NOT raised here — caller (AgentLoop) emits the
        actionable error event so we don't have to import the error
        class.
    """
    # Step 1: companion sessions skip DB lookup entirely.
    if not is_code_session:
        return _global_chain_entries(registry)

    # Step 2: read binding for code session.
    binding = await session_db.get_code_session_provider_binding(base_sid)
    provider_id = binding.get("provider_id")
    preferred_model = binding.get("preferred_model")

    # Step 3: pinned provider — single-element chain when it still exists.
    if provider_id:
        entry = registry.get_entry(provider_id)
        if entry is not None and getattr(entry, "enabled", True):
            pinned = copy.copy(entry)
            if preferred_model:
                pinned.model = preferred_model
            return [pinned]
        # Step 4: pinned-to-deleted (or disabled) — log + fall through.
        logger.info(
            "session_binding_stale sid=%s provider_id=%s "
            "(falling back to global chain)",
            base_sid, provider_id,
        )

    # Step 5: NULL provider_id (or fell through from step 4) — global chain.
    chain = _global_chain_entries(registry)
    if preferred_model:
        for entry in chain:
            entry.model = preferred_model
    return chain


def _global_chain_entries(registry: Any) -> list[Any]:
    """Pull the global chain from the registry as a list of mutable copies.

    ``LLMProviderRegistry.get_chain()`` returns ``list[dict]`` (api_key
    redacted) in production. Stubs in tests can return either dicts or
    ProviderEntry-like namespaces. We normalize both into a list of
    namespace objects with ``.id`` and ``.model`` attributes so callers
    can mutate ``.model`` for preferred_model overrides without touching
    the registry's internal state.
    """
    raw = registry.get_chain()
    out: list[Any] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(_ChainEntry.from_dict(item))
        else:
            out.append(copy.copy(item))
    return out


class _ChainEntry:
    """Mutable view of a provider chain entry.

    Mirrors the attribute surface of ``ProviderEntry`` (id, name,
    base_url, model, api_key_ref, priority, enabled) so AgentLoop's
    chain-walking code can read them uniformly. Used only when the
    registry returns dicts (production path); ProviderEntry instances
    are passed through ``copy.copy()`` unchanged.
    """

    __slots__ = (
        "id", "name", "base_url", "model", "api_key_ref",
        "priority", "enabled",
    )

    def __init__(
        self,
        *,
        id: str,
        name: str,
        base_url: str,
        model: str,
        api_key_ref: str = "",
        priority: int = 1,
        enabled: bool = True,
    ) -> None:
        self.id = id
        self.name = name
        self.base_url = base_url
        self.model = model
        self.api_key_ref = api_key_ref
        self.priority = priority
        self.enabled = enabled

    @classmethod
    def from_dict(cls, d: dict) -> "_ChainEntry":
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", d.get("id", ""))),
            base_url=str(d.get("base_url", "")),
            model=str(d.get("model", "")),
            api_key_ref=str(d.get("api_key_ref", "")),
            priority=int(d.get("priority", 1)),
            enabled=bool(d.get("enabled", True)),
        )


__all__ = ["resolve_provider_for_session"]
