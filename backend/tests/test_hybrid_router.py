"""HybridRouter 单元测试 (P2-1-S2).

Strategy: local_first
- 本地优先，本地 health/chat 失败时 fallback 到云端
- force_cloud=True 直接走云端（per-request 一次性）
- 都失败 / 云端未配置时抛 LLMUnavailableError
- circuit breaker: 单 provider 连续 3 次 chat 失败进入 OPEN，
  30s 后 HALF_OPEN，成功一次回 CLOSED
- health_check 结果有 30s TTL 缓存
"""
from __future__ import annotations

import pytest
import httpx

from providers.base import LLMProvider
from providers.openai_compatible import OpenAICompatibleProvider
from router.hybrid_router import (
    HybridRouter,
    LLMUnavailableError,
    _CircuitState,
    _ProviderState,
)


def test_hybrid_router_implements_llm_provider_protocol():
    router = HybridRouter(local=None, cloud=None)
    assert isinstance(router, LLMProvider)


def test_provider_state_starts_closed():
    s = _ProviderState()
    assert s.circuit == _CircuitState.CLOSED
    assert s.consecutive_failures == 0


def test_provider_state_opens_after_three_failures():
    s = _ProviderState()
    for _ in range(3):
        s.record_chat_failure()
    assert s.circuit == _CircuitState.OPEN


def test_provider_state_half_open_after_30s(monkeypatch):
    s = _ProviderState()
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    for _ in range(3):
        s.record_chat_failure()
    assert s.circuit == _CircuitState.OPEN
    fake_now[0] += 31.0  # > 30s
    assert s.circuit_state_now() == _CircuitState.HALF_OPEN


def test_provider_state_chat_success_closes_circuit(monkeypatch):
    s = _ProviderState()
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    for _ in range(3):
        s.record_chat_failure()
    fake_now[0] += 31.0
    s.record_chat_success()  # HALF_OPEN trial succeeded
    assert s.circuit == _CircuitState.CLOSED
    assert s.consecutive_failures == 0


def test_health_cache_returns_within_ttl(monkeypatch):
    s = _ProviderState()
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    s.cache_health(True)
    # 29s later — still cached
    fake_now[0] += 29.0
    assert s.cached_health() is True
    # 31s later — expired
    fake_now[0] += 2.0
    assert s.cached_health() is None


class _FakeProvider:
    """Minimal LLMProvider stub for router tests."""
    def __init__(self, *, health: bool = True, chat_chunks: list[str] | None = None,
                 chat_raises: Exception | None = None):
        self._health = health
        self._chat_chunks = chat_chunks or []
        self._chat_raises = chat_raises
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


@pytest.mark.asyncio
async def test_router_health_check_true_when_local_healthy():
    router = HybridRouter(local=_FakeProvider(health=True), cloud=None)
    assert await router.health_check() is True


@pytest.mark.asyncio
async def test_router_health_check_true_when_cloud_healthy_only():
    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=_FakeProvider(health=True),
    )
    assert await router.health_check() is True


@pytest.mark.asyncio
async def test_router_health_check_false_when_all_dead():
    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=_FakeProvider(health=False),
    )
    assert await router.health_check() is False


@pytest.mark.asyncio
async def test_router_health_check_false_when_no_providers():
    router = HybridRouter(local=None, cloud=None)
    assert await router.health_check() is False


@pytest.mark.asyncio
async def test_router_health_uses_cache_within_ttl(monkeypatch):
    """Two consecutive health_check calls within TTL → underlying provider hit once."""
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    local = _FakeProvider(health=True)
    router = HybridRouter(local=local, cloud=None)
    assert await router.health_check() is True
    assert await router.health_check() is True
    assert local.health_calls == 1  # cached on second call


# ---------------------------------------------------------------------------
# Task 5 — chat_stream routing logic
# ---------------------------------------------------------------------------

async def _collect(agen):
    return [x async for x in agen]


