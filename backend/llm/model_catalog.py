# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""code-session-model-params — live model catalog + per-model param caps.

The Cursor-style picker must not hardcode a model list. This module:

  1. ``fetch_models`` — pulls the relay's real catalog from the
     OpenAI-standard ``GET {base_url}/models`` (中转站). Total &
     defensive: any failure → ``[]`` so callers fall back to the
     registry's configured ``models`` list.
  2. ``model_param_caps`` — which picker controls a given model
     actually supports. Different families expose different knobs:
       - OpenAI/xAI (gpt-*, o1/o3/o4-*, grok-*): ``reasoning_effort``
         (Low…High) → ``effort`` control. No Anthropic-style thinking
         budget.
       - Anthropic (claude-*): extended *thinking* (budget), NO
         ``reasoning_effort`` → ``thinking`` control, ``effort`` hidden.
       - Gemini / DeepSeek-reasoner / GLM / Kimi-thinking / Qwen3 /
         Ernie-x1 / MiniMax-m2 …: reasoning-capable → ``thinking``.
       - small / non-reasoning (mini, nano, lite, image, embedding):
         minimal — only the context hint.
     This is a family heuristic (the relay's ``/models`` only returns
     ids, never capability metadata); it is intentionally easy to
     extend as new families appear.

``caps`` shape (all booleans — the picker shows a control iff True):
    {"thinking": bool, "fast": bool, "context": bool, "effort": bool}
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("deskpet.llm.model_catalog")


async def fetch_models(
    base_url: str,
    api_key: str | None,
    *,
    timeout: float = 8.0,
) -> list[str]:
    """GET {base_url}/models → list of model ids. ``[]`` on any failure.

    Never raises — the caller falls back to the registry's configured
    ``models`` list when this returns empty.
    """
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.info("model_catalog_fetch_non200 status=%s url=%s",
                        resp.status_code, url)
            return []
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — total: any failure → fallback
        logger.info("model_catalog_fetch_failed url=%s err=%s",
                    url, str(exc)[:160])
        return []
    # OpenAI standard: {"data": [{"id": "..."}, ...]}. Tolerate a bare
    # list or {"models": [...]} that some relays emit.
    rows: Any = (
        data.get("data")
        if isinstance(data, dict)
        else data
    )
    if rows is None and isinstance(data, dict):
        rows = data.get("models")
    out: list[str] = []
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, str):
                out.append(r)
            elif isinstance(r, dict):
                mid = r.get("id") or r.get("name")
                if isinstance(mid, str) and mid:
                    out.append(mid)
    return out


def _is(model: str, *needles: str) -> bool:
    return any(n in model for n in needles)


def model_param_caps(model_id: str) -> dict[str, bool]:
    """Return which picker controls a model supports. Family heuristic."""
    m = (model_id or "").lower().strip()

    # Non-chat / tiny — only the context hint is meaningful (and even
    # that is cosmetic). Hide reasoning knobs.
    if _is(m, "embedding", "text-embedding", "-image", "image-", "gpt-image",
            "whisper", "tts", "rerank"):
        return {"thinking": False, "fast": False,
                "context": False, "effort": False}
    if _is(m, "-mini", "-nano", "-lite", "-flash-lite", "4.1-mini"):
        return {"thinking": False, "fast": True,
                "context": True, "effort": False}

    # Anthropic — extended thinking (budget), no reasoning_effort.
    if _is(m, "claude-", "opus-", "sonnet-", "haiku-"):
        return {"thinking": True, "fast": False,
                "context": True, "effort": False}

    # OpenAI / xAI — reasoning_effort family.
    if _is(m, "gpt-5", "gpt-4", "o1-", "o1", "o3", "o4-",
            "grok-", "codex"):
        return {"thinking": True, "fast": True,
                "context": True, "effort": True}

    # Gemini — thinking config, no reasoning_effort.
    if _is(m, "gemini-"):
        return {"thinking": True, "fast": True,
                "context": True, "effort": False}

    # Other reasoning-capable open models.
    if _is(m, "deepseek-reasoner", "deepseek-v3.2", "deepseek-v4",
            "glm-4", "kimi", "qwen3", "ernie-x1", "ernie-5",
            "minimax-m2", "ring-", "ling-", "kat-", "step-3",
            "mimo", "llada", "doubao-seed-code"):
        return {"thinking": True, "fast": True,
                "context": True, "effort": False}

    # Unknown — conservative: allow thinking + context, hide effort.
    return {"thinking": True, "fast": True,
            "context": True, "effort": False}


