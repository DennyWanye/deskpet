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

from llm.code_params import code_params_to_request

logger = logging.getLogger("deskpet.llm.resolution")


def _attach_code_params(entries: list[Any], model_params: Any) -> None:
    """Attach the mapped the relay request fragment to every entry, in-place.

    ``code-session-model-params``: callers read ``entry.code_params``
    and merge it into the OpenAI-compatible request. Empty/None params →
    ``{}`` (provider defaults). Pure + total (never raises).

    Model-aware: ``code_params_to_request`` derives ``reasoning_effort``
    from ``thinking`` (an OpenAI-ism). For a model whose family does NOT
    expose reasoning_effort (Anthropic / Gemini / DeepSeek …) we strip
    that key per-entry so a Claude request never carries a meaningless
    ``reasoning_effort`` field. Capability source = the same family map
    the picker uses, so UI and wire stay consistent.
    """
    base = code_params_to_request(model_params)
    try:
        from llm.model_catalog import model_param_caps as _caps
    except Exception:  # noqa: BLE001 — never let an import break resolution
        _caps = None
    for e in entries:
        frag = dict(base)
        if _caps is not None and "reasoning_effort" in frag:
            try:
                if not _caps(str(getattr(e, "model", "")))["effort"]:
                    frag.pop("reasoning_effort", None)
            except Exception:  # noqa: BLE001 — non-fatal, keep frag as-is
                pass
        try:
            e.code_params = frag
        except Exception:  # noqa: BLE001 — namespace may be slotted; non-fatal
            pass


async def resolve_provider_for_session(
    base_sid: str,
    *,
    is_code_session: bool,
    registry: Any,
    session_db: Any,
    code_default_model: str | None = None,
) -> list[Any]:
    """Return the list of ProviderEntry-like objects this session should walk.

    :param base_sid: session id (sid base, no role suffix).
    :param is_code_session: True for code panel sessions. As of
        2026-05-19 companion ("default") sessions ALSO honor a binding
        if one exists; this flag now only gates the code-mode
        ``code_default_model`` fallback.
    :param registry: an ``LLMProviderRegistry`` instance (or stub with
        the same surface — ``get_chain()``, ``get_entry(id)``).
    :param session_db: a ``SessionDB`` instance (or stub with
        ``async get_code_session_provider_binding(sid)``).

    :return: list of provider entries (shallow copies of the registry's
        ProviderEntry dataclasses). May have their ``model`` field
        overridden if the session has a ``preferred_model`` binding.
        Empty chain is NOT raised here — caller (AgentLoop) emits the
        actionable error event so we don't have to import the error
        class.
    """
    # Step 1: read the per-session binding for ANY session.
    #
    # 2026-05-19: companion ("default") sessions now ALSO honor a
    # per-session model/params binding — the user wants the slim
    # message panel to switch model exactly like Code mode. A session
    # with NO binding row gets {None,None,None} → plain global chain →
    # zero behavior change for anyone who never sets a model (so the
    # earlier "companion untouched" guarantee still holds whenever no
    # binding exists). ``code_default_model`` stays code-mode-only.
    if session_db is None:
        return _global_chain_entries(registry)

    binding = await session_db.get_code_session_provider_binding(base_sid)
    provider_id = binding.get("provider_id")
    preferred_model = binding.get("preferred_model")
    model_params = binding.get("model_params")

    # Step 3: pinned provider — single-element chain when it still exists.
    if provider_id:
        entry = registry.get_entry(provider_id)
        if entry is not None and getattr(entry, "enabled", True):
            # Normalize to a mutable _ChainEntry so code_params attaches
            # uniformly even if ProviderEntry is __slots__-ed.
            pinned = _ChainEntry(
                id=str(getattr(entry, "id", "")),
                name=str(getattr(entry, "name", getattr(entry, "id", ""))),
                base_url=str(getattr(entry, "base_url", "")),
                model=str(getattr(entry, "model", "")),
                api_key_ref=str(getattr(entry, "api_key_ref", "")),
                priority=int(getattr(entry, "priority", 1)),
                enabled=bool(getattr(entry, "enabled", True)),
            )
            if preferred_model:
                pinned.model = preferred_model
            elif is_code_session and code_default_model:
                # code-mode default (e.g. gpt-5.5) — code sessions only.
                # Companion honors an explicit preferred_model binding
                # but never the code-mode default.
                pinned.model = code_default_model
            out = [pinned]
            _attach_code_params(out, model_params)
            return out
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
    elif is_code_session and code_default_model:
        # Unbound CODE session → code-mode default model on every chain
        # entry (Strangler-Fig: caller passes None when knob off →
        # legacy). Companion never takes the code default.
        for entry in chain:
            entry.model = code_default_model
    _attach_code_params(chain, model_params)
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
        "priority", "enabled", "code_params",
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
        # code-session-model-params: filled by _attach_code_params.
        self.code_params: dict = {}

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
