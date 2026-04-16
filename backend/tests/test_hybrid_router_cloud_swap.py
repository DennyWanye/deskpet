"""HybridRouter hot-swap unit tests.

Covers:
- set_cloud_provider: replaces cloud, resets circuit breaker, disables cloud (None)
- set_strategy: switches routing, rejects unimplemented strategies
"""
from __future__ import annotations

import pytest

import router.hybrid_router as _hmod
from router.hybrid_router import (
    HybridRouter,
    LLMUnavailableError,
    RoutingStrategy,
    _ProviderState,
    _CircuitState,
)


class _FakeProvider:
    def __init__(
        self,
        *,
        health: bool = True,
        chat_chunks: list[str] | None = None,
        chat_raises: Exception | None = None,
        model: str = "fake-model",
    ) -> None:
        self._health = health
        self._chat_chunks = chat_chunks or []
        self._chat_raises = chat_raises
        self.model = model
        self.health_calls = 0
        self.chat_calls = 0

    async def health_check(self) -> bool:
        self.health_calls += 1
        return self._health

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        self.chat_calls += 1
        if self._chat_raises is not None:
            raise self._chat_raises
        for c in self._chat_chunks:
            yield c


async def _collect(stream) -> str:
    out = ""
    async for tok in stream:
        out += tok
    return out


# ---------------------------------------------------------------------------
# set_cloud_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_cloud_provider_replaces_cloud():
    """After set_cloud_provider, the next chat_stream should use the new provider."""
    old_cloud = _FakeProvider(health=True, chat_chunks=["old"])
    new_cloud = _FakeProvider(health=True, chat_chunks=["new"])
    router = HybridRouter(
        local=None,
        cloud=old_cloud,
        strategy=RoutingStrategy.LOCAL_FIRST,
    )

    router.set_cloud_provider(new_cloud)

    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "new"
    assert old_cloud.chat_calls == 0
    assert new_cloud.chat_calls == 1


@pytest.mark.asyncio
async def test_set_cloud_provider_resets_circuit_breaker():
    """An open circuit on the old provider must NOT be inherited by the new one."""
    old_cloud = _FakeProvider(health=True, chat_raises=RuntimeError("boom"))
    new_cloud = _FakeProvider(health=True, chat_chunks=["recovered"])
    router = HybridRouter(
        local=None,
        cloud=old_cloud,
        strategy=RoutingStrategy.LOCAL_FIRST,
    )

    # Drive old_cloud's circuit to OPEN (requires 3 failures).
    # We manipulate _cloud_state directly to avoid needing a local provider
    # to catch the LLMUnavailableError for each attempt.
    state = router._cloud_state
    for _ in range(3):
        state.record_chat_failure()
    assert state.circuit == _CircuitState.OPEN

    # Hot-swap: new provider should start with a fresh (CLOSED) state.
    router.set_cloud_provider(new_cloud)
    assert router._cloud_state.circuit == _CircuitState.CLOSED

    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "recovered"
    assert new_cloud.chat_calls == 1


@pytest.mark.asyncio
async def test_set_cloud_provider_none_disables_cloud():
    """set_cloud_provider(None) should put the router in local-only mode."""
    local = _FakeProvider(health=True, chat_chunks=["local-only"])
    cloud = _FakeProvider(health=True, chat_chunks=["cloud"])
    router = HybridRouter(
        local=local,
        cloud=cloud,
        strategy=RoutingStrategy.LOCAL_FIRST,
    )

    router.set_cloud_provider(None)

    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "local-only"
    assert cloud.chat_calls == 0
    assert local.chat_calls == 1


# ---------------------------------------------------------------------------
# set_strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_strategy_switches_routing():
    """Swapping from LOCAL_FIRST to CLOUD_FIRST should change which provider is tried first."""
    local = _FakeProvider(health=True, chat_chunks=["local"])
    cloud = _FakeProvider(health=True, chat_chunks=["cloud"])
    router = HybridRouter(
        local=local,
        cloud=cloud,
        strategy=RoutingStrategy.LOCAL_FIRST,
    )

    # Initially local_first — local is served.
    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "local"
    assert local.chat_calls == 1
    assert cloud.chat_calls == 0

    # Reset call counts for clarity.
    local.chat_calls = 0
    cloud.chat_calls = 0
    # Clear health cache so both are re-evaluated after strategy swap.
    router._local_state = _ProviderState()
    router._cloud_state = _ProviderState()

    router.set_strategy(RoutingStrategy.CLOUD_FIRST)

    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "cloud"
    assert cloud.chat_calls == 1
    assert local.chat_calls == 0


def test_set_strategy_rejects_unimplemented():
    """COST_AWARE and LATENCY_AWARE must raise NotImplementedError immediately."""
    router = HybridRouter(local=None, cloud=None)

    with pytest.raises(NotImplementedError, match="cost_aware"):
        router.set_strategy(RoutingStrategy.COST_AWARE)

    with pytest.raises(NotImplementedError, match="latency_aware"):
        router.set_strategy(RoutingStrategy.LATENCY_AWARE)
