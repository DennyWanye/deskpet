"""API key resolution: env > keyring, with log-safe masking.

No fallback to config.toml — keys in config.toml would slip into commits
and into PyInstaller bundles. Enforced at the resolution layer so adapters
don't have to remember.

Key lookup order:
    1. os.environ[PROVIDER_API_KEY]        (e.g. ANTHROPIC_API_KEY)
    2. keyring.get_password("deskpet", f"{provider}_api_key")
    3. None → adapter.available() returns False → registry hides provider
"""
from __future__ import annotations

import os
from typing import Optional

# keyring is an optional dependency: CI environments don't ship a
# keyring backend, and import-time failures here would block all LLM
# adapters from loading. Treat missing keyring as "env-only resolution".
try:
    import keyring  # type: ignore[import-untyped]

    _KEYRING_AVAILABLE = True
except Exception:  # pragma: no cover - depends on host env
    keyring = None  # type: ignore[assignment]
    _KEYRING_AVAILABLE = False


PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def get_api_key(provider: str) -> Optional[str]:
    """Resolve an API key for the given provider, env-first.

    Normalizes provider name (lowercase) before lookup. Returns None if
    neither env var nor keyring entry exists, in which case the adapter
    MUST report available() == False and the registry MUST skip it.
    """
    key_name = provider.lower()
    env_var = PROVIDER_ENV_VARS.get(key_name)
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value

    if _KEYRING_AVAILABLE and keyring is not None:
        try:
            keyring_value = keyring.get_password("deskpet", f"{key_name}_api_key")
        except Exception:  # keyring backend can throw on locked session
            keyring_value = None
        if keyring_value:
            return keyring_value

    return None


def mask_key(key: Optional[str]) -> str:
    """Render an API key safe for logs: prefix + '****' + last 4 chars.

    Empty / very short keys get a single "****" placeholder — never emit
    the raw key length or any middle bytes (information leak).
    """
    if not key:
        return "****"
    # Preserve the sk- / AIza- prefix so ops can tell providers apart in logs.
    if len(key) <= 8:
        return "****"
    if key.startswith("sk-"):
        return f"sk-****{key[-4:]}"
    if key.startswith("AIza"):
        return f"AIza****{key[-4:]}"
    return f"****{key[-4:]}"