# Real published context windows by family (tokens). NOT the picker's
# old fake "300K/1M" — these are the models' actual nominal windows.
# Project overrides (model_info BUILTIN / TOML) win over this; anything
# we genuinely don't know returns None → UI shows "由 provider 决定"
# instead of a made-up number. Extend as new families appear.
_FAMILY_CTX: tuple[tuple[tuple[str, ...], int], ...] = (
    (("gpt-4.1",), 1_000_000),
    (("gpt-5", "gpt-4", "o1", "o3", "o4-", "codex"), 400_000),
    (("grok-4", "grok-code"), 256_000),
    (("claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
      "claude-3", "opus-", "sonnet-", "haiku-"), 200_000),
    (("gemini-3", "gemini-2.5", "gemini-"), 1_000_000),
    (("deepseek-v4", "deepseek-reasoner", "deepseek-v3.2"), 1_000_000),
    (("deepseek-v3", "deepseek-chat"), 128_000),
    (("glm-4",), 200_000),
    (("qwen3",), 256_000),
    (("kimi-k2",), 256_000),
    (("minimax-m2", "ernie-x1", "ernie-5", "step-3"), 256_000),
)


def model_context_window(model_id: str) -> int | None:
    """Real nominal context window (tokens) for a model, or None when
    genuinely unknown. The project's own model_info (BUILTIN + user TOML
    overrides) is authoritative when it has a real entry; otherwise a
    family heuristic; otherwise None (caller shows "由 provider 决定")."""
    mid = (model_id or "").strip()
    if not mid:
        return None
    # Authoritative project source first — but only when it's a real
    # match, not the 32K "_default" fallback (which would be wrong for
    # most relay models).
    try:
        from llm import model_info as _mi
        if mid in _mi.BUILTIN:
            return _mi.resolve(mid).context_window
        ov = _mi._model_section(_mi.load_global_overrides(), mid)
        if ov and "context_window" in ov:
            return int(ov["context_window"])
    except Exception:  # noqa: BLE001 — never let model_info break the catalog
        pass
    m = mid.lower()
    for needles, win in _FAMILY_CTX:
        if any(n in m for n in needles):
            return win
    return None


def _label_for(model_id: str) -> str:
    """Human-ish label. Keep the id verbatim (users recognise it) but
    add a family tag so the long dropdown scans fast."""
    m = model_id.lower()
    if _is(m, "claude-", "opus-", "sonnet-", "haiku-"):
        tag = "Anthropic"
    elif _is(m, "gpt-", "o1", "o3", "o4-", "codex"):
        tag = "OpenAI"
    elif _is(m, "grok-"):
        tag = "xAI"
    elif _is(m, "gemini-"):
        tag = "Google"
    elif _is(m, "deepseek"):
        tag = "DeepSeek"
    elif _is(m, "glm-"):
        tag = "Zhipu"
    elif _is(m, "qwen"):
        tag = "Qwen"
    elif _is(m, "kimi"):
        tag = "Moonshot"
    else:
        tag = ""
    return f"{model_id}  ·  {tag}" if tag else model_id


def build_catalog(model_ids: list[str]) -> list[dict[str, Any]]:
    """``[{id,label,caps}]`` — de-duped, order preserved."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for mid in model_ids:
        if not isinstance(mid, str):
            continue
        mid = mid.strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({
            "id": mid,
            "label": _label_for(mid),
            "caps": model_param_caps(mid),
            "context_window": model_context_window(mid),
        })
    return out


__all__ = [
    "fetch_models",
    "model_param_caps",
    "model_context_window",
    "build_catalog",
]