@pytest.mark.asyncio
async def test_chat_local_first_uses_local_when_healthy():
    local = _FakeProvider(health=True, chat_chunks=["hi", " local"])
    cloud = _FakeProvider(health=True, chat_chunks=["should not be called"])
    router = HybridRouter(local=local, cloud=cloud)
    out = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out == ["hi", " local"]
    assert cloud.chat_calls == 0


@pytest.mark.asyncio
async def test_chat_falls_back_to_cloud_when_local_unhealthy():
    local = _FakeProvider(health=False)
    cloud = _FakeProvider(health=True, chat_chunks=["from", " cloud"])
    router = HybridRouter(local=local, cloud=cloud)
    out = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out == ["from", " cloud"]
    assert local.chat_calls == 0


@pytest.mark.asyncio
async def test_chat_force_cloud_skips_local_entirely():
    local = _FakeProvider(health=True, chat_chunks=["local"])
    cloud = _FakeProvider(health=True, chat_chunks=["cloud"])
    router = HybridRouter(local=local, cloud=cloud)
    out = await _collect(router.chat_stream(
        [{"role": "user", "content": "x"}], force_cloud=True))
    assert out == ["cloud"]
    assert local.chat_calls == 0


@pytest.mark.asyncio
async def test_chat_force_cloud_raises_when_cloud_unconfigured():
    router = HybridRouter(local=_FakeProvider(health=True), cloud=None)
    with pytest.raises(LLMUnavailableError):
        await _collect(router.chat_stream(
            [{"role": "user", "content": "x"}], force_cloud=True))


@pytest.mark.asyncio
async def test_chat_raises_when_all_providers_dead():
    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=_FakeProvider(health=False),
    )
    with pytest.raises(LLMUnavailableError):
        await _collect(router.chat_stream([{"role": "user", "content": "x"}]))


@pytest.mark.asyncio
async def test_circuit_opens_after_three_chat_failures(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    local = _FakeProvider(health=True, chat_raises=RuntimeError("boom"))
    cloud = _FakeProvider(health=True, chat_chunks=["c"])
    router = HybridRouter(local=local, cloud=cloud)
    for _ in range(3):
        await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    # 4th call: local circuit OPEN, must skip local entirely
    await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert local.chat_calls == 3  # not incremented on 4th call


@pytest.mark.asyncio
async def test_circuit_recovers_on_half_open_success(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    # Local fails 3x then recovers
    local = _FlakeyProvider(fail_first_n=3, then_chunks=["recovered"])
    cloud = _FakeProvider(health=True, chat_chunks=["c"])
    router = HybridRouter(local=local, cloud=cloud)
    for _ in range(3):
        await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    # 30s later → HALF_OPEN
    fake_now[0] += 31.0
    out = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out == ["recovered"]
    # circuit closed again → next call also goes local
    out2 = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out2 == ["recovered"]
    assert cloud.chat_calls == 3  # all 3 OPEN-period calls fell back to cloud


# ---------------------------------------------------------------------------
# P2-1-S8 — BudgetHook gating on cloud calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_denies_cloud_when_local_unavailable_raises():
    """Local dead + budget denies cloud → LLMUnavailableError mentioning budget."""
    from router.types import BudgetContext, BudgetDecision

    async def deny_cloud(ctx: BudgetContext) -> BudgetDecision:
        if ctx.route == "cloud":
            return BudgetDecision(allow=False, reason="budget_exceeded_test")
        return BudgetDecision(allow=True)

    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=_FakeProvider(health=True, chat_chunks=["should-not-run"]),
        budget_hook=deny_cloud,
    )
    with pytest.raises(LLMUnavailableError) as ei:
        await _collect(
            router.chat_stream([{"role": "user", "content": "q"}])
        )
    assert "budget" in str(ei.value).lower()
    # last_budget_reason is set so main.py can surface it to the UI.
    assert router._last_budget_reason == "budget_exceeded_test"


@pytest.mark.asyncio
async def test_budget_allow_goes_through_to_cloud():
    from router.types import BudgetContext, BudgetDecision

    async def allow(ctx: BudgetContext) -> BudgetDecision:
        return BudgetDecision(allow=True)

    cloud = _FakeProvider(health=True, chat_chunks=["from", " cloud"])
    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=cloud,
        budget_hook=allow,
    )
    out = await _collect(
        router.chat_stream([{"role": "user", "content": "q"}])
    )
    assert out == ["from", " cloud"]


