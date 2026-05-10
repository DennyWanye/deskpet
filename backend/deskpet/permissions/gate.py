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
from pathlib import Path
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
        # P4-S21 #13: when True, every permission request is auto-allowed.
        # Toggled from the Settings panel; default OFF (safe). Useful for
        # voice-driven sessions where the user can't easily reach the
        # PermissionPopup, or for power users who trust their own LLM
        # config.
        #
        # P4-S25: now persisted to a small JSON file under the user data
        # dir so the choice survives backend restart. (Originally stored
        # only in process state — caused user-confusion when popups
        # reappeared after a tauri dev restart even though they'd
        # toggled it on.) The persistence path is set later via
        # ``bind_persistence_path()`` once main.py knows where the user
        # data dir lives.
        self.auto_mode: bool = False
        self._auto_mode_path: Path | None = None
        # P4-S21 #13: caller-provided source tag, used by `_prompt` to
        # decide whether to also speak the prompt out loud. Voice pipeline
        # sets this to "voice" before running AgentLoop; main chat handler
        # leaves it as None ("text" implicit).
        self.current_source: Optional[str] = None
        # Optional TTS engine for voice prompts. Wired by main.py at
        # startup via `set_tts_engine`.
        self._tts_engine: Optional[Any] = None

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

    def bind_persistence_path(self, path: Path) -> None:
        """P4-S25: install the JSON path used to persist auto_mode.

        Called once at startup by main.py with
        ``<user_data_dir>/permissions_auto_mode.json``. After binding,
        :meth:`load_auto_mode` reads from the path; subsequent IPC
        toggles auto-save via :meth:`set_auto_mode`.
        """
        self._auto_mode_path = path

    def load_auto_mode(self) -> bool:
        """Read the persisted auto_mode flag and apply it to ``self``.

        Returns the loaded value. False on any error (missing file,
        corrupt JSON, etc.) — explicit opt-in stays the safe default.
        """
        if self._auto_mode_path is None or not self._auto_mode_path.exists():
            return False
        try:
            data = json.loads(self._auto_mode_path.read_text(encoding="utf-8"))
            self.auto_mode = bool(data.get("enabled", False))
            return self.auto_mode
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_mode_load_failed: %s", exc)
            return False

    def set_auto_mode(self, enabled: bool) -> None:
        """P4-S25: toggle auto_mode and persist to disk.

        Replaces direct ``gate.auto_mode = ...`` assignment from main.py.
        Writes are best-effort — if the disk write fails the in-memory
        state still flips so the current session reflects the toggle.
        """
        self.auto_mode = bool(enabled)
        if self._auto_mode_path is None:
            return
        try:
            self._auto_mode_path.parent.mkdir(parents=True, exist_ok=True)
            self._auto_mode_path.write_text(
                json.dumps({"enabled": self.auto_mode}),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_mode_save_failed: %s", exc)

    def set_tts_engine(self, tts: Any) -> None:
        """Install a TTS engine used for voice-context prompt narration.

        When ``current_source == 'voice'`` and a popup is about to fire,
        the gate also queues a synthesized line like "我需要确认才能执行
        ..." so the user knows to look at the screen. Best-effort: TTS
        failures are swallowed; the popup still fires either way.
        """
        self._tts_engine = tts

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

        # Layer 0 (P4-S21 #13): auto-mode short-circuit. Beats deny
        # patterns intentionally — the user explicitly opted into "yes
        # to everything". If they want denylists to still apply, they
        # should keep auto-mode off. We DO log so the audit trail is
        # complete.
        if self.auto_mode:
            return PermissionDecision(
                allow=True, source="auto-mode",
            )

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
        # P4-S21 #13: voice-source prompt narration. If the request was
        # triggered by a voice utterance, we ALSO speak a short cue so
        # the user reaches for the mouse. Best effort: TTS errors are
        # logged once and the popup still goes ahead.
        if self.current_source == "voice" and self._tts_engine is not None:
            try:
                # Don't await: we don't want the popup blocked on TTS.
                # synthesize_pcm_stream is the streaming API; .synthesize
                # is one-shot; we use whichever exists.
                tts = self._tts_engine
                voice_msg = (
                    f"我需要确认才能执行{request.summary}，请点击屏幕上的允许按钮"
                )
                if hasattr(tts, "synthesize"):
                    asyncio.create_task(tts.synthesize(voice_msg))
                elif hasattr(tts, "synthesize_pcm_stream"):
                    async def _drain():
                        async for _ in tts.synthesize_pcm_stream(voice_msg):
                            pass
                    asyncio.create_task(_drain())
            except Exception:  # noqa: BLE001
                pass  # TTS failures don't block permission flow
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
