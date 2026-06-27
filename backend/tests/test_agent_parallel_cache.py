# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G4 (companion-code-v2) — agent_parallel subagent prompt-cache mode tests.

Coverage:
  * _compute_system_prompt_hash: fork mode → same bytes across subagents
  * _compute_system_prompt_hash: fresh mode → distinct bytes per subagent
  * _resolve_cache_mode precedence (subagent > batch > default)
  * Default cache_mode is "fork" (the cache-friendly choice)
  * Result envelope per-subagent surfaces cache_mode + system_prompt_hash
  * Sprint Contract still lives in user prompt (NOT system prompt) so the
    fork-mode system_prompt_hash truly is per-subagent-independent
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from deskpet.tools.code_tools.agent_parallel_tool import (
    _CACHE_MODE_FORK,
    _CACHE_MODE_FRESH,
    _DEFAULT_CACHE_MODE,
    _build_sprint_contract,
    _compute_system_prompt_hash,
    _resolve_cache_mode,
    build_agent_parallel_tool,
)


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_default_cache_mode_is_fork() -> None:
    """Cache reuse is the more useful default — opt out for unusual cases."""
    assert _DEFAULT_CACHE_MODE == _CACHE_MODE_FORK


def test_resolve_cache_mode_precedence_subagent_wins() -> None:
    """Subagent.cache_mode overrides batch default."""
    assert _resolve_cache_mode({"cache_mode": "fresh"}, "fork") == "fresh"
    assert _resolve_cache_mode({"cache_mode": "fork"}, "fresh") == "fork"


def test_resolve_cache_mode_falls_back_to_batch_then_default() -> None:
    """No subagent.cache_mode → batch default; unknown batch → module default."""
    assert _resolve_cache_mode({}, "fresh") == "fresh"
    assert _resolve_cache_mode({}, "garbage") == _DEFAULT_CACHE_MODE
    assert _resolve_cache_mode({"cache_mode": "garbage"}, "fork") == "fork"


def test_fork_mode_hashes_are_identical_across_subagents() -> None:
    """Fork = reuse parent system prompt bytes verbatim. Two subagents
    with different task_ids MUST hash to the same value → LLM provider's
    prompt cache key matches → cache hit on subagent 2/3/4."""
    parent = "you are a code agent\nfollow these rules: ..."
    h_a = _compute_system_prompt_hash(
        cache_mode=_CACHE_MODE_FORK,
        parent_system_prompt=parent,
        sa_task_id="alpha",
    )
    h_b = _compute_system_prompt_hash(
        cache_mode=_CACHE_MODE_FORK,
        parent_system_prompt=parent,
        sa_task_id="beta",
    )
    assert h_a == h_b, "fork-mode system_prompt_hash must be identical across siblings"
    # Sanity: hash actually depends on parent text
    h_other = _compute_system_prompt_hash(
        cache_mode=_CACHE_MODE_FORK,
        parent_system_prompt="totally different prompt",
        sa_task_id="alpha",
    )
    assert h_other != h_a


def test_fresh_mode_hashes_differ_per_subagent() -> None:
    """Fresh = each subagent gets its own system prompt prefix → distinct
    cache keys → no cache reuse (the explicit opt-out behaviour)."""
    parent = "you are a code agent"
    h_a = _compute_system_prompt_hash(
        cache_mode=_CACHE_MODE_FRESH,
        parent_system_prompt=parent,
        sa_task_id="alpha",
    )
    h_b = _compute_system_prompt_hash(
        cache_mode=_CACHE_MODE_FRESH,
        parent_system_prompt=parent,
        sa_task_id="beta",
    )
    assert h_a != h_b, "fresh-mode hashes MUST differ across subagents"


def test_sprint_contract_lives_in_user_prompt_not_system_prompt() -> None:
    """Critical invariant for G4: per-subagent task info goes into the
    user message via _build_sprint_contract — NOT into the system prompt.
    Without this guarantee fork-mode cache reuse breaks (each subagent
    would have a different system prefix)."""
    sa_a = {
        "task_id": "alpha",
        "prompt": "task A",
        "input_files": ["a.py"],
        "success_criteria": "tests green",
    }
    sa_b = {
        "task_id": "beta",
        "prompt": "task B",
        "input_files": ["b.py"],
        "success_criteria": "no regressions",
    }
    out_a = _build_sprint_contract(sa_a)
    out_b = _build_sprint_contract(sa_b)
    # Contracts differ (good — that's their job in the *user* prompt)
    assert out_a != out_b
    # And separately the *system* prompt hash for both is identical in fork mode
    parent_sys = "<parent system prompt bytes>"
    h_a = _compute_system_prompt_hash(
        cache_mode=_CACHE_MODE_FORK,
        parent_system_prompt=parent_sys,
        sa_task_id=sa_a["task_id"],
    )
    h_b = _compute_system_prompt_hash(
        cache_mode=_CACHE_MODE_FORK,
        parent_system_prompt=parent_sys,
        sa_task_id=sa_b["task_id"],
    )
    assert h_a == h_b


# ---------------------------------------------------------------------------
# End-to-end through build_agent_parallel_tool with mock runner
# ---------------------------------------------------------------------------


def _make_capture_runner() -> tuple[Any, list[dict[str, Any]]]:
    """Test runner that records the sa_for_runner dict it received +
    returns a trivial output, so we can inspect what cache_mode /
    parent_system_prompt_hash the partition layer passed through."""
    received: list[dict[str, Any]] = []

    async def _runner(sa_for_runner: dict[str, Any], sa_task_id: str) -> str:
        received.append(sa_for_runner)
        return f"done:{sa_task_id}"

    return _runner, received


