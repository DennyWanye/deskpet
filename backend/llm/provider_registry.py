# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""LLMProviderRegistry — P5-S2 Phase 1 (multi-provider-management).

This is the single source of truth for the user's list of LLM providers.
Owns:
  - In-memory list of ProviderEntry dataclasses
  - Atomic persistence to `config.toml` `[[llm.endpoints]]` array
  - API keys stored in OS keychain under service "deskpet", account
    "provider.<provider_id>" (NEVER plaintext in toml)
  - Migration of legacy `[llm.local]` single-provider schema → new list

Schema in config.toml::

    [[llm.endpoints]]
    id = "relay-deepseek"
    name = "Relay DeepSeek"
    base_url = "https://api.your-llm-relay.example.com/v1"
    model = "deepseek-v3"
    api_key_ref = "deskpet.provider.relay-deepseek"
    priority = 1
    enabled = true

`api_key_ref` is the only secret-shaped field. The real key lives in the
OS keychain at `keyring.get_password("deskpet", "provider.<id>")`. The
``api_key_ref`` string is just a UI hint; persistence is keyed by ``id``.

Public API::

    reg = LLMProviderRegistry(path_to_config_toml)
    await reg.add_provider({...})
    reg.list_providers()                # api_key redacted to "********"
    reg.get_chain()                     # enabled, ordered by priority
    await reg.remove_provider(id)
    await reg.set_enabled(id, enabled)
    await reg.reorder([id1, id2, ...])
    await reg.update_provider(id, **patch)

All mutating methods are async because Phase 2 will broadcast a
``providers_changed`` ws event; for Phase 1 we just keep the signature
shape so chat-handler can await without refactor later.

Migration: ``_migrate_legacy_provider_config(path)`` is a free function so
``main.py`` lifespan can call it before constructing the registry.
"""
from __future__ import annotations

import logging
import os
import re
import tomli
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("deskpet.llm.provider_registry")

# ───────────────────────── keyring (optional) ─────────────────────────
# Mirrors backend/llm/keys.py — CI environments without a keyring backend
# should still import this module; we just degrade keychain ops to no-ops
# in that case (and tests will inject a fake).
try:
    import keyring as _keyring_mod  # type: ignore[import-untyped]

    keyring = _keyring_mod
    _KEYRING_AVAILABLE = True
except Exception:  # pragma: no cover — depends on host env
    keyring = None  # type: ignore[assignment]
    _KEYRING_AVAILABLE = False


# ───────────────────────── constants ─────────────────────────

KEYCHAIN_SERVICE = "deskpet"
"""Service name shared with backend/llm/keys.py — single namespace under
the Windows Credential Manager / macOS Keychain."""

KEYCHAIN_ACCOUNT_PREFIX = "provider."
"""Per-provider keychain accounts: ``provider.<id>``."""

KEYCHAIN_REF_PREFIX = "deskpet.provider."
"""Public-facing ``api_key_ref`` shown in toml; just documentation."""

LEGACY_CLOUD_KEYCHAIN_REF = "deskpet.cloud_api_key"
"""Pre-P5-S2 single-provider keychain entry. Migration points the
auto-created provider here so existing users don't have to re-type."""

REDACTED_API_KEY = "********"
"""Sentinel value list_providers() returns instead of any real key."""

_KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_ID_LEN = 32


# ───────────────────────── exceptions ─────────────────────────


class NoProviderConfiguredError(RuntimeError):
    """get_chain() called but no provider is enabled.

    Callers (chat handler) translate this into an ``ErrorEvent`` with
    ``reason="no_provider_configured"`` so the frontend can pop the
    settings → providers panel.
    """

    def __init__(self, message: str = "no LLM provider configured") -> None:
        super().__init__(message)