@pytest.mark.asyncio
async def test_budget_hook_not_called_for_local_only_path():
    """Healthy local stream must not invoke budget_hook at all."""
    from router.types import BudgetContext, BudgetDecision

    calls: list[BudgetContext] = []

    async def trace(ctx: BudgetContext) -> BudgetDecision:
        calls.append(ctx)
        return BudgetDecision(allow=True)

    router = HybridRouter(
        local=_FakeProvider(health=True, chat_chunks=["local"]),
        cloud=_FakeProvider(health=True, chat_chunks=["cloud"]),
        budget_hook=trace,
    )
    out = await _collect(
        router.chat_stream([{"role": "user", "content": "q"}])
    )
    assert out == ["local"]
    assert calls == []


@pytest.mark.asyncio
async def test_budget_denial_with_force_cloud_raises():
    from router.types import BudgetContext, BudgetDecision

    async def deny(ctx: BudgetContext) -> BudgetDecision:
        return BudgetDecision(allow=False, reason="denied")

    router = HybridRouter(
        local=_FakeProvider(health=True, chat_chunks=["local"]),
        cloud=_FakeProvider(health=True, chat_chunks=["cloud"]),
        budget_hook=deny,
    )
    with pytest.raises(LLMUnavailableError):
        await _collect(
            router.chat_stream(
                [{"role": "user", "content": "q"}], force_cloud=True
            )
        )


class _FlakeyProvider:
    """Fails first N chat calls, then yields then_chunks."""
    def __init__(self, *, fail_first_n: int, then_chunks: list[str]):
        self._fail = fail_first_n
        self._chunks = then_chunks
        self.chat_calls = 0

    async def health_check(self) -> bool:
        return True

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        self.chat_calls += 1
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("flakey boom")
        for c in self._chunks:
            yield c


# ---------------------------------------------------------------------------
# Task 7 — integration test with real OpenAICompatibleProvider + MockTransport
# ---------------------------------------------------------------------------

def _sse_done() -> bytes:
    return b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'


@pytest.mark.asyncio
async def test_router_with_real_providers_routes_to_local_when_healthy():
    """Both providers are real OpenAICompatibleProvider w/ MockTransport injected."""
    local_calls = {"chat": 0, "models": 0}
    cloud_calls = {"chat": 0, "models": 0}

    def local_handler(req):
        if req.url.path.endswith("/models"):
            local_calls["models"] += 1
            return httpx.Response(200, json={"data": [{"id": "gemma4:e4b"}]})
        local_calls["chat"] += 1
        return httpx.Response(200, content=_sse_done(),
                              headers={"content-type": "text/event-stream"})

    def cloud_handler(req):
        if req.url.path.endswith("/models"):
            cloud_calls["models"] += 1
            return httpx.Response(200, json={"data": [{"id": "qwen3.6-plus"}]})
        cloud_calls["chat"] += 1
        return httpx.Response(200, content=_sse_done(),
                              headers={"content-type": "text/event-stream"})

    local = OpenAICompatibleProvider(
        base_url="http://local.invalid/v1", api_key="ollama", model="gemma4:e4b")
    local._test_transport = httpx.MockTransport(local_handler)
    cloud = OpenAICompatibleProvider(
        base_url="http://cloud.invalid/v1", api_key="sk", model="qwen3.6-plus")
    cloud._test_transport = httpx.MockTransport(cloud_handler)

    router = HybridRouter(local=local, cloud=cloud)
    tokens = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))

    assert tokens == ["ok"]
    assert local_calls["chat"] == 1
    assert cloud_calls["chat"] == 0  # local healthy → cloud not touched