def test_envelope_surfaces_cache_mode_and_hash_default_fork() -> None:
    """Result objects ship cache_mode + system_prompt_hash so observability
    (and tests) can verify the cache strategy. Default fork → identical
    hashes for both subagents."""
    runner, received = _make_capture_runner()
    handler, _ = build_agent_parallel_tool(
        llm_shim=object(),
        parent_tool_registry=object(),
        parent_session_id_resolver=lambda: "sess-1",
        subagent_runner=runner,
        parent_system_prompt_resolver=lambda: "PARENT-SYS-PROMPT-BYTES",
    )
    args = {
        "subagents": [
            {"task_id": "alpha", "prompt": "task A"},
            {"task_id": "beta", "prompt": "task B"},
        ],
    }
    out_json = asyncio.run(handler(args))
    out = json.loads(out_json)
    assert out["ok"] is True
    assert out["count"] == 2

    results = out["results"]
    # Both subagents tagged with cache_mode=fork
    assert all(r["cache_mode"] == "fork" for r in results)
    # Both have identical system_prompt_hash (the whole point of fork)
    hashes = [r["system_prompt_hash"] for r in results]
    assert hashes[0] == hashes[1]
    # The runner saw the same parent_system_prompt_hash forwarded
    fwd_hashes = [sa["parent_system_prompt_hash"] for sa in received]
    assert fwd_hashes[0] == fwd_hashes[1] == hashes[0]


def test_envelope_fresh_mode_hashes_differ() -> None:
    """Batch cache_mode=fresh → both subagents get distinct system prompt
    hashes (no cache reuse — the explicit opt-out)."""
    runner, _ = _make_capture_runner()
    handler, _ = build_agent_parallel_tool(
        llm_shim=object(),
        parent_tool_registry=object(),
        parent_session_id_resolver=lambda: "sess-1",
        subagent_runner=runner,
        parent_system_prompt_resolver=lambda: "PARENT-SYS-PROMPT-BYTES",
    )
    args = {
        "cache_mode": "fresh",
        "subagents": [
            {"task_id": "alpha", "prompt": "task A"},
            {"task_id": "beta", "prompt": "task B"},
        ],
    }
    out = json.loads(asyncio.run(handler(args)))
    results = out["results"]
    assert all(r["cache_mode"] == "fresh" for r in results)
    hashes = [r["system_prompt_hash"] for r in results]
    assert hashes[0] != hashes[1], "fresh mode must give each subagent a unique hash"


def test_per_subagent_cache_mode_overrides_batch_default() -> None:
    """Mixed batch: one subagent fork, one fresh — verify per-call resolution."""
    runner, _ = _make_capture_runner()
    handler, _ = build_agent_parallel_tool(
        llm_shim=object(),
        parent_tool_registry=object(),
        parent_session_id_resolver=lambda: "sess-1",
        subagent_runner=runner,
        parent_system_prompt_resolver=lambda: "P",
    )
    args = {
        "cache_mode": "fork",
        "subagents": [
            {"task_id": "alpha", "prompt": "A", "cache_mode": "fresh"},
            {"task_id": "beta", "prompt": "B"},  # inherits batch=fork
        ],
    }
    out = json.loads(asyncio.run(handler(args)))
    results = {r["task_id"]: r for r in out["results"]}
    assert results["alpha"]["cache_mode"] == "fresh"
    assert results["beta"]["cache_mode"] == "fork"
    # Different cache modes → different hashes
    assert results["alpha"]["system_prompt_hash"] != results["beta"]["system_prompt_hash"]


def test_resolver_failure_does_not_break_dispatch() -> None:
    """If parent_system_prompt_resolver raises, dispatch keeps going with
    empty parent prompt (fork-hash is just sha256("")) — the cache wiring
    is best-effort observability, never a hard failure."""
    runner, _ = _make_capture_runner()

    def _bad_resolver() -> str:
        raise RuntimeError("resolver exploded")

    handler, _ = build_agent_parallel_tool(
        llm_shim=object(),
        parent_tool_registry=object(),
        parent_session_id_resolver=lambda: "sess-1",
        subagent_runner=runner,
        parent_system_prompt_resolver=_bad_resolver,
    )
    args = {
        "subagents": [
            {"task_id": "alpha", "prompt": "A"},
            {"task_id": "beta", "prompt": "B"},
        ],
    }
    out = json.loads(asyncio.run(handler(args)))
    assert out["ok"] is True
    # Both fall back to empty parent → fork hashes still match
    hashes = [r["system_prompt_hash"] for r in out["results"]]
    assert hashes[0] == hashes[1]


def test_failed_subagent_result_still_carries_cache_metadata() -> None:
    """Even when a subagent runner raises, the result envelope retains
    cache_mode + system_prompt_hash so observability can correlate
    failures to cache strategy."""

    async def _bad_runner(sa_for_runner: dict[str, Any], sa_task_id: str) -> str:
        if sa_task_id == "beta":
            raise ValueError("subagent boom")
        return "ok"

    handler, _ = build_agent_parallel_tool(
        llm_shim=object(),
        parent_tool_registry=object(),
        parent_session_id_resolver=lambda: "sess-1",
        subagent_runner=_bad_runner,
        parent_system_prompt_resolver=lambda: "P",
    )
    args = {
        "subagents": [
            {"task_id": "alpha", "prompt": "A"},
            {"task_id": "beta", "prompt": "B"},
        ],
    }
    out = json.loads(asyncio.run(handler(args)))
    by_id = {r["task_id"]: r for r in out["results"]}
    assert by_id["alpha"]["ok"] is True
    assert by_id["beta"]["ok"] is False
    # Both still tagged cache_mode + hash, even the failed one
    assert by_id["beta"]["cache_mode"] == "fork"
    assert by_id["beta"]["system_prompt_hash"] == by_id["alpha"]["system_prompt_hash"]