class KeyMissingError(RuntimeError):
    """Managed provider exists but its keychain secret is missing."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(f"api key missing for provider {provider_id!r}")


# ───────────────────────── data ─────────────────────────


@dataclass
class ProviderEntry:
    """In-memory representation of one ``[[llm.endpoints]]`` row.

    ``api_key_ref`` is the keychain reference (string identifier shown
    in toml). The real key is fetched from keyring on demand via
    ``LLMProviderRegistry.resolve_api_key(provider_id)`` — never stored
    on the dataclass itself, so accidental ``asdict()`` / log dumps
    cannot leak it.

    P5-S2 v2: ``model`` (single str) → ``models`` (list of str) +
    ``default_model`` (one selected for chain calls). Legacy single-model
    rows are coerced on load (see ``_load_from_toml``).
    """

    id: str
    name: str
    base_url: str
    models: list[str]
    api_key_ref: str
    default_model: str | None = None
    priority: int = 1
    enabled: bool = True
    source: str = "user"
    account_ref: str = ""

    @property
    def model(self) -> str:
        """Back-compat accessor — returns the resolved default model.

        Falls back to first entry in ``models`` if ``default_model`` is
        unset, or empty string if the list is empty.
        """
        if self.default_model:
            return self.default_model
        return self.models[0] if self.models else ""

    @model.setter
    def model(self, value: str) -> None:
        """Back-compat setter — used by resolve_provider_for_session when
        applying a session-level ``preferred_model`` override. Writes to
        ``default_model`` (and adds to ``models`` if not already present
        so ``model`` getter stays consistent)."""
        v = str(value) if value else ""
        if not v:
            return
        self.default_model = v
        if v not in self.models:
            self.models = [*self.models, v]

    def to_toml_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        """Shape returned by ``list_providers()`` — api_key redacted.

        Includes both ``models`` (the array) and a derived ``model``
        scalar for any legacy UI/test code that still expects it.
        """
        d = asdict(self)
        d["api_key"] = REDACTED_API_KEY
        d["model"] = self.model  # derived, for back-compat
        return d


# ───────────────────────── validation ─────────────────────────


def _validate_provider_id(provider_id: str) -> None:
    if not isinstance(provider_id, str) or not provider_id:
        raise ValueError("invalid provider id: must be non-empty kebab-case string")
    if len(provider_id) > _MAX_ID_LEN:
        raise ValueError(
            f"invalid provider id: must be kebab-case, ≤{_MAX_ID_LEN} chars (got {len(provider_id)})"
        )
    if not _KEBAB_CASE_RE.match(provider_id):
        raise ValueError(
            f"invalid provider id {provider_id!r}: must be kebab-case "
            "(lowercase letters/digits separated by single dashes)"
        )


# ───────────────────────── toml writer ─────────────────────────


def _escape_toml_string(value: str) -> str:
    """Escape a string for basic TOML string literal use.

    We hand-write the providers section (no `tomli_w` dependency); inputs
    are user-supplied URLs / names so backslashes and quotes need escaping.
    Newlines and control chars are intentionally not allowed here — the
    provider model/base_url/name fields are not expected to contain them.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_providers_section(entries: list[ProviderEntry]) -> str:
    """Render `entries` as a TOML `[[llm.endpoints]]` block.

    Stable ordering by current list order — caller controls priority via
    the ``priority`` field, not text order, but we still emit them in
    list order so diffs stay readable.
    """
    out: list[str] = []
    for e in entries:
        out.append("[[llm.endpoints]]")
        out.append(f'id = "{_escape_toml_string(e.id)}"')
        out.append(f'name = "{_escape_toml_string(e.name)}"')
        out.append(f'base_url = "{_escape_toml_string(e.base_url)}"')
        # P5-S2 v2: models is the canonical array; emit empty list if unset
        # to keep schema explicit (loader expects array-of-string).
        models_payload = (
            "[" + ", ".join(f'"{_escape_toml_string(m)}"' for m in e.models) + "]"
        )
        out.append(f"models = {models_payload}")
        if e.default_model:
            out.append(f'default_model = "{_escape_toml_string(e.default_model)}"')
        out.append(f'api_key_ref = "{_escape_toml_string(e.api_key_ref)}"')
        out.append(f"priority = {int(e.priority)}")
        out.append(f"enabled = {'true' if e.enabled else 'false'}")
        if e.source != "user":
            out.append(f'source = "{_escape_toml_string(e.source)}"')
        if e.account_ref:
            out.append(f'account_ref = "{_escape_toml_string(e.account_ref)}"')
        out.append("")  # blank line between entries
    return "\n".join(out)


