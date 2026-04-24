"""P4-S5: tool-local config loader.

The tool package lives under ``deskpet.tools`` and is imported by the
agent loop before ``backend.config`` is necessarily ready (tests may
exercise individual tools without spinning up the full app). So instead
of depending on ``config.load_config`` (which pulls in billing,
provider, ASR config, etc.), this module does a minimal, cached TOML
read restricted to the knobs web/file/todo tools need.

Resolution order for the config file matches ``backend/config.py`` but
only covers the two paths a tool-only test needs:

  1. ``DESKPET_CONFIG`` env override (tests / CI).
  2. ``<repo>/config.toml`` (dev mode) — two levels up from this file.

Falls back to shipped defaults if neither is readable — web tools still
work, they just use hardcoded UA + default rate limit.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli

_DEFAULT_USER_AGENT = "DeskPet/0.6 (+https://github.com/DennyWanye/deskpet)"


@dataclass(frozen=True)
class WebToolsConfig:
    """Materialized [tools.web] section with defensive defaults."""

    user_agent: str = _DEFAULT_USER_AGENT
    respect_robots_txt: bool = True
    per_domain_max_concurrency: int = 2
    request_interval_ms: int = 500
    crawl_default_max_pages: int = 20
    crawl_default_max_depth: int = 2
    preferred_sources: list[dict[str, str]] = field(default_factory=list)


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("DESKPET_CONFIG")
    if override:
        paths.append(Path(override))
    # backend/deskpet/tools/_config.py → backend/deskpet/tools/ → backend/deskpet/
    # → backend/ → <repo>/. config.toml sits at <repo>/config.toml.
    here = Path(__file__).resolve()
    paths.append(here.parents[3] / "config.toml")
    return paths


_lock = threading.Lock()
_cached: WebToolsConfig | None = None
_cached_src: Path | None = None


def _load_raw() -> tuple[dict[str, Any], Path | None]:
    for p in _candidate_paths():
        try:
            if p.is_file():
                with p.open("rb") as f:
                    return tomli.load(f), p
        except OSError:
            continue
    return {}, None


def load_web_config(*, force: bool = False) -> WebToolsConfig:
    """Return the cached [tools.web] config, loading once lazily.

    Pass ``force=True`` to reload (used by tests that rewrite config.toml
    mid-suite).
    """
    global _cached, _cached_src
    with _lock:
        if _cached is not None and not force:
            return _cached
        raw, src = _load_raw()
        web = (raw.get("tools") or {}).get("web") or {}
        _cached = WebToolsConfig(
            user_agent=str(web.get("user_agent", _DEFAULT_USER_AGENT)),
            respect_robots_txt=bool(web.get("respect_robots_txt", True)),
            per_domain_max_concurrency=int(
                web.get("per_domain_max_concurrency", 2)
            ),
            request_interval_ms=int(web.get("request_interval_ms", 500)),
            crawl_default_max_pages=int(web.get("crawl_default_max_pages", 20)),
            crawl_default_max_depth=int(web.get("crawl_default_max_depth", 2)),
            preferred_sources=list(web.get("preferred_sources", []) or []),
        )
        _cached_src = src
        return _cached


def reset_cache() -> None:
    """Drop the cached config. Used by unit tests."""
    global _cached, _cached_src
    with _lock:
        _cached = None
        _cached_src = None
