"""P4-S20 Stage C — official skill registry client.

Fetches `registry.json` from a configurable URL (default: a GitHub raw
URL pointing at the deskpet-org/skills-registry repo). Caches in memory
for ``cache_ttl_s`` seconds (default 1h) to avoid hammering GitHub.

Returns:
    {"skills": [<MarketplaceSkill>], "fetched_at": <iso>, "error": str?}

Failure (5xx, 4xx, network error) is surfaced as ``{"skills": [],
"error": "..."}`` so the UI can show a graceful empty state.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)


class RegistryClient:
    def __init__(
        self,
        url: str,
        *,
        cache_ttl_s: float = 3600.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.url = url
        self.cache_ttl_s = cache_ttl_s
        self._transport = transport
        self._cache: Optional[dict[str, Any]] = None
        self._cache_at: float = 0.0

    async def fetch(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if (
            not force
            and self._cache is not None
            and now - self._cache_at < self.cache_ttl_s
        ):
            return self._cache
        try:
            async with httpx.AsyncClient(
                timeout=20.0, transport=self._transport
            ) as client:
                r = await client.get(self.url)
            if r.status_code != 200:
                return {
                    "skills": [],
                    "error": f"registry returned status {r.status_code}",
                }
            payload = r.json()
            if not isinstance(payload, dict) or "skills" not in payload:
                return {
                    "skills": [],
                    "error": "registry payload missing 'skills' key",
                }
            self._cache = {
                "skills": list(payload.get("skills") or []),
                "fetched_at": now,
            }
            self._cache_at = now
            return self._cache
        except Exception as exc:  # noqa: BLE001
            logger.warning("registry_fetch_failed", error=str(exc))
            return {"skills": [], "error": f"{type(exc).__name__}: {exc}"}

    def invalidate(self) -> None:
        self._cache = None
        self._cache_at = 0.0