def _strip_existing_providers_block(text: str) -> str:
    """Remove any existing `[[llm.endpoints]]` array-of-tables blocks from
    the toml *text*, preserving everything else (including comments)."""
    lines = text.splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[[llm.endpoints]]"):
            in_block = True
            continue
        if in_block:
            # End of block on next [section] / [[section]] / EOF
            if stripped.startswith("[") and not stripped.startswith("[["):
                in_block = False
                out.append(line)
                continue
            if stripped.startswith("[[") and not stripped.startswith("[[llm.endpoints]]"):
                in_block = False
                out.append(line)
                continue
            # Still inside the block — skip its key=value / blank lines.
            continue
        out.append(line)
    # Trim trailing blank lines so we don't accumulate them across writes.
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` atomically: write tmp, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ───────────────────────── registry ─────────────────────────


class LLMProviderRegistry:
    """Owns the canonical list of LLM providers.

    Construct once per backend process. Pass ``config.toml`` path. The
    constructor loads existing ``[[llm.endpoints]]`` entries from disk;
    if absent, the registry starts empty.

    Concurrency: mutating methods (``add_provider``, ``remove_provider``,
    ``set_enabled``, ``reorder``, ``update_provider``) are async to leave
    room for a ws broadcast in Phase 2. Within a single backend process
    the ws handler serializes ``settings_providers_*`` messages, so we
    don't need an asyncio.Lock for the in-memory list mutations.
    """

    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        self._entries: list[ProviderEntry] = []
        self._load_from_toml()

    # ───────── persistence ─────────

    def _load_from_toml(self) -> None:
        if not self._config_path.exists():
            return
        try:
            with self._config_path.open("rb") as fh:
                data = tomli.load(fh)
        except (tomli.TOMLDecodeError, OSError) as exc:
            logger.warning("provider_registry: failed to load toml: %s", exc)
            return
        raw_list = (data.get("llm") or {}).get("endpoints") or []
        # Defensive: P4-S6 era config.toml uses `[llm.providers]` as a table
        # (dict of model-tier knobs). If user's toml predates this rename and
        # has a non-list under `[llm.endpoints]`, ignore it gracefully.
        if not isinstance(raw_list, list):
            raw_list = []
        self._entries = []
        for raw in raw_list:
            try:
                # P5-S2 v2: prefer `models` array; fall back to legacy `model`
                # scalar (single-model row pre-v2) and coerce.
                raw_models = raw.get("models")
                if isinstance(raw_models, list) and raw_models:
                    models = [str(m) for m in raw_models]
                else:
                    legacy_single = raw.get("model")
                    models = [str(legacy_single)] if legacy_single else []
                default_model_raw = raw.get("default_model")
                default_model = (
                    str(default_model_raw)
                    if default_model_raw
                    else (models[0] if models else None)
                )
                self._entries.append(
                    ProviderEntry(
                        id=str(raw["id"]),
                        name=str(raw.get("name", raw["id"])),
                        base_url=str(raw["base_url"]),
                        models=models,
                        default_model=default_model,
                        api_key_ref=str(raw.get("api_key_ref", f"{KEYCHAIN_REF_PREFIX}{raw['id']}")),
                        priority=int(raw.get("priority", 1)),
                        enabled=bool(raw.get("enabled", True)),
                        source=str(raw.get("source", "user")),
                        account_ref=str(raw.get("account_ref", "")),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("provider_registry: skipping malformed entry %r: %s", raw, exc)

    def _persist_to_toml(self) -> None:
        """Rewrite the providers section in `config.toml` atomically.

        Preserves all non-`[[llm.endpoints]]` content verbatim (comments,
        other sections). The new block is appended at the end of the file
        — toml's array-of-tables semantics don't care about position.
        """
        if not self._config_path.exists():
            # Brand-new file; just write the providers block + a header.
            content = "schema_version = 1\n\n" + _format_providers_section(self._entries)
            _atomic_write_text(self._config_path, content)
            return

        existing = self._config_path.read_text(encoding="utf-8")
        cleaned = _strip_existing_providers_block(existing)
        block = _format_providers_section(self._entries)
        if block:
            if not cleaned.endswith("\n"):
                cleaned += "\n"
            new_content = cleaned + "\n" + block
        else:
            new_content = cleaned
        _atomic_write_text(self._config_path, new_content)

    # ───────── keychain ─────────

    @staticmethod
    def _keychain_account(provider_id: str) -> str:
        return f"{KEYCHAIN_ACCOUNT_PREFIX}{provider_id}"

    @classmethod
    def _keychain_save(cls, provider_id: str, api_key: str) -> None:
        if not _KEYRING_AVAILABLE or keyring is None:
            logger.warning(
                "keyring backend unavailable; api_key for %s NOT persisted to OS credential store",
                provider_id,
            )
            return
        try:
            keyring.set_password(KEYCHAIN_SERVICE, cls._keychain_account(provider_id), api_key)
        except Exception as exc:  # pragma: no cover — backend-specific
            logger.error("keyring.set_password failed for %s: %s", provider_id, exc)
            raise

    @classmethod
    def _keychain_load(cls, provider_id: str) -> str | None:
        if not _KEYRING_AVAILABLE or keyring is None:
            return None
        try:
            return keyring.get_password(KEYCHAIN_SERVICE, cls._keychain_account(provider_id))
        except Exception as exc:  # pragma: no cover — backend-specific
            logger.warning("keyring.get_password failed for %s: %s", provider_id, exc)
            return None

    @classmethod
    def _keychain_delete(cls, provider_id: str) -> None:
        if not _KEYRING_AVAILABLE or keyring is None:
            return
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, cls._keychain_account(provider_id))
        except Exception as exc:  # pragma: no cover — backend-specific / entry already gone
            logger.debug("keyring.delete_password for %s: %s (ignored)", provider_id, exc)

    def resolve_api_key(self, provider_id: str) -> str | None:
        """Look up the real api_key for a provider. Returns None if not
        set or keychain is unavailable. Callers should treat None as a
        configuration error and surface to the user via settings UI."""
        return self._keychain_load(provider_id)

    # ───────── public mutators ─────────

    async def add_provider(self, fields: dict[str, Any]) -> ProviderEntry:
        """Register a new provider. Atomically:
          1. Validate id (kebab-case + unique)
          2. Write api_key to keychain
          3. Append entry to in-memory list
          4. Persist toml atomically

        Returns the inserted ProviderEntry (with api_key NOT included).
        """
        provider_id = fields.get("id", "")
        _validate_provider_id(provider_id)
        if any(e.id == provider_id for e in self._entries):
            raise ValueError(f"provider id {provider_id!r} already exists (must be unique)")

        # P5-S2 v2: accept either `models: list[str]` (preferred) or
        # legacy `model: str` (single model). At least one must be set.
        raw_models = fields.get("models")
        if isinstance(raw_models, list) and raw_models:
            models = [str(m).strip() for m in raw_models if str(m).strip()]
        elif fields.get("model"):
            models = [str(fields["model"]).strip()]
        else:
            models = []

        required = ("base_url",)
        missing = [k for k in required if not fields.get(k)]
        if missing or not models:
            missing_msg = list(missing) + (["models"] if not models else [])
            raise ValueError(f"missing required fields: {missing_msg}")

        api_key = fields.get("api_key")
        if not api_key:
            raise ValueError("api_key is required when adding a provider")

        default_model_raw = fields.get("default_model")
        default_model = (
            str(default_model_raw)
            if default_model_raw and str(default_model_raw) in models
            else models[0]
        )

        entry = ProviderEntry(
            id=provider_id,
            name=str(fields.get("name") or provider_id),
            base_url=str(fields["base_url"]),
            models=models,
            default_model=default_model,
            api_key_ref=f"{KEYCHAIN_REF_PREFIX}{provider_id}",
            priority=int(fields.get("priority", len(self._entries) + 1)),
            enabled=bool(fields.get("enabled", True)),
            source=str(fields.get("source", "user")),
            account_ref=str(fields.get("account_ref", "")),
        )

        self._keychain_save(provider_id, str(api_key))
        self._entries.append(entry)
        try:
            self._persist_to_toml()
        except Exception:
            # Rollback in-memory append + keychain entry on persist failure
            # so retries don't see a half-written state.
            self._entries.pop()
            self._keychain_delete(provider_id)
            raise

        logger.info("provider_registry: added %s (priority=%d)", provider_id, entry.priority)
        return entry

    async def remove_provider(self, provider_id: str) -> None:
        idx = self._find_index(provider_id)
        if idx is None:
            raise KeyError(f"provider {provider_id!r} not found")
        removed = self._entries.pop(idx)
        self._keychain_delete(provider_id)
        self._persist_to_toml()
        logger.info("provider_registry: removed %s", removed.id)

    async def set_enabled(self, provider_id: str, enabled: bool) -> None:
        idx = self._find_index(provider_id)
        if idx is None:
            raise KeyError(f"provider {provider_id!r} not found")
        self._entries[idx].enabled = bool(enabled)
        self._persist_to_toml()
        logger.info("provider_registry: set_enabled %s=%s", provider_id, enabled)

    async def reorder(self, ordered_ids: list[str]) -> None:
        """Reorder + reassign priorities so chain follows ``ordered_ids``.

        ``ordered_ids`` MUST contain every current provider id exactly once.
        Priority is rewritten 1..N to match the new order; insertion order
        of the internal list is also updated so equal-priority tie-break
        stays consistent.
        """
        current = {e.id: e for e in self._entries}
        if set(ordered_ids) != set(current.keys()):
            raise ValueError(
                f"reorder requires complete provider list; got {ordered_ids}, "
                f"have {list(current.keys())}"
            )
        new_list: list[ProviderEntry] = []
        for i, pid in enumerate(ordered_ids, start=1):
            entry = current[pid]
            entry.priority = i
            new_list.append(entry)
        self._entries = new_list
        self._persist_to_toml()
        logger.info("provider_registry: reordered %s", ordered_ids)

    async def update_provider(self, provider_id: str, **patch: Any) -> ProviderEntry:
        """Partial update. Only fields present in `patch` change.

        If ``api_key`` is in the patch and non-empty, the keychain entry
        is rewritten; otherwise keychain stays as-is. ``id`` cannot be
        renamed (would orphan keychain entry).
        """
        idx = self._find_index(provider_id)
        if idx is None:
            raise KeyError(f"provider {provider_id!r} not found")
        entry = self._entries[idx]

        if "id" in patch and patch["id"] != provider_id:
            raise ValueError("provider id is immutable (delete + re-add to rename)")

        api_key = patch.pop("api_key", None)
        if api_key:
            self._keychain_save(provider_id, str(api_key))

        for k, v in patch.items():
            if k == "priority":
                entry.priority = int(v)
            elif k == "enabled":
                entry.enabled = bool(v)
            elif k in ("name", "base_url"):
                setattr(entry, k, str(v))
            elif k == "models":
                # P5-S2 v2: replace the models list. Filter empties.
                if isinstance(v, list):
                    new_models = [str(m).strip() for m in v if str(m).strip()]
                    if new_models:
                        entry.models = new_models
                        # Keep default_model if still in the new list; else
                        # snap to the first entry.
                        if entry.default_model not in new_models:
                            entry.default_model = new_models[0]
            elif k == "model":
                # Legacy single-model update — coerce to a 1-element list.
                if v:
                    entry.models = [str(v).strip()]
                    entry.default_model = entry.models[0]
            elif k == "default_model":
                if v and str(v) in entry.models:
                    entry.default_model = str(v)
            elif k == "source":
                entry.source = str(v)
            elif k == "account_ref":
                entry.account_ref = str(v)
            elif k == "api_key_ref":
                entry.api_key_ref = str(v)
            # Silently ignore unknown keys — IPC layer validates first.

        self._persist_to_toml()
        return entry

    # ───────── public readers ─────────

    async def ensure_provider(self, fields: dict[str, Any]) -> ProviderEntry:
        """Idempotently add/update a managed provider row."""
        pid = fields.get("id", "")
        _validate_provider_id(pid)
        idx = self._find_index(pid)

        if idx is None:
            if self._entries:
                fields.setdefault("priority", min(e.priority for e in self._entries) - 1)
            else:
                fields.setdefault("priority", 1)
            entry = await self.add_provider(fields)
            self._normalize_priorities()
            return entry

        if not fields.get("api_key") and self.resolve_api_key(pid) is None:
            raise KeyMissingError(pid)

        patch = {
            k: fields[k]
            for k in (
                "name",
                "base_url",
                "models",
                "default_model",
                "source",
                "account_ref",
                "api_key",
            )
            if k in fields and fields[k] is not None
        }
        return await self.update_provider(pid, **patch)

    def list_providers(self) -> list[dict[str, Any]]:
        """Return all providers (enabled + disabled) with api_key redacted."""
        return [e.to_public_dict() for e in self._entries]

    def get_chain(self) -> list[dict[str, Any]]:
        """Return enabled providers ordered by (priority asc, insertion order).

        Raises ``NoProviderConfiguredError`` if no enabled provider exists
        — callers (chat handler) translate to an actionable error event.
        """
        enabled = [e for e in self._entries if e.enabled]
        if not enabled:
            raise NoProviderConfiguredError()
        # Use Python's stable sort: equal priorities preserve insertion order.
        enabled.sort(key=lambda e: e.priority)
        return [e.to_public_dict() for e in enabled]

    def get_entry(self, provider_id: str) -> ProviderEntry | None:
        """Internal helper for agent_loop / resolution code. Returns the
        raw dataclass (still no api_key inside) or None."""
        idx = self._find_index(provider_id)
        return self._entries[idx] if idx is not None else None

    # ───────── internal ─────────

    def get_account_ref(self, provider_id: str) -> str | None:
        entry = self.get_entry(provider_id)
        return entry.account_ref if entry else None

    def _normalize_priorities(self) -> None:
        for i, entry in enumerate(sorted(self._entries, key=lambda e: e.priority), start=1):
            entry.priority = i
        self._persist_to_toml()

    def _find_index(self, provider_id: str) -> int | None:
        for i, e in enumerate(self._entries):
            if e.id == provider_id:
                return i
        return None


# ───────────────────────── migration ─────────────────────────


def _migrate_legacy_provider_config(config_path: Path | str) -> bool:
    """One-time migration from `[llm.local]` single-provider schema to
    `[[llm.endpoints]]` list. Idempotent. Safe to call on every startup.

    Returns True if the file was modified, False if migration was a no-op
    (already migrated / nothing to migrate).

    Migration rules (matches design.md):
      - If `[[llm.endpoints]]` already present → no-op
      - If `[llm.local]` absent → no-op (fresh install)
      - Else: create one entry with id="legacy-default", api_key_ref
        pointing at the existing keychain slot (``deskpet.cloud_api_key``)
      - Write atomically; if keychain key missing, still create entry
        + emit warning so UI can prompt user to re-enter
    """
    path = Path(config_path)
    if not path.exists():
        return False

    try:
        with path.open("rb") as fh:
            data = tomli.load(fh)
    except (tomli.TOMLDecodeError, OSError) as exc:
        logger.error("migration: cannot parse %s: %s", path, exc)
        return False

    llm = data.get("llm") or {}
    # P4-S6 era `[llm.providers]` is a table (dict of budget knobs) — NOT
    # our endpoints list. Check the new `[llm.endpoints]` key instead so
    # we don't bail out incorrectly on a P4-S6-era config.
    endpoints = llm.get("endpoints")
    if isinstance(endpoints, list) and endpoints:
        return False  # already migrated

    legacy = llm.get("local")
    if not legacy:
        return False  # fresh install — nothing to migrate

    # Build the new entry.
    model = str(legacy.get("model", "default"))
    base_url = str(legacy.get("base_url", ""))
    if not base_url:
        logger.warning("migration: legacy [llm.local] missing base_url; skipping")
        return False

    entry = ProviderEntry(
        id="legacy-default",
        name=f"{model} (auto-migrated)",
        base_url=base_url,
        models=[model],
        default_model=model,
        api_key_ref=LEGACY_CLOUD_KEYCHAIN_REF,
        priority=1,
        enabled=True,
    )

    # Check keychain for the existing cloud key.
    existing_key = None
    if _KEYRING_AVAILABLE and keyring is not None:
        try:
            existing_key = keyring.get_password(KEYCHAIN_SERVICE, "cloud_api_key")
        except Exception as exc:  # pragma: no cover — backend-specific
            logger.warning("migration: keychain read failed: %s", exc)
            existing_key = None

    if not existing_key:
        logger.warning(
            "migration: api_key for legacy-default not found in keychain; "
            "please re-enter via Settings → LLM Providers"
        )

    # Append the providers block to the existing file content (preserves
    # comments + other sections).
    existing_text = path.read_text(encoding="utf-8")
    block = _format_providers_section([entry])
    sep = "" if existing_text.endswith("\n") else "\n"
    new_content = existing_text + sep + "\n" + block

    _atomic_write_text(path, new_content)
    logger.info("migrated_legacy_llm_local_to_providers id=%s", entry.id)
    return True


__all__ = [
    "LLMProviderRegistry",
    "ProviderEntry",
    "NoProviderConfiguredError",
    "KeyMissingError",
    "_migrate_legacy_provider_config",
]
