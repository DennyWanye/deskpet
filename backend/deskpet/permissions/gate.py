"""P4-S20 PermissionGate — central choke-point for all sensitive tool ops.

Every tool call that has a non-trivial security category goes through
``await gate.check(category, params, session_id)`` before the handler
runs. The gate handles three layers of policy:

1. **Sensitive-path upgrade** — ``read_file`` against an obvious secret
   (``.ssh/id_rsa``, ``.env``, browser cookies) is auto-promoted to
   ``read_file_sensitive`` so the user always sees a popup.
2. **Config deny patterns** — ``[permissions.deny]`` patterns from
   ``config.toml`` reject matching ops *before* any user prompt and
   override session caches. This is fail-closed.
3. **User prompt** — the gate asks the frontend via injected responder
   callback; UI shows a 3-button modal ("Yes once" / "Yes always for
   session" / "No"). 60s timeout → auto-deny.

The responder is a pluggable ``async (PermissionRequest) -> PermissionResponse``
callable, so tests can inject deterministic responses without
spinning up a real WebSocket. Production wires it to the control WS
broadcaster in ``main.py``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, get_args

from ..types.skill_platform import (
    PermissionCategory,
    PermissionDecision,
    PermissionRequest,
    PermissionResponse,
)

logger = logging.getLogger(__name__)


# Pattern set used to upgrade an ostensibly read-only file read to
# ``read_file_sensitive``. Conservative — if in doubt, prompt.
_SENSITIVE_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:\.ssh[\\/]|\.aws[\\/]credentials|\.env(?:\.|$)|"
    r"id_rsa|id_ed25519|cookies\.sqlite|login\.keychain|"
    r"shadow|password)",
    re.IGNORECASE,
)


# Default-allow categories. Everything else falls through to "prompt".
_DEFAULT_ALLOW: set[str] = {"read_file"}


# Authoritative set of categories — derived from the Literal type so it
# stays in sync. `get_args` returns the strings.
_VALID_CATEGORIES: set[str] = set(get_args(PermissionCategory))


Responder = Callable[[PermissionRequest], Awaitable[PermissionResponse]]


@dataclass
class PermissionGateConfig:
    """Configuration knobs for the gate. Loaded from config.toml in prod."""

    timeout_s: float = 60.0
    shell_deny_patterns: list[str] = field(default_factory=list)
    write_deny_patterns: list[str] = field(default_factory=list)
    network_deny_patterns: list[str] = field(default_factory=list)


class PermissionGate:
    """Central permission gate. One instance per app process."""

    def __init__(self, config: Optional[PermissionGateConfig] = None) -> None:
        self.config = config or PermissionGateConfig()
        self._responder: Optional[Responder] = None
        # Session-scoped allow cache.
        # Key = (session_id, category, params_shape_hash)
        self._allow_cache: dict[tuple[str, str, str], bool] = {}

    # -----------------------------------------------------------------
    # Wiring
    # -----------------------------------------------------------------
    def set_responder(self, responder: Optional[Responder]) -> None:
        """Install (or remove) the IPC responder callable.

        In production this is wired to the control-WS broadcaster:
        the gate sends a ``permission_request`` JSON and awaits the
        matching ``permission_response`` from the frontend.
        """
        self._responder = responder

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------
    async def check(
        self,
        category: PermissionCategory | str,
        params: dict[str, Any],
        session_id: str,
    ) -> PermissionDecision:
        """Decide whether ``category(params)`` is allowed for this session.

        Always returns a ``PermissionDecision``; never raises (except for
        unknown category — that's a programmer bug, fail loud).
        """
        if category not in _VALID_CATEGORIES:
            raise ValueError(f"unknown permission category: {category!r}")

        # Layer 1: sensitive-path upgrade.
        category = self._maybe_upgrade(category, params)

        # Layer 2: config deny patterns. Always run — beats cache, beats prompt.
        denied = self._match_deny_pattern(category, params)
        if denied is not None:
            return PermissionDecision(
                allow=False, source="config-deny", pattern=denied
            )

        # Layer 3a: default-allow.
        if category in _DEFAULT_ALLOW:
            return PermissionDecision(allow=True, source="default-allow")

        # Layer 3b: session cache.
        cache_key = self._cache_key(session_id, category, params)
        cached = self._allow_cache.get(cache_key)
        if cached is True:
            return PermissionDecision(allow=True, source="cache-hit")

        # Layer 3c: prompt user via responder.
        return await self._prompt(category, params, session_id, cache_key)

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------
    def _maybe_upgrade(
        self, category: str, params: dict[str, Any]
    ) -> str:
        if category != "read_file":
            return category
        path = params.get("path", "")
        if isinstance(path, str) and _SENSITIVE_PATH_RE.search(path):
            return "read_file_sensitive"
        return category

    def _match_deny_pattern(
        self, category: str, params: dict[str, Any]
    ) -> Optional[str]:
        if category == "shell":
            cmd = params.get("command", "")
            for pat in self.config.shell_deny_patterns:
                if pat and pat in cmd:
                    return pat
        elif category in ("write_file", "desktop_write"):
            path = params.get("path", "")
            for pat in self.config.write_deny_patterns:
                if pat and pat in path:
                    return pat
        elif category == "network":
            url = params.get("url", "")
            for pat in self.config.network_deny_patterns:
                if pat and pat in url:
                    return pat
        return None

    @staticmethod
    def _cache_key(
        session_id: str, category: str, params: dict[str, Any]
    ) -> tuple[str, str, str]:
        # Shape hash uses the keyset, not the values, so similar ops
        # (same category + same param keys) all share one cache slot.
        # That matches the user's mental model: "always allow `shell`"
        # implies "always allow any shell command" within the session
        # (subject to deny patterns).
        keys = sorted(params.keys())
        h = hashlib.sha1(json.dumps(keys, ensure_ascii=False).encode()).hexdigest()
        return (session_id, category, h[:16])

    async def _prompt(
        self,
        category: str,
        params: dict[str, Any],
        session_id: str,
        cache_key: tuple[str, str, str],
    ) -> PermissionDecision:
        if self._responder is None:
            # No UI wired (tests or headless mode) → wait for timeout
            # then fail-closed. This matches the spec's auto-deny
            # behavior when the user is unreachable.
            try:
                await asyncio.wait_for(
                    asyncio.Event().wait(), timeout=self.config.timeout_s
                )
            except asyncio.TimeoutError:
                pass
            return PermissionDecision(allow=False, source="timeout")

        request = PermissionRequest(
            request_id=str(uuid.uuid4()),
            category=category,  # type: ignore[arg-type]
            summary=self._summarize(category, params),
            params=dict(params),
            default_action=self._default_action(category),
            dangerous=category in {"shell", "skill_install"},
            session_id=session_id,
        )
        try:
            response: PermissionResponse = await asyncio.wait_for(
                self._responder(request), timeout=self.config.timeout_s
            )
        except asyncio.TimeoutError:
            return PermissionDecision(
                allow=False, source="timeout", request_id=request.request_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("permission_responder_raised", exc_info=exc)
            return PermissionDecision(
                allow=False, source="user-denied", request_id=request.request_id
            )

        if response.decision == "allow":
            return PermissionDecision(
                allow=True, source="user-allowed", request_id=request.request_id
            )
        if response.decision == "allow_session":
            self._allow_cache[cache_key] = True
            return PermissionDecision(
                allow=True,
                source="user-allowed-session",
                request_id=request.request_id,
            )
        return PermissionDecision(
            allow=False, source="user-denied", request_id=request.request_id
        )

    @staticmethod
    def _summarize(category: str, params: dict[str, Any]) -> str:
        if category == "shell":
            return f"Run shell: {params.get('command','')[:80]}"
        if category in ("write_file", "desktop_write"):
            content = params.get("content", "")
            size = len(content) if isinstance(content, str) else 0
            return f"Write to {params.get('path','')} ({size} bytes)"
        if category == "read_file_sensitive":
            return f"Read sensitive file: {params.get('path','')}"
        if category == "network":
            return f"Fetch URL: {params.get('url','')}"
        if category == "mcp_call":
            return f"MCP call: {params.get('server','')}.{params.get('tool','')}"
        if category == "skill_install":
            return f"Install skill: {params.get('source','')}"
        return f"{category}({params})"

    @staticmethod
    def _default_action(category: str) -> str:
        if category in {"shell", "skill_install", "read_file_sensitive"}:
            return "deny"
        return "prompt"

    # -----------------------------------------------------------------
    # Test helpers
    # -----------------------------------------------------------------
    def clear_cache(self) -> None:
        """Reset the session allow cache. Used by tests + by `chat_reset`."""
        self._allow_cache.clear()
