"""HybridRouter cloud_first 单元测试.

Mirrors local_first coverage:
- cloud healthy → cloud serves, local untouched
- cloud chat raises → fallback to local
- cloud health fails → fallback to local (no chat attempt)
- cloud budget denied → fallback to local, reason stashed for UI
- cloud budget denied + local unavailable → LLMUnavailableError(budget_reason=...)
- both providers dead → LLMUnavailableError (generic)
- cloud missing → fallback to local
"""
from __future__ import annotations

import pytest

from router.hybrid_router import (
    HybridRouter,
    LLMUnavailableError,
    RoutingStrategy,
)
from router.types import BudgetContext, BudgetDecision


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


@pytest.mark.asyncio
async def test_cloud_first_cloud_healthy_serves_cloud():
    local = _FakeProvider(health=True, chat_chunks=["should not be called"])
    cloud = _FakeProvider(health=True, chat_chunks=["cloud", " served"])
    router = HybridRouter(
        local=local, cloud=cloud, strategy=RoutingStrategy.CLOUD_FIRST
    )
    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "cloud served"
    assert cloud.chat_calls == 1
    assert local.chat_calls == 0


@pytest.mark.asyncio
async def test_cloud_first_cloud_chat_raises_retries_then_falls_back():
    """Cloud fails twice (retry exhausted) → fallback to local."""
    local = _FakeProvider(health=True, chat_chunks=["from", " local"])
    cloud = _FakeProvider(health=True, chat_raises=RuntimeError("cloud died mid-stream"))
    router = HybridRouter(
        local=local, cloud=cloud, strategy=RoutingStrategy.CLOUD_FIRST
    )
    # Patch retry delay to 0 for fast tests
    import router.hybrid_router as _mod
    orig = _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS
    _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS = 0.0
    try:
        text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    finally:
        _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS = orig
    assert text == "from local"
    assert cloud.chat_calls == 2  # tried twice
    assert local.chat_calls == 1


@pytest.mark.asyncio
async def test_cloud_first_cloud_unhealthy_retries_then_falls_back():
    """Cloud health fails twice → fallback to local."""
    local = _FakeProvider(health=True, chat_chunks=["local"])
    cloud = _FakeProvider(health=False, chat_chunks=["never"])
    router = HybridRouter(
        local=local, cloud=cloud, strategy=RoutingStrategy.CLOUD_FIRST
    )
    import router.hybrid_router as _mod
    orig = _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS
    _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS = 0.0
    try:
        text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    finally:
        _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS = orig
    assert text == "local"
    assert cloud.chat_calls == 0  # never reached chat (health failed both times)
    assert cloud.health_calls == 2
    assert local.chat_calls == 1


@pytest.mark.asyncio
async def test_cloud_first_budget_denied_falls_back_to_local():
    """Budget denial should NOT be fatal in cloud_first — local is free."""
    local = _FakeProvider(health=True, chat_chunks=["local"])
    cloud = _FakeProvider(health=True, chat_chunks=["should not be called"])

    async def deny_all(_: BudgetContext) -> BudgetDecision:
        return BudgetDecision(allow=False, reason="daily_budget_exceeded:5/5")

    router = HybridRouter(
        local=local,
        cloud=cloud,
        strategy=RoutingStrategy.CLOUD_FIRST,
        budget_hook=deny_all,
    )
    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "local"
    assert cloud.chat_calls == 0  # health_check never even happens
    assert local.chat_calls == 1


@pytest.mark.asyncio
async def test_cloud_first_budget_denied_and_local_dead_raises_with_reason():
    """Budget + local-dead: both routes gone. Reason must survive so the
    UI can still toast 'budget exceeded' — even though local was the
    actual failing step."""
    local = _FakeProvider(health=False)  # unhealthy
    cloud = _FakeProvider(health=True)

    async def deny_all(_: BudgetContext) -> BudgetDecision:
        return BudgetDecision(allow=False, reason="daily_budget_exceeded:5/5")

    router = HybridRouter(
        local=local,
        cloud=cloud,
        strategy=RoutingStrategy.CLOUD_FIRST,
        budget_hook=deny_all,
    )
    with pytest.raises(LLMUnavailableError) as ei:
        await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert ei.value.budget_reason == "daily_budget_exceeded:5/5"


@pytest.mark.asyncio
async def test_cloud_first_retry_succeeds_on_second_attempt():
    """First cloud attempt raises (503-like), retry succeeds → cloud serves."""

    class _FlakeyCloud:
        """Fails the first chat_stream call, succeeds on the second."""
        def __init__(self):
            self.model = "test-cloud"
            self.health_calls = 0
            self.chat_calls = 0

        async def health_check(self) -> bool:
            self.health_calls += 1
            return True

        async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
            self.chat_calls += 1
            if self.chat_calls == 1:
                raise RuntimeError("503 cold start")
            for c in ["cloud", " ok"]:
                yield c

    local = _FakeProvider(health=True, chat_chunks=["local"])
    cloud = _FlakeyCloud()
    router = HybridRouter(
        local=local, cloud=cloud, strategy=RoutingStrategy.CLOUD_FIRST
    )
    import router.hybrid_router as _mod
    orig = _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS
    _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS = 0.0
    try:
        text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    finally:
        _mod._CLOUD_FIRST_RETRY_DELAY_SECONDS = orig
    assert text == "cloud ok"
    assert cloud.chat_calls == 2
    assert local.chat_calls == 0


@pytest.mark.asyncio
async def test_cloud_first_both_dead_raises():
    local = _FakeProvider(health=False)
    cloud = _FakeProvider(health=False)
    router = HybridRouter(
        local=local, cloud=cloud, strategy=RoutingStrategy.CLOUD_FIRST
    )
    with pytest.raises(LLMUnavailableError):
        await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))


@pytest.mark.asyncio
async def test_cloud_first_no_cloud_configured_uses_local():
    local = _FakeProvider(health=True, chat_chunks=["local-only"])
    router = HybridRouter(
        local=local, cloud=None, strategy=RoutingStrategy.CLOUD_FIRST
    )
    text = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "local-only"
    assert local.chat_calls == 1
